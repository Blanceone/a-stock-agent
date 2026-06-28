"""
shutdown.py — 退出清理核心

full_shutdown() 按顺序执行：
  1. flush_redis_to_pg()       — Redis 缓存落盘到 PostgreSQL
  2. flush_redis_to_file()     — 剩余 Redis 数据落盘到 JSON 文件
  3. stop_background_tasks()   — 终止 API 后台任务
  4. close_database_connections() — 关闭所有 DB 连接
  5. kill_ssh_tunnels()        — 终止 SSH 隧道窗口
  6. kill_api_server()         — 释放 8088 端口
  7. kill_dynamic_monitor()    — 终止 main.py 进程
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "intermediate"

# Redis key 常量（与 database.py 保持一致）
REDIS_KEY_GRAPH_EDGES      = "concept_graph:edges"
REDIS_KEY_GRAPH_ROOTS      = "concept_graph:roots"
REDIS_KEY_GRAPH_LAYER_PFX  = "concept_graph:layer:"
REDIS_KEY_GRAPH_PROGRESS   = "concept_graph:build_progress"
REDIS_KEY_GRAPH_BUILD_LOCK = "concept_graph:build_lock"
REDIS_KEY_NEWS_FEED        = "dynamic:news_feed"


@dataclass
class StepResult:
    name: str
    success: bool
    message: str
    elapsed_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name, "success": self.success,
            "message": self.message, "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class ShutdownReport:
    steps: list[StepResult] = field(default_factory=list)
    all_ok: bool = True
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict:
        return {
            "steps": [
                {"name": s.name, "success": s.success,
                 "message": s.message, "elapsed_ms": s.elapsed_ms}
                for s in self.steps
            ],
            "all_ok": self.all_ok,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ── 辅助：安全获取 Redis 客户端 ────────────────────────────────────────────────

def _get_redis():
    """获取 Redis 客户端，始终新建带 socket_timeout 的连接避免挂起"""
    try:
        import redis as _redis
        r = _redis.from_url(
            "redis://localhost:6379/0",
            socket_connect_timeout=3,
            socket_timeout=5,
        )
        r.ping()
        return r
    except Exception:
        return None


# ── Step 1: Redis → PostgreSQL ─────────────────────────────────────────────────

def flush_redis_to_pg() -> StepResult:
    """将 Redis 中的动态数据落盘到 PostgreSQL"""
    t0 = time.time()
    written = 0
    errors = []
    r = _get_redis()
    if r is None:
        return StepResult("flush_redis_to_pg", True, "Redis 不可达，跳过", 0)

    try:
        # 设置关闭锁（带超时的 Redis 连接，不会挂起）
        try:
            r.set("system:shutdown_lock", "1", ex=30)
        except Exception:
            pass
        time.sleep(1)

        import psycopg2
        conn = psycopg2.connect(
            "postgresql://astock:astock@localhost:5432/astock",
            connect_timeout=5,
            options="-c statement_timeout=30000",
        )
        cur = conn.cursor()

        # 1) dynamic:concepts (Hash) → concept_stocks 表
        try:
            concept_count = 0
            cursor = 0
            while True:
                cursor, batch = r.hscan("dynamic:concepts", cursor=cursor, count=100)
                for term_bytes, raw in batch.items():
                    term = term_bytes if isinstance(term_bytes, str) else term_bytes.decode("utf-8")
                    try:
                        data = json.loads(raw)
                        stocks_detail = data.get("stocks_detail", {})
                        for ts_code, detail in stocks_detail.items():
                            sources = detail.get("sources", ["llm"])
                            score = detail.get("score", 0)
                            stock_name = detail.get("name", "")
                            cur.execute(
                                """INSERT INTO concept_stocks (concept, ts_code, sources, score, stock_name, updated_at)
                                   VALUES (%s, %s, %s, %s, %s, NOW())
                                   ON CONFLICT (concept, ts_code)
                                   DO UPDATE SET sources=EXCLUDED.sources, score=EXCLUDED.score,
                                                 stock_name=EXCLUDED.stock_name, updated_at=NOW()""",
                                (term, ts_code, json.dumps(sources), score, stock_name),
                            )
                            concept_count += 1
                    except (json.JSONDecodeError, TypeError):
                        continue
                if cursor == 0:
                    break
            conn.commit()
            written += concept_count
            logger.info("[Shutdown] concept_stocks 落盘 {} 条", concept_count)
        except Exception as e:
            conn.rollback()
            errors.append(f"concept_stocks: {e}")
            logger.error("[Shutdown] concept_stocks 落盘失败: {}", e)

        # 2) dynamic:news_analysis (Hash) → news_analysis 表（仅 analyzed）
        try:
            news_count = 0
            cursor = 0
            while True:
                cursor, batch = r.hscan("dynamic:news_analysis", cursor=cursor, count=100)
                for aid_bytes, raw in batch.items():
                    aid = aid_bytes if isinstance(aid_bytes, str) else aid_bytes.decode("utf-8")
                    try:
                        data = json.loads(raw)
                        if data.get("status") != "analyzed":
                            continue
                        cur.execute(
                            """INSERT INTO news_analysis
                               (article_id, source, title, pub_time, news_score,
                                impact_type, impact_concept, sentiment, reason,
                                related_ts_codes, new_concept_terms, mentioned_companies,
                                supply_chain_impact, updated_at)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                               ON CONFLICT (article_id)
                               DO UPDATE SET news_score=EXCLUDED.news_score,
                                             impact_type=EXCLUDED.impact_type,
                                             sentiment=EXCLUDED.sentiment,
                                             reason=EXCLUDED.reason,
                                             updated_at=NOW()""",
                            (
                                aid,
                                data.get("source", ""),
                                data.get("title", ""),
                                data.get("pub_time"),
                                data.get("news_score", 0),
                                data.get("impact_type", ""),
                                data.get("impact_concept", ""),
                                data.get("sentiment", "neutral"),
                                data.get("reason", ""),
                                json.dumps(data.get("related_ts_codes", [])),
                                json.dumps(data.get("new_concept_terms", [])),
                                json.dumps(data.get("mentioned_companies", [])),
                                json.dumps(data.get("supply_chain_impact", [])),
                            ),
                        )
                        news_count += 1
                    except (json.JSONDecodeError, TypeError):
                        continue
                if cursor == 0:
                    break
            conn.commit()
            written += news_count
            logger.info("[Shutdown] news_analysis 落盘 {} 条", news_count)
        except Exception as e:
            conn.rollback()
            errors.append(f"news_analysis: {e}")
            logger.error("[Shutdown] news_analysis 落盘失败: {}", e)

        cur.close()
        conn.close()

        # 释放关闭锁
        try:
            r.delete("system:shutdown_lock")
        except Exception:
            pass

        ms = int((time.time() - t0) * 1000)
        msg = f"已落盘 {written} 条" + (f"，{len(errors)} 项异常" if errors else "")
        return StepResult("flush_redis_to_pg", not errors, msg, ms)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return StepResult("flush_redis_to_pg", False, f"失败: {e}", ms)


# ── Step 2: Redis → JSON 文件 ──────────────────────────────────────────────────

def flush_redis_to_file() -> StepResult:
    """将 Redis 中的图谱/告警等数据落盘到 JSON 文件"""
    t0 = time.time()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    files_written = 0
    errors = []
    r = _get_redis()
    if r is None:
        return StepResult("flush_redis_to_file", True, "Redis 不可达，跳过", 0)

    try:
        # 1) concept_graph:edges → concept_graph_edges.json
        try:
            edges_raw = r.hgetall(REDIS_KEY_GRAPH_EDGES)
            if edges_raw:
                edges = {
                    (k if isinstance(k, str) else k.decode("utf-8")):
                    json.loads(v if isinstance(v, str) else v.decode("utf-8"))
                    for k, v in edges_raw.items()
                }
                _write_json("concept_graph_edges.json", edges)
                files_written += 1
        except Exception as e:
            errors.append(f"edges: {e}")

        # 2) concept_graph:roots → concept_graph_roots.json
        try:
            roots = r.smembers(REDIS_KEY_GRAPH_ROOTS)
            if roots:
                _write_json("concept_graph_roots.json", list(roots))
                files_written += 1
        except Exception as e:
            errors.append(f"roots: {e}")

        # 3) concept_graph:layer:* → concept_graph_layers.json
        try:
            layers: dict[str, list] = {}
            for key in r.scan_iter(match="concept_graph:layer:*", count=100):
                key_str = key if isinstance(key, str) else key.decode("utf-8")
                depth = key_str.split(":")[-1]
                members = r.smembers(key_str)
                if members:
                    layers[depth] = list(members)
            if layers:
                _write_json("concept_graph_layers.json", layers)
                files_written += 1
        except Exception as e:
            errors.append(f"layers: {e}")

        # 4) dynamic:alerts:* → alerts_{date}.json
        try:
            for key in r.scan_iter(match="dynamic:alerts:*", count=100):
                key_str = key if isinstance(key, str) else key.decode("utf-8")
                date_str = key_str.split(":")[-1]
                items = r.lrange(key_str, 0, -1)
                if items:
                    parsed = [json.loads(item) for item in items]
                    _write_json(f"alerts_{date_str}.json", parsed)
                    files_written += 1
        except Exception as e:
            errors.append(f"alerts: {e}")

        # 5) dynamic:news_feed → news_feed.json
        try:
            feed_items = r.lrange(REDIS_KEY_NEWS_FEED, 0, -1)
            if feed_items:
                parsed = [json.loads(item) for item in feed_items]
                _write_json("news_feed.json", parsed)
                files_written += 1
        except Exception as e:
            errors.append(f"news_feed: {e}")

        # 6) static:stock_pool → stock_pool.json
        try:
            pool_raw = r.get("static:stock_pool")
            if pool_raw:
                _write_json("stock_pool.json", json.loads(pool_raw))
                files_written += 1
        except Exception as e:
            errors.append(f"stock_pool: {e}")

        ms = int((time.time() - t0) * 1000)
        msg = f"写入 {files_written} 个文件到 {DATA_DIR}"
        if errors:
            msg += f"，{len(errors)} 项异常"
        return StepResult("flush_redis_to_file", not errors, msg, ms)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return StepResult("flush_redis_to_file", False, f"失败: {e}", ms)


def _write_json(filename: str, data):
    filepath = DATA_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("[Shutdown] 写入 {}", filepath)


# ── Step 3: 停止后台任务 ───────────────────────────────────────────────────────

def stop_background_tasks() -> StepResult:
    """终止通过 API 启动的后台子进程"""
    t0 = time.time()
    try:
        # 通过 API 发送关闭请求（不杀进程，仅停任务）
        import requests
        try:
            resp = requests.post(
                "http://localhost:8088/api/shutdown?flush=false&kill_processes=false",
                timeout=5,
            )
            if resp.ok:
                ms = int((time.time() - t0) * 1000)
                return StepResult("stop_background_tasks", True, "后台任务已停止", ms)
        except Exception:
            pass
        ms = int((time.time() - t0) * 1000)
        return StepResult("stop_background_tasks", True, "API 不可达，跳过", ms)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return StepResult("stop_background_tasks", False, f"失败: {e}", ms)


# ── Step 4: 关闭数据库连接 ─────────────────────────────────────────────────────

def close_database_connections() -> StepResult:
    """关闭所有数据库连接"""
    t0 = time.time()
    try:
        from src.infrastructure.database import close_all
        close_all()
        ms = int((time.time() - t0) * 1000)
        return StepResult("close_database_connections", True, "所有连接已关闭", ms)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return StepResult("close_database_connections", False, f"失败: {e}", ms)


# ── Step 5-7: 终止进程 ─────────────────────────────────────────────────────────

def kill_ssh_tunnels() -> StepResult:
    """终止 SSH 隧道窗口"""
    t0 = time.time()
    try:
        if sys.platform == "win32":
            subprocess.run(
                'taskkill /FI "WINDOWTITLE eq SSH-Tunnel*" /F',
                shell=True, capture_output=True, timeout=5,
            )
        else:
            subprocess.run(
                "pkill -f 'ssh -N -L' 2>/dev/null || true",
                shell=True, capture_output=True, timeout=5,
            )
        ms = int((time.time() - t0) * 1000)
        return StepResult("kill_ssh_tunnels", True, "SSH 隧道已终止", ms)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return StepResult("kill_ssh_tunnels", False, f"失败: {e}", ms)


def kill_api_server() -> StepResult:
    """终止 API Server 窗口（释放 8088 端口）"""
    t0 = time.time()
    try:
        if sys.platform == "win32":
            subprocess.run(
                'taskkill /FI "WINDOWTITLE eq API-Server*" /F',
                shell=True, capture_output=True, timeout=5,
            )
        ms = int((time.time() - t0) * 1000)
        return StepResult("kill_api_server", True, "API Server 已终止", ms)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return StepResult("kill_api_server", False, f"失败: {e}", ms)


def kill_dynamic_monitor() -> StepResult:
    """终止 main.py 动态监控进程"""
    t0 = time.time()
    killed = 0
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                'tasklist /fi "imagename eq python.exe" /fo list',
                shell=True, text=True, encoding="gbk", errors="replace",
            )
            pids = []
            for line in out.split("\n"):
                if line.startswith("PID:"):
                    pid = line.split(":")[1].strip()
                    pids.append(pid)
            for pid in pids:
                try:
                    cmd_out = subprocess.check_output(
                        f'wmic process where "ProcessId={pid}" get CommandLine',
                        shell=True, text=True, encoding="gbk", errors="replace",
                        timeout=3,
                    )
                    if "main.py" in cmd_out:
                        subprocess.run(
                            f"taskkill /PID {pid} /F",
                            shell=True, capture_output=True, timeout=3,
                        )
                        killed += 1
                except Exception:
                    continue
        ms = int((time.time() - t0) * 1000)
        return StepResult("kill_dynamic_monitor", True, f"已终止 {killed} 个 main.py 进程", ms)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return StepResult("kill_dynamic_monitor", False, f"失败: {e}", ms)


# ── 验证端口释放 ───────────────────────────────────────────────────────────────

def _verify_ports_released() -> dict[str, bool]:
    """验证关键端口已释放"""
    import socket
    result = {}
    for port in [8088, 5432, 6379]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("0.0.0.0", port))
            s.close()
            result[port] = True  # 端口空闲 = 已释放
        except OSError:
            result[port] = False  # 仍被占用
    return result


# ── 主入口 ─────────────────────────────────────────────────────────────────────

def full_shutdown(include_process_kill: bool = True) -> ShutdownReport:
    """
    按顺序执行完整退出流程。

    include_process_kill=False 时仅做数据落盘 + 关闭连接，不终止外部进程。
    """
    report = ShutdownReport(started_at=datetime.now().isoformat())
    logger.info("[Shutdown] === 开始安全退出 ===")

    # Step 1: Redis → PG
    step = flush_redis_to_pg()
    report.steps.append(step)
    logger.info("[Shutdown] {} — {} ({}ms)", step.name, step.message, step.elapsed_ms)

    # Step 2: Redis → JSON
    step = flush_redis_to_file()
    report.steps.append(step)
    logger.info("[Shutdown] {} — {} ({}ms)", step.name, step.message, step.elapsed_ms)

    # Step 3: 停止后台任务
    step = stop_background_tasks()
    report.steps.append(step)
    logger.info("[Shutdown] {} — {} ({}ms)", step.name, step.message, step.elapsed_ms)

    # Step 4: 关闭 DB 连接
    step = close_database_connections()
    report.steps.append(step)
    logger.info("[Shutdown] {} — {} ({}ms)", step.name, step.message, step.elapsed_ms)

    if include_process_kill:
        # Step 5: 终止 SSH 隧道
        step = kill_ssh_tunnels()
        report.steps.append(step)
        logger.info("[Shutdown] {} — {} ({}ms)", step.name, step.message, step.elapsed_ms)

        # Step 6: 终止 API Server
        step = kill_api_server()
        report.steps.append(step)
        logger.info("[Shutdown] {} — {} ({}ms)", step.name, step.message, step.elapsed_ms)

        # Step 7: 终止动态监控
        step = kill_dynamic_monitor()
        report.steps.append(step)
        logger.info("[Shutdown] {} — {} ({}ms)", step.name, step.message, step.elapsed_ms)

        # 短暂等待后验证端口
        time.sleep(2)
        port_status = _verify_ports_released()
        for port, released in port_status.items():
            if not released:
                logger.warning("[Shutdown] 端口 {} 未释放", port)

    report.all_ok = all(s.success for s in report.steps)
    report.finished_at = datetime.now().isoformat()
    logger.info("[Shutdown] === 退出流程完成, 全部成功: {} ===", report.all_ok)
    return report
