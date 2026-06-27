"""
semantic_init.py — 全A股语义知识库初始化（Step 1.3）

流程：
  1. 从 PostgreSQL stock_basic 读取全部上市股票
  2. 逐只获取主营业务文本（四级降级链 fetch_business_description）
  3. 调用 V4-Flash 对原始文本做摘要（去除冗余信息）
  4. 将摘要文本向量化存入 ChromaDB collection: stock_business
     metadata: {ts_code, name, industry}

注意事项：
  - 跳过已存在于 ChromaDB 中的股票（增量更新）
  - 失败股票记录日志但不中断整体流程
  - 每 100 只输出一次进度日志
  - Tushare 限频：sleep(0.2) 防止超频
"""
from __future__ import annotations

import time

from loguru import logger

from config.settings import settings
from src.infrastructure import database
from src.infrastructure.database import get_pg_conn, release_pg_conn
from src.infrastructure.data_fetcher import fetch_business_description
from src.nodes.llm_utils import call_llm


# ── 摘要 Prompt ───────────────────────────────────────────────────────────────
SUMMARIZE_PROMPT = """请对以下A股上市公司的主营业务描述做精简摘要。
要求：
- 仅保留核心业务、主要产品/服务、行业地位
- 去除财务数据、股价信息等冗余内容
- 输出不超过 200 字的纯文本
- 直接输出摘要文本，不要 JSON 格式

公司名称：{name}（{ts_code}）
业务描述：
{text}"""


def _get_existing_ts_codes() -> set[str]:
    """从 ChromaDB 查询已入库的 ts_code 集合"""
    if database.chroma_collection is None:
        return set()
    try:
        all_data = database.chroma_collection.get(include=["metadatas"])
        if all_data and all_data["metadatas"]:
            return {m.get("ts_code") for m in all_data["metadatas"] if m.get("ts_code")}
    except Exception as e:
        logger.warning("[SemanticInit] 查询已有数据失败: {}", e)
    return set()


def _get_all_stocks() -> list[dict]:
    """从 PostgreSQL 读取全A股基础信息（使用直连避免连接池 SSH 隧道冲突）"""
    import psycopg2 as _pg2
    from config.settings import settings as _settings
    conn = _pg2.connect(_settings.pg_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ts_code, name, industry FROM stock_basic "
                "WHERE is_st = FALSE AND list_status = 'L'"
            )
            rows = cur.fetchall()
        return [{"ts_code": r[0], "name": r[1], "industry": r[2]} for r in rows]
    finally:
        conn.close()


def _summarize(name: str, ts_code: str, raw_text: str) -> str:
    """V4-Flash 摘要"""
    prompt = SUMMARIZE_PROMPT.format(name=name, ts_code=ts_code, text=raw_text[:3000])
    result = call_llm(prompt, model="flash", system="你是精简摘要助手。", max_tokens=512)
    return result.strip()[:500]


# ── 概念分类 Prompt ─────────────────────────────────────────────────────────────
import json as _json
from pathlib import Path as _Path

_CONCEPT_CLASSIFY_PROMPT_PATH = _Path(__file__).parent.parent.parent / "config" / "prompts" / "concept_classify.txt"


def _get_known_concepts() -> list[str]:
    """从 Redis dynamic:concepts 读取所有已有概念名称"""
    if database.redis_client is None:
        return []
    try:
        keys = database.redis_client.hkeys("dynamic:concepts")
        return [k.decode("utf-8") if isinstance(k, bytes) else k for k in keys]
    except Exception:
        return []


def _classify_concepts(name: str, ts_code: str, summary: str, concept_list: list[str]) -> tuple[list[str], float]:
    """V4-Flash 概念分类：从已有概念列表中选取匹配项，返回 (概念列表, 置信度)"""
    if not concept_list:
        return [], 0.0
    try:
        prompt_text = _CONCEPT_CLASSIFY_PROMPT_PATH.read_text(encoding="utf-8")
        prompt = prompt_text.format(
            concept_list="\n".join(f"- {c}" for c in concept_list[:200]),
            name=name, ts_code=ts_code, summary=summary[:300],
        )
        raw = call_llm(prompt, model="flash", system="你是A股概念分类助手。", max_tokens=256)
        data = _json.loads(raw.strip())
        result = data.get("concepts", [])
        confidence = float(data.get("confidence", 0.5))
        # 只保留确实在已知列表中的概念
        valid_set = set(concept_list)
        return [c for c in result if c in valid_set], confidence
    except Exception as e:
        logger.debug("[SemanticInit] 概念分类失败 {}/{}: {}", ts_code, name, e)
        return [], 0.0


def _persist_llm_concepts(redis_client, concepts: list[str], ts_code: str, name: str, confidence: float = 0.5):
    """将 LLM 分类结果写入 Redis dynamic:concepts（source=llm_classify）"""
    if not redis_client or not concepts:
        return
    from datetime import datetime
    now = datetime.now().isoformat()
    for concept in concepts:
        try:
            existing_raw = redis_client.hget("dynamic:concepts", concept)
            stocks_detail: dict[str, dict] = {}
            concept_sources_set: set[str] = set()
            if existing_raw:
                existing = _json.loads(existing_raw)
                stocks_detail = existing.get("stocks_detail", {})
                concept_sources_set = set(existing.get("sources", []))
                if not stocks_detail and "stocks" in existing:
                    for sc in existing["stocks"]:
                        stocks_detail[sc] = {"sources": ["llm"], "name": ""}

            if ts_code in stocks_detail:
                if "llm_classify" not in stocks_detail[ts_code].get("sources", []):
                    stocks_detail[ts_code]["sources"].append("llm_classify")
                stocks_detail[ts_code]["name"] = name
            else:
                stocks_detail[ts_code] = {"sources": ["llm_classify"], "name": name}

            concept_sources_set.add("llm_classify")
            stocks_list = list(stocks_detail.keys())

            redis_client.hset(
                "dynamic:concepts", concept,
                _json.dumps({
                    "stocks": stocks_list,
                    "stocks_detail": stocks_detail,
                    "sources": list(concept_sources_set),
                    "confidence": confidence,
                    "last_seen": now,
                }, ensure_ascii=False),
            )
            redis_client.expire("dynamic:concepts", 7 * 86400)
        except Exception as e:
            logger.debug("[SemanticInit] 概念持久化失败 {}: {}", concept, e)


def run() -> dict:
    """
    语义知识库初始化主流程。
    返回统计信息 dict。
    """
    if database.chroma_collection is None:
        raise RuntimeError("ChromaDB 未初始化，请先调用 init_all()")

    logger.info("[SemanticInit] ===== 开始全A股语义知识库初始化 =====")

    # 1. 获取已有数据（增量更新）
    existing = _get_existing_ts_codes()
    logger.info("[SemanticInit] ChromaDB 已有 {} 只股票", len(existing))

    # 2. 获取全A股列表
    stocks = _get_all_stocks()
    logger.info("[SemanticInit] PostgreSQL 中共 {} 只上市股票", len(stocks))

    # 3. 过滤已入库的
    pending = [s for s in stocks if s["ts_code"] not in existing]
    logger.info("[SemanticInit] 待处理 {} 只（跳过已入库的 {} 只）",
                len(pending), len(stocks) - len(pending))

    stats = {"total": len(pending), "success": 0, "failed": 0, "skipped": 0,
             "concept_classified": 0}
    batch_ids: list[str] = []
    batch_docs: list[str] = []
    batch_metas: list[dict] = []

    # 4. 读取已知概念列表（用于 LLM 分类）
    known_concepts = _get_known_concepts()
    logger.info("[SemanticInit] 已有 {} 个概念可用于分类", len(known_concepts))

    for idx, stock in enumerate(pending, 1):
        ts_code = stock["ts_code"]
        name = stock["name"]
        industry = stock.get("industry", "")

        # 获取主营业务文本
        try:
            raw_text = fetch_business_description(ts_code, name)
        except Exception as e:
            logger.warning("[SemanticInit] 获取主营失败 {}/{} {} : {}",
                           idx, len(pending), ts_code, e)
            stats["failed"] += 1
            time.sleep(0.2)
            continue

        # V4-Flash 摘要
        try:
            summary = _summarize(name, ts_code, raw_text)
        except Exception as e:
            logger.warning("[SemanticInit] 摘要失败 {}/{} {} : {}",
                           idx, len(pending), ts_code, e)
            stats["failed"] += 1
            time.sleep(0.2)
            continue

        # LLM 概念分类（与 tushare/akshare 取并集）
        if known_concepts:
            try:
                llm_concepts, confidence = _classify_concepts(name, ts_code, summary, known_concepts)
                if llm_concepts:
                    _persist_llm_concepts(database.redis_client, llm_concepts, ts_code, name, confidence)
                    stats["concept_classified"] += 1
                    logger.debug("[SemanticInit] {} {} 分类到: {} (confidence={:.2f})", ts_code, name, llm_concepts, confidence)
            except Exception as e:
                logger.debug("[SemanticInit] 概念分类异常 {}: {}", ts_code, e)

        # 批量累积
        batch_ids.append(ts_code)
        batch_docs.append(summary)
        batch_metas.append({"ts_code": ts_code, "name": name, "industry": industry or ""})

        stats["success"] += 1

        # 每 50 只写入一次 ChromaDB
        if len(batch_ids) >= 50:
            try:
                database.chroma_collection.add(
                    ids=batch_ids, documents=batch_docs, metadatas=batch_metas,
                )
                logger.info("[SemanticInit] 批量写入 ChromaDB {} 只", len(batch_ids))
            except Exception as e:
                logger.error("[SemanticInit] ChromaDB 写入失败: {}", e)
            batch_ids.clear()
            batch_docs.clear()
            batch_metas.clear()

        # 进度日志
        if idx % 100 == 0:
            logger.info("[SemanticInit] 进度 {}/{} (成功 {} 失败 {})",
                        idx, len(pending), stats["success"], stats["failed"])

        # Tushare 限频
        time.sleep(0.2)

    # 写入剩余
    if batch_ids:
        try:
            database.chroma_collection.add(
                ids=batch_ids, documents=batch_docs, metadatas=batch_metas,
            )
            logger.info("[SemanticInit] 最终批量写入 {} 只", len(batch_ids))
        except Exception as e:
            logger.error("[SemanticInit] 最终批次写入失败: {}", e)

    logger.info(
        "[SemanticInit] ===== 完成 ===== 总计 {} | 成功 {} | 失败 {} | 概念分类 {} | ChromaDB 总量 {}",
        stats["total"], stats["success"], stats["failed"],
        stats["concept_classified"], database.chroma_collection.count(),
    )
    return stats
