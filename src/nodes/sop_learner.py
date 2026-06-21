"""
sop_learner.py — SOP 自学习节点

职责：
  1. 从 sop_pending 表读取待学习的 SOP
  2. V4-Flash 结构化提取为操作图谱（JSON Schema）
  3. 人工审核闸门：写入 sop_active 表（approved=FALSE）
  4. 人工在 Web UI 批准后，图谱才真正生效

⚠️ 红线：sop_learner 只能写入 sop_pending → sop_active 中间表，
         不可直接修改 system_policy 图谱。
"""
from __future__ import annotations

import json
from datetime import datetime

from loguru import logger

from src.infrastructure.database import get_pg_conn, release_pg_conn
from src.nodes.llm_utils import call_llm_json, load_prompt


def _extract_sop_graph(policy_text: str) -> dict:
    """V4-Flash 结构化提取 SOP 操作图谱"""
    template = load_prompt("sop_extractor")
    prompt = template.format(policy_text=policy_text[:6000])
    return call_llm_json(prompt, model="flash", max_tokens=2048)


def _write_to_pending(sop_graph: dict, source_text: str) -> int:
    """将提取的 SOP 写入 sop_pending 表，返回 sop_id"""
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sop_pending (graph_json, source_text, created_at)
                   VALUES (%s, %s, %s)
                   RETURNING id""",
                (json.dumps(sop_graph, ensure_ascii=False),
                 source_text[:2000],
                 datetime.now()),
            )
            sop_id = cur.fetchone()[0]
            conn.commit()
            return sop_id
    finally:
        release_pg_conn(conn)


def _write_to_active(sop_id: int, sop_graph: dict) -> None:
    """将 SOP 写入 sop_active 表（待人工审核）"""
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sop_active (sop_name, policy_name, graph_json,
                                           approved, created_at)
                   VALUES (%s, %s, %s, FALSE, %s)""",
                (sop_graph.get("sop_name", "unnamed"),
                 sop_graph.get("policy_name", ""),
                 json.dumps(sop_graph, ensure_ascii=False),
                 datetime.now()),
            )
            conn.commit()
    finally:
        release_pg_conn(conn)


def run(state: dict) -> dict:
    """
    主流程：
      1. 查询 sop_pending 中未处理的记录
      2. 对每条记录提取 SOP 图谱
      3. 写入 sop_active（approved=FALSE，等待人工审核）
    """
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, graph_json, source_text, status
                   FROM sop_pending
                   WHERE status = 'pending'
                   ORDER BY created_at ASC
                   LIMIT 10"""
            )
            rows = cur.fetchall()
    finally:
        release_pg_conn(conn)

    if not rows:
        logger.info("[sop_learner] 无待学习 SOP")
        return {"sop_processed": 0}

    logger.info("[sop_learner] 待学习 SOP: {} 条", len(rows))
    processed = 0

    for row in rows:
        sop_id, _, source_text, _ = row
        try:
            sop_graph = _extract_sop_graph(source_text)
            _write_to_active(sop_id, sop_graph)

            # 标记为已处理
            conn = get_pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE sop_pending SET status = 'processed' WHERE id = %s",
                        (sop_id,),
                    )
                    conn.commit()
            finally:
                release_pg_conn(conn)

            processed += 1
            logger.info("[sop_learner] SOP #{} 已提取并写入 sop_active (待审核)", sop_id)
        except Exception as e:
            logger.error("[sop_learner] SOP #{} 处理失败: {}", sop_id, e)

    return {
        "sop_processed": processed,
        "error_node": None,
        "error_msg": None,
    }
