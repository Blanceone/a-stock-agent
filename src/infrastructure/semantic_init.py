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
from src.infrastructure.database import chroma_collection, get_pg_conn, release_pg_conn
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
    if chroma_collection is None:
        return set()
    try:
        all_data = chroma_collection.get(include=["metadatas"])
        if all_data and all_data["metadatas"]:
            return {m.get("ts_code") for m in all_data["metadatas"] if m.get("ts_code")}
    except Exception as e:
        logger.warning("[SemanticInit] 查询已有数据失败: {}", e)
    return set()


def _get_all_stocks() -> list[dict]:
    """从 PostgreSQL 读取全A股基础信息"""
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ts_code, name, industry FROM stock_basic "
                "WHERE is_st = FALSE AND list_status = 'L'"
            )
            rows = cur.fetchall()
        return [{"ts_code": r[0], "name": r[1], "industry": r[2]} for r in rows]
    finally:
        release_pg_conn(conn)


def _summarize(name: str, ts_code: str, raw_text: str) -> str:
    """V4-Flash 摘要"""
    prompt = SUMMARIZE_PROMPT.format(name=name, ts_code=ts_code, text=raw_text[:3000])
    result = call_llm(prompt, model="flash", system="你是精简摘要助手。", max_tokens=512)
    return result.strip()[:500]


def run() -> dict:
    """
    语义知识库初始化主流程。
    返回统计信息 dict。
    """
    if chroma_collection is None:
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

    stats = {"total": len(pending), "success": 0, "failed": 0, "skipped": 0}
    batch_ids: list[str] = []
    batch_docs: list[str] = []
    batch_metas: list[dict] = []

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

        # 批量累积
        batch_ids.append(ts_code)
        batch_docs.append(summary)
        batch_metas.append({"ts_code": ts_code, "name": name, "industry": industry or ""})

        stats["success"] += 1

        # 每 50 只写入一次 ChromaDB
        if len(batch_ids) >= 50:
            try:
                chroma_collection.add(
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
            chroma_collection.add(
                ids=batch_ids, documents=batch_docs, metadatas=batch_metas,
            )
            logger.info("[SemanticInit] 最终批量写入 {} 只", len(batch_ids))
        except Exception as e:
            logger.error("[SemanticInit] 最终批次写入失败: {}", e)

    logger.info(
        "[SemanticInit] ===== 完成 ===== 总计 {} | 成功 {} | 失败 {} | ChromaDB 总量 {}",
        stats["total"], stats["success"], stats["failed"],
        chroma_collection.count(),
    )
    return stats
