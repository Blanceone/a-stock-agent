"""
api.py — SOP 审核 Web API（FastAPI）

提供 SOP 战法的人工审核界面后端：
  - GET  /sop/pending     — 查看待审核 SOP 列表
  - GET  /sop/active      — 查看已审核 SOP 列表
  - POST /sop/approve/{id} — 审核通过某条 SOP
  - POST /sop/reject/{id}  — 拒绝某条 SOP
  - GET  /alerts/today     — 查看今日三共振预警

  - POST /api/run/{task}   — 触发后台任务 (init/semantic/static/dynamic)
  - GET  /api/tasks        — 查看任务状态

启动：uvicorn api:app --host 0.0.0.0 --port 8088
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
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

from src.infrastructure.database import get_pg_conn, release_pg_conn, redis_client, ensure_redis, init_all

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
    r = ensure_redis()
    if r is None:
        return {"count": 0, "items": [], "redis": False}
    today = datetime.now().strftime("%Y%m%d")
    key = f"dynamic:alerts:{today}"
    items = r.lrange(key, 0, -1)
    parsed = [json.loads(item) for item in items]
    return {"date": today, "count": len(parsed), "items": parsed, "redis": True}


# ── 简易审核页面 ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    """数据仪表盘主页"""
    dashboard_path = PROJECT_ROOT / "static" / "dashboard.html"
    if dashboard_path.exists():
        return dashboard_path.read_text(encoding="utf-8")
    # 降级：简单跳转提示
    return "<h1>请确保 static/dashboard.html 存在</h1>"


# ── 健康检查 ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ── 数据浏览 API ─────────────────────────────────────────────────────────────

@app.get("/api/stats")
def system_stats():
    """系统运行统计"""
    stats = {}
    # PostgreSQL
    try:
        conn = get_pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM stock_basic")
                stats["stock_total"] = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM stock_basic WHERE list_status='L' AND is_st=FALSE")
                stats["stock_active"] = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM sop_pending WHERE status='pending'")
                stats["sop_pending"] = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM sop_active")
                stats["sop_active"] = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM sop_active WHERE approved=TRUE")
                stats["sop_approved"] = cur.fetchone()[0]
        finally:
            release_pg_conn(conn)
    except Exception as e:
        stats["pg_error"] = str(e)
    # ChromaDB
    try:
        from src.infrastructure.database import chroma_collection
        stats["chroma_count"] = chroma_collection.count() if chroma_collection else 0
    except Exception:
        stats["chroma_count"] = 0
    # Redis
    try:
        r = ensure_redis()
        if r:
            today = datetime.now().strftime("%Y%m%d")
            stats["alerts_today"] = r.llen(f"dynamic:alerts:{today}") or 0
            stats["llm_cache"] = len(r.keys("llm:*"))
            stats["has_stock_pool"] = bool(r.exists("static:stock_pool"))
            stats["redis_ok"] = True
        else:
            stats["alerts_today"] = 0
            stats["llm_cache"] = 0
            stats["has_stock_pool"] = False
            stats["redis_ok"] = False
    except Exception:
        stats["redis_error"] = True
    return stats


@app.get("/api/stocks")
def list_stocks(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=10, le=200),
    search: str = Query("", description="按代码或名称搜索"),
    sort: str = Query("circ_mv", description="排序字段: circ_mv/name/ts_code"),
    order: str = Query("desc", description="asc/desc"),
):
    """股票列表（分页）"""
    conn = get_pg_conn()
    try:
        where_clause = "WHERE 1=1"
        params: list = []
        if search:
            where_clause += " AND (ts_code ILIKE %s OR name ILIKE %s)"
            params.extend([f"%{search}%", f"%{search}%"])
        order_dir = "DESC" if order == "desc" else "ASC"
        sort_col = sort if sort in ("circ_mv", "name", "ts_code", "industry") else "circ_mv"
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM stock_basic {where_clause}", params)
            total = cur.fetchone()[0]
            offset = (page - 1) * size
            cur.execute(
                f"SELECT ts_code, name, industry, circ_mv, is_st, list_status, updated_at "
                f"FROM stock_basic {where_clause} "
                f"ORDER BY {sort_col} {order_dir} NULLS LAST LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
        return {
            "total": total,
            "page": page,
            "size": size,
            "pages": (total + size - 1) // size,
            "items": [
                {
                    "ts_code": r[0], "name": r[1], "industry": r[2],
                    "circ_mv": float(r[3]) if r[3] else 0,
                    "is_st": r[4], "list_status": r[5],
                    "updated_at": r[6].isoformat() if r[6] else None,
                }
                for r in rows
            ],
        }
    finally:
        release_pg_conn(conn)


@app.get("/api/semantic")
def semantic_search(q: str = Query("", min_length=1), n: int = Query(10, ge=1, le=50)):
    """ChromaDB 语义搜索"""
    from src.infrastructure.database import chroma_collection
    if chroma_collection is None:
        return {"error": "ChromaDB 不可用", "items": []}
    try:
        result = chroma_collection.query(query_texts=[q], n_results=n)
        items = []
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        dists = result.get("distances", [[]])[0]
        for i in range(len(ids)):
            items.append({
                "id": ids[i],
                "document": docs[i][:500] if docs[i] else "",
                "metadata": metas[i] if metas[i] else {},
                "score": round(1 - dists[i], 4) if dists[i] is not None else 0,
            })
        return {"query": q, "count": len(items), "items": items}
    except Exception as e:
        return {"error": str(e), "items": []}


@app.get("/api/stockpool")
def stock_pool():
    """静态图谱股池"""
    r = ensure_redis()
    if r is None:
        return {"tier1": [], "tier2": [], "redis": False,
                "hint": "Redis 未连接，无法读取股池数据"}
    try:
        raw = r.get("static:stock_pool")
        if not raw:
            return {
                "tier1": [], "tier2": [], "redis": True,
                "hint": "股池数据不存在，请先运行「静态图谱构建」生成选股结果",
            }
        data = json.loads(raw)
        t1 = data.get("tier1", [])
        t2 = data.get("tier2", [])
        return {
            "tier1": t1, "tier2": t2, "redis": True,
            "total": len(t1) + len(t2),
        }
    except Exception as e:
        return {"tier1": [], "tier2": [], "redis": True, "error": str(e)}


@app.get("/api/news")
def news_feed(limit: int = Query(50, ge=10, le=200)):
    """最新消息面（多源聚合 + 分析结果）"""
    from src.infrastructure.database import REDIS_KEY_NEWS_FEED
    r = ensure_redis()
    if r is None:
        return {"count": 0, "items": [], "redis": False,
                "hint": "Redis 未连接，请确认 SSH 隧道已启动"}
    try:
        raw_items = r.lrange(REDIS_KEY_NEWS_FEED, 0, limit - 1)
        items = []
        for raw in raw_items:
            try:
                items.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                continue
        # 合并分析结果（从 Redis hash dynamic:news_analysis 读取）
        for item in items:
            aid = item.get("article_id", "")
            if aid:
                analysis_raw = r.hget("dynamic:news_analysis", aid)
                if analysis_raw:
                    try:
                        analysis = json.loads(analysis_raw)
                        item["analysis"] = analysis
                    except (json.JSONDecodeError, TypeError):
                        pass
        # 按 pub_time 倒序（最新排最前）
        items.sort(key=lambda x: x.get("pub_time", ""), reverse=True)
        feed_len = r.llen(REDIS_KEY_NEWS_FEED) or 0
        return {
            "count": len(items),
            "items": items,
            "redis": True,
            "feed_total": feed_len,
        }
    except Exception as e:
        return {"count": 0, "items": [], "redis": True, "error": str(e)}


# ── 后台任务管理 ────────────────────────────────────────────────────────────

_tasks: dict[str, dict] = {}  # task_id -> {name, status, started_at, log}

TASKS_CONFIG = {
    "init":     {"label": "初始化数据",   "cmd": [sys.executable, "main.py", "--mode", "init"]},
    "semantic": {"label": "语义初始化",   "cmd": [sys.executable, "main.py", "--mode", "semantic"]},
    "static":   {"label": "静态图谱构建", "cmd": [sys.executable, "main.py", "--mode", "static", "--pdf", "resources/policy.pdf"]},
    "dynamic":  {"label": "动态监控",     "cmd": [sys.executable, "main.py", "--mode", "dynamic"]},
}


def _run_task(task_id: str, task_name: str, cmd: list[str]):
    """在后台线程中运行子进程"""
    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["pid"] = None
    try:
        # Windows 下子进程默认用系统编码(cp936)，强制 UTF-8 避免乱码
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT), text=True, encoding="utf-8", errors="replace",
            env=env,
        )
        _tasks[task_id]["pid"] = proc.pid
        lines: list[str] = []
        for line in proc.stdout:
            lines.append(line.rstrip())
            if len(lines) > 500:
                lines.pop(0)
            _tasks[task_id]["log"] = "\n".join(lines[-100:])
        proc.wait()
        _tasks[task_id]["status"] = "done" if proc.returncode == 0 else "failed"
        _tasks[task_id]["returncode"] = proc.returncode
    except Exception as e:
        _tasks[task_id]["status"] = "failed"
        _tasks[task_id]["log"] = str(e)


@app.post("/api/run/{task_name}")
def run_task(task_name: str):
    """触发后台任务"""
    cfg = TASKS_CONFIG.get(task_name)
    if not cfg:
        raise HTTPException(400, f"未知任务: {task_name}，可选: {list(TASKS_CONFIG.keys())}")
    # 检查是否已有同类型任务在运行
    for tid, t in _tasks.items():
        if t["name"] == task_name and t["status"] == "running":
            return {"task_id": tid, "status": "already_running", "message": f"{cfg['label']} 正在运行中"}
    task_id = uuid.uuid4().hex[:8]
    _tasks[task_id] = {
        "name": task_name, "label": cfg["label"], "status": "pending",
        "started_at": datetime.now().isoformat(), "log": "", "pid": None,
    }
    thread = threading.Thread(target=_run_task, args=(task_id, task_name, cfg["cmd"]), daemon=True)
    thread.start()
    logger.info("[Task] 启动 {} (id={})", task_name, task_id)
    return {"task_id": task_id, "status": "started", "message": f"{cfg['label']} 已启动"}


@app.get("/api/tasks")
def list_tasks():
    """查看任务状态"""
    return {
        "tasks": [
            {
                "id": tid, "name": t["name"], "label": t["label"],
                "status": t["status"], "started_at": t["started_at"],
                "log": t.get("log", ""),
            }
            for tid, t in sorted(_tasks.items(), key=lambda x: x[1].get("started_at", ""), reverse=True)
        ]
    }

