"""
api.py — SOP 审核 Web API（FastAPI）

提供 SOP 战法的人工审核界面后端：
  - GET  /sop/pending     — 查看待审核 SOP 列表
  - GET  /sop/active      — 查看已审核 SOP 列表
  - POST /sop/approve/{id} — 审核通过某条 SOP
  - POST /sop/reject/{id}  — 拒绝某条 SOP
  - GET  /alerts/today     — 查看今日三共振预警

启动：uvicorn api:app --host 0.0.0.0 --port 8088
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from loguru import logger
from pydantic import BaseModel

# 项目根目录
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from src.infrastructure.database import get_pg_conn, release_pg_conn, redis_client, init_all

app = FastAPI(title="A股投研智能体 SOP 审核平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    """启动时初始化数据库连接"""
    init_all()


# ── SOP 审核接口 ──────────────────────────────────────────────────────────────

class ApproveRequest(BaseModel):
    approved_by: str = "admin"


@app.get("/sop/pending")
def list_pending(limit: int = Query(20, le=100)):
    """获取待审核 SOP 列表"""
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, graph_json, source_text, status, created_at
                   FROM sop_pending
                   WHERE status = 'pending'
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()
        return {
            "count": len(rows),
            "items": [
                {
                    "id": r[0],
                    "graph_json": r[1],
                    "source_text": r[2][:500] if r[2] else "",
                    "status": r[3],
                    "created_at": r[4].isoformat() if r[4] else None,
                }
                for r in rows
            ],
        }
    finally:
        release_pg_conn(conn)


@app.get("/sop/active")
def list_active(limit: int = Query(20, le=100)):
    """获取已审核 SOP 列表"""
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, sop_name, policy_name, graph_json, approved,
                          approved_by, approved_at
                   FROM sop_active
                   ORDER BY approved_at DESC
                   LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()
        return {
            "count": len(rows),
            "items": [
                {
                    "id": r[0],
                    "sop_name": r[1],
                    "policy_name": r[2],
                    "graph_json": r[3],
                    "approved": r[4],
                    "approved_by": r[5],
                    "approved_at": r[6].isoformat() if r[6] else None,
                }
                for r in rows
            ],
        }
    finally:
        release_pg_conn(conn)


@app.post("/sop/approve/{sop_id}")
def approve_sop(sop_id: int, req: ApproveRequest):
    """审核通过 SOP：更新 sop_active.approved = TRUE"""
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE sop_active
                   SET approved = TRUE, approved_by = %s, approved_at = NOW()
                   WHERE id = %s AND approved = FALSE""",
                (req.approved_by, sop_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, f"SOP #{sop_id} 不存在或已审核")
        conn.commit()
        logger.info("[SOP] 审核通过 #{}, 审核人: {}", sop_id, req.approved_by)
        return {"status": "approved", "id": sop_id}
    finally:
        release_pg_conn(conn)


@app.post("/sop/reject/{sop_id}")
def reject_sop(sop_id: int):
    """拒绝 SOP：从 sop_active 删除"""
    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sop_active WHERE id = %s AND approved = FALSE", (sop_id,))
            if cur.rowcount == 0:
                raise HTTPException(404, f"SOP #{sop_id} 不存在或已审核")
        conn.commit()
        logger.info("[SOP] 已拒绝 #{}", sop_id)
        return {"status": "rejected", "id": sop_id}
    finally:
        release_pg_conn(conn)


# ── 预警接口 ──────────────────────────────────────────────────────────────────

@app.get("/alerts/today")
def get_today_alerts():
    """获取今日三共振预警"""
    if redis_client is None:
        return {"count": 0, "items": []}
    today = datetime.now().strftime("%Y%m%d")
    key = f"dynamic:alerts:{today}"
    items = redis_client.lrange(key, 0, -1)
    parsed = [json.loads(item) for item in items]
    return {"date": today, "count": len(parsed), "items": parsed}


# ── 简易审核页面 ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    """简易审核主页"""
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>A股投研智能体 - SOP 审核平台</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
  h1 { color: #1a1a1a; border-bottom: 2px solid #e63946; padding-bottom: 10px; }
  .card { background: white; border-radius: 8px; padding: 16px; margin: 10px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
  .btn { padding: 6px 16px; border: none; border-radius: 4px; cursor: pointer; margin-right: 8px; }
  .btn-approve { background: #2a9d8f; color: white; }
  .btn-reject { background: #e63946; color: white; }
  .btn:hover { opacity: 0.85; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; }
  .badge-pending { background: #ffd166; }
  .badge-approved { background: #2a9d8f; color: white; }
  #alerts { margin-top: 30px; }
  .alert-item { border-left: 4px solid #e63946; padding: 10px; margin: 5px 0; background: #fff; }
  pre { background: #f8f9fa; padding: 10px; border-radius: 4px; overflow-x: auto; font-size: 13px; }
</style>
</head>
<body>
<h1>🔬 A股投研智能体 - SOP 审核平台</h1>
<h2>待审核 SOP</h2>
<div id="pending">加载中...</div>
<h2>🚨 今日三共振预警</h2>
<div id="alerts">加载中...</div>
<script>
async function loadPending() {
  const r = await fetch('/sop/pending');
  const d = await r.json();
  const el = document.getElementById('pending');
  if (d.count === 0) { el.innerHTML = '<p>暂无待审核 SOP</p>'; return; }
  el.innerHTML = d.items.map(i => `
    <div class="card">
      <span class="badge badge-pending">#${i.id}</span>
      <pre>${JSON.stringify(i.graph_json, null, 2)}</pre>
      <p style="color:#666;font-size:13px">${i.source_text}</p>
      <button class="btn btn-approve" onclick="approve(${i.id})">✓ 通过</button>
      <button class="btn btn-reject" onclick="reject(${i.id})">✗ 拒绝</button>
    </div>`).join('');
}
async function loadAlerts() {
  const r = await fetch('/alerts/today');
  const d = await r.json();
  const el = document.getElementById('alerts');
  if (d.count === 0) { el.innerHTML = '<p>今日无预警信号</p>'; return; }
  el.innerHTML = d.items.map(a => `
    <div class="alert-item">
      <b>${a.ts_code}</b> | 消息${a.news_score} 资金${a.capital_inflow_pct}% 量比${a.volume_ratio}
      <br><small>${a.news_title}</small>
    </div>`).join('');
}
async function approve(id) {
  await fetch(`/sop/approve/${id}`, {method:'POST',headers:{'Content-Type':'application/json'},body:'{"approved_by":"admin"}'});
  loadPending();
}
async function reject(id) {
  await fetch(`/sop/reject/${id}`, {method:'POST'});
  loadPending();
}
loadPending(); loadAlerts();
</script>
</body>
</html>"""


# ── 健康检查 ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}
