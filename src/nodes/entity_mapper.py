"""
entity_mapper.py — 步骤3：实体映射与去伪存真

输入 state:
  industry_chains: list[dict]

输出 state 增量:
  stock_pool: list[dict]
  # Schema: {ts_code, name, concept, layer, node, llm_score, circ_mv}
"""
from __future__ import annotations

import json

from loguru import logger

from config.settings import settings
from src.infrastructure.database import chroma_collection, get_pg_conn, release_pg_conn
from src.nodes.llm_utils import call_llm_json, load_prompt

# V4-Pro 打分每批最多 15 只
LLM_BATCH_SIZE = 15


def _chroma_recall(node_name: str, description: str, keywords: list[str]) -> list[dict]:
    """ChromaDB 语义召回 Top-K 候选股"""
    if chroma_collection is None:
        return []
    query = f"{node_name} {description} {' '.join(keywords)}"
    results = chroma_collection.query(
        query_texts=[query],
        n_results=settings.entity_chroma_top_k,
    )
    candidates: list[dict] = []
    if results and results["ids"] and results["ids"][0]:
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            doc = results["documents"][0][i] if results["documents"] else ""
            candidates.append({
                "ts_code": meta.get("ts_code", doc_id),
                "name": meta.get("name", ""),
                "business_text": doc,
                "distance": results["distances"][0][i] if results["distances"] else 1.0,
            })
    return candidates


def _llm_score(node_name: str, node_desc: str, candidates: list[dict]) -> list[dict]:
    """V4-Pro 精判相关性打分（分批，每批 ≤15 只）"""
    template = load_prompt("entity_mapper")
    scored: list[dict] = []

    for i in range(0, len(candidates), LLM_BATCH_SIZE):
        batch = candidates[i:i + LLM_BATCH_SIZE]
        cand_json = json.dumps(
            [{"ts_code": c["ts_code"], "name": c["name"],
              "business": c["business_text"][:300]}
             for c in batch],
            ensure_ascii=False,
        )
        prompt = template.format(
            node_name=node_name,
            node_description=node_desc,
            candidates_json=cand_json,
        )
        try:
            batch_scores = call_llm_json(prompt, model="pro", max_tokens=2048)
            for s in batch_scores:
                if s.get("score", 0) >= settings.entity_llm_score_threshold:
                    scored.append(s)
        except Exception as e:
            logger.warning("[entity_mapper] LLM打分批次失败 reason={}", e)

    return scored


def _sql_filter(ts_codes: list[str]) -> dict[str, dict]:
    """SQL 过滤：剔除 ST、退市、超大盘股"""
    if not ts_codes:
        return {}
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ts_code, name, circ_mv FROM stock_basic
                   WHERE ts_code = ANY(%s)
                     AND is_st = FALSE
                     AND list_status = 'L'
                     AND circ_mv < %s""",
                (ts_codes, settings.entity_max_circ_mv),
            )
            rows = cur.fetchall()
        return {r[0]: {"name": r[1], "circ_mv": float(r[2] or 0)} for r in rows}
    finally:
        release_pg_conn(conn)


def run(state: dict) -> dict:
    industry_chains = state.get("industry_chains", [])
    logger.info("[entity_mapper] 处理 {} 条产业链", len(industry_chains))

    stock_pool: list[dict] = []
    seen_codes: set[str] = set()

    for chain in industry_chains:
        concept = chain.get("concept", "")
        for layer in chain.get("layers", []):
            layer_name = layer.get("layer_name", "")
            for node in layer.get("nodes", []):
                node_name = node.get("node_name", "")
                node_desc = node.get("description", "")
                keywords = node.get("keywords", [])

                # 1. ChromaDB 召回
                candidates = _chroma_recall(node_name, node_desc, keywords)
                if not candidates:
                    continue

                # 2. LLM 打分
                scored = _llm_score(node_name, node_desc, candidates)

                # 3. SQL 过滤
                ts_codes = [s["ts_code"] for s in scored if s.get("ts_code")]
                valid = _sql_filter(ts_codes)

                for s in scored:
                    tc = s.get("ts_code", "")
                    if tc not in valid or tc in seen_codes:
                        continue
                    seen_codes.add(tc)
                    info = valid[tc]
                    stock_pool.append({
                        "ts_code": tc,
                        "name": info["name"],
                        "concept": concept,
                        "layer": layer_name,
                        "node": node_name,
                        "llm_score": s.get("score", 0),
                        "circ_mv": info["circ_mv"],
                    })

    logger.info("[entity_mapper] 候选股池: {} 只（去重后）", len(stock_pool))
    return {
        "stock_pool": stock_pool,
        "error_node": None,
        "error_msg": None,
    }
