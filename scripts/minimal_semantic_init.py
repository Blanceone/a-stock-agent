"""
minimal_semantic_init.py — 最小化语义初始化（无连接池，每次新建连接）
绕过 paramiko SSH 隧道的连接池冲突问题。
"""
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import psycopg2
import requests
from loguru import logger
from config.settings import settings

# ── ChromaDB 兼容层（直接 import）─────────────────────────────────────────
from src.infrastructure.database import _ChromaDBCompat, _CollectionCompat

# ── 摘要 Prompt ───────────────────────────────────────────────────────────
SUMMARIZE_PROMPT = """请对以下A股上市公司的主营业务描述做精简摘要。
要求：
- 仅保留核心业务、主要产品/服务、行业地位
- 去除财务数据、股价信息等冗余内容
- 输出不超过 200 字的纯文本
- 直接输出摘要文本，不要 JSON 格式

公司名称：{name}（{ts_code}）
业务描述：
{text}"""


def get_fresh_pg_conn():
    """每次新建一个 PG 连接（不使用连接池）"""
    return psycopg2.connect(settings.pg_dsn)


def init_chromadb():
    """初始化 ChromaDB 连接"""
    from chromadb.utils import embedding_functions
    base_url = f"http://{settings.chromadb_host}:{settings.chromadb_port}"

    # 验证连接
    resp = requests.get(f"{base_url}/api/v1/heartbeat", timeout=5)
    if not resp.ok:
        raise RuntimeError(f"ChromaDB 不可用: {resp.status_code}")

    client = _ChromaDBCompat(base_url)

    # 加载 embedding 模型
    model_path = settings.embedding_model_path
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model_path)
    logger.info("[Embedding] 模型已加载: {}", model_path)

    collection = client.get_or_create_collection(
        name=settings.chroma_collection_name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )
    logger.info("[ChromaDB] collection '{}' 就绪, count={}", settings.chroma_collection_name, collection.count())
    return collection


def get_all_stocks():
    """用新连接查询全部股票"""
    conn = get_fresh_pg_conn()
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


def get_existing_ts_codes(collection):
    """从 ChromaDB 查询已入库的 ts_code"""
    try:
        all_data = collection.get(include=["metadatas"])
        if all_data and all_data["metadatas"]:
            return {m.get("ts_code") for m in all_data["metadatas"] if m.get("ts_code")}
    except Exception as e:
        logger.warning("查询已有数据失败: {}", e)
    return set()


def summarize(name, ts_code, raw_text):
    """V4-Flash 摘要"""
    from src.nodes.llm_utils import call_llm
    prompt = SUMMARIZE_PROMPT.format(name=name, ts_code=ts_code, text=raw_text[:3000])
    result = call_llm(prompt, model="flash", system="你是精简摘要助手。", max_tokens=512)
    return result.strip()[:500]


def main():
    from src.infrastructure.data_fetcher import fetch_business_description

    logger.info("===== 最小化语义初始化开始 =====")

    # 1. 初始化 ChromaDB
    collection = init_chromadb()

    # 2. 获取已有数据
    existing = get_existing_ts_codes(collection)
    logger.info("ChromaDB 已有 {} 只股票", len(existing))

    # 3. 获取全A股列表（新建连接）
    logger.info("正在获取A股列表...")
    stocks = get_all_stocks()
    logger.info("共 {} 只上市股票", len(stocks))

    # 4. 过滤已入库的
    pending = [s for s in stocks if s["ts_code"] not in existing]
    logger.info("待处理 {} 只（跳过已入库的 {} 只）", len(pending), len(stocks) - len(existing))

    stats = {"total": len(pending), "success": 0, "failed": 0}
    batch_ids, batch_docs, batch_metas = [], [], []

    for idx, stock in enumerate(pending, 1):
        ts_code = stock["ts_code"]
        name = stock["name"]
        industry = stock.get("industry", "")

        try:
            raw_text = fetch_business_description(ts_code, name)
        except Exception as e:
            logger.warning("[{}/{}] 获取主营失败 {}: {}", idx, len(pending), ts_code, e)
            stats["failed"] += 1
            time.sleep(0.2)
            continue

        try:
            summary = summarize(name, ts_code, raw_text)
        except Exception as e:
            logger.warning("[{}/{}] 摘要失败 {}: {}", idx, len(pending), ts_code, e)
            stats["failed"] += 1
            time.sleep(0.2)
            continue

        batch_ids.append(ts_code)
        batch_docs.append(summary)
        batch_metas.append({"ts_code": ts_code, "name": name, "industry": industry or ""})
        stats["success"] += 1

        if len(batch_ids) >= 50:
            try:
                collection.add(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
                logger.info("批量写入 ChromaDB {} 只 (累计成功 {})", len(batch_ids), stats["success"])
            except Exception as e:
                logger.error("ChromaDB 写入失败: {}", e)
            batch_ids.clear()
            batch_docs.clear()
            batch_metas.clear()

        if idx % 100 == 0:
            logger.info("进度 {}/{} (成功 {} 失败 {})", idx, len(pending), stats["success"], stats["failed"])

        time.sleep(0.2)

    if batch_ids:
        try:
            collection.add(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
            logger.info("最终批量写入 {} 只", len(batch_ids))
        except Exception as e:
            logger.error("最终批次写入失败: {}", e)

    final_count = collection.count()
    logger.info("===== 完成 ===== 总计 {} | 成功 {} | 失败 {} | ChromaDB 总量 {}",
                stats["total"], stats["success"], stats["failed"], final_count)
    return stats


if __name__ == "__main__":
    main()
