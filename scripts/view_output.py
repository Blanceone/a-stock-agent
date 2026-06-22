"""
view_output.py — 查看系统运行输出数据

用法: python scripts/view_output.py [alerts|stockpool|sop-pending|sop-active|stats]

无参数时进入交互式菜单，带参数时直接输出对应数据。
"""
import sys
import os
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

API_BASE = "http://localhost:8088"


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _get_redis():
    from src.infrastructure.database import redis_client
    return redis_client


def _get_pg():
    from src.infrastructure.database import get_pg_conn, release_pg_conn
    return get_pg_conn, release_pg_conn


def _api_get(path: str):
    import urllib.request
    try:
        with urllib.request.urlopen(f"{API_BASE}{path}", timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _print_separator(title: str):
    print(f"\n  {'─' * 50}")
    print(f"   {title}")
    print(f"  {'─' * 50}")


# ── 功能函数 ──────────────────────────────────────────────────────────────────

def view_alerts():
    """查看今日三共振预警"""
    _print_separator("今日三共振预警")

    data = _api_get("/alerts/today")
    if data:
        items = data.get("items", [])
        date = data.get("date", "")
        if not items:
            print(f"  {date} 无预警信号")
        else:
            print(f"  {date} 共 {len(items)} 条预警\n")
            for i, a in enumerate(items, 1):
                print(f"  [{i}] {a.get('ts_code', '?')} | {a.get('news_title', '')[:40]}")
                print(f"      消息评分: {a.get('news_score', 0):.2f}")
                print(f"      主力净流入: {a.get('capital_inflow_pct', 0):.1f}%")
                print(f"      量比: {a.get('volume_ratio', 0):.1f}")
        return

    # API 不可用，尝试直连 Redis
    redis = _get_redis()
    if redis:
        from datetime import datetime
        today = datetime.now().strftime("%Y%m%d")
        key = f"dynamic:alerts:{today}"
        items = redis.lrange(key, 0, -1)
        if not items:
            print(f"  {today} 无预警信号（Redis 直连）")
        else:
            print(f"  {today} 共 {len(items)} 条预警（Redis 直连）\n")
            for i, raw in enumerate(items, 1):
                a = json.loads(raw)
                print(f"  [{i}] {a.get('ts_code', '?')} | {a.get('news_title', '')[:40]}")
                print(f"      消息评分: {a.get('news_score', 0):.2f}")
                print(f"      主力净流入: {a.get('capital_inflow_pct', 0):.1f}%")
                print(f"      量比: {a.get('volume_ratio', 0):.1f}")
        return

    print("  [!] 无法连接（请先启动 API 或 SSH 隧道）")


def view_stock_pool():
    """查看静态图谱输出股池"""
    _print_separator("静态图谱股池")

    # 优先从 Redis 读取
    redis = _get_redis()
    if redis:
        raw = redis.get("static:stock_pool")
        if raw:
            data = json.loads(raw)
            tier1 = data.get("tier1", [])
            tier2 = data.get("tier2", [])
            print(f"\n  第一梯队 ({len(tier1)} 只):")
            if not tier1:
                print("    (空)")
            for i, s in enumerate(tier1, 1):
                print(f"    {i}. {s['ts_code']} {s['name']} | "
                      f"score={s['score']:.3f} | {s['reason']}")
            print(f"\n  第二梯队 ({len(tier2)} 只):")
            if not tier2:
                print("    (空)")
            for i, s in enumerate(tier2, 1):
                print(f"    {i}. {s['ts_code']} {s['name']} | "
                      f"score={s['score']:.3f} | {s['reason']}")
            return
        else:
            print("  Redis 中无股池数据（尚未运行静态图谱构建）")
            return

    # 尝试 PG 查询
    try:
        get_pg, release_pg = _get_pg()
        conn = get_pg()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ts_code, name, industry, circ_mv, is_st "
                    "FROM stock_basic WHERE list_status = 'L' AND is_st = FALSE "
                    "ORDER BY circ_mv DESC LIMIT 20"
                )
                rows = cur.fetchall()
            if not rows:
                print("  stock_basic 表无数据（请先运行初始化）")
            else:
                print(f"\n  stock_basic 表前 20 条（按流通市值排序）:\n")
                for r in rows:
                    mv_yi = r[3] / 10000 if r[3] else 0
                    print(f"    {r[0]:12s} {r[1]:8s} | {r[2] or '':8s} | "
                          f"流通市值 {mv_yi:.0f} 亿")
        finally:
            release_pg(conn)
    except Exception as e:
        print(f"  [!] 无法连接数据库: {e}")


def view_sop_pending():
    """查看待审核 SOP"""
    _print_separator("待审核 SOP")

    data = _api_get("/sop/pending")
    if data:
        items = data.get("items", [])
        print(f"  共 {data.get('count', 0)} 条待审核\n")
        for i, item in enumerate(items, 1):
            print(f"  [{i}] #{item['id']} | {item['status']} | {item['created_at']}")
            graph = item.get("graph_json", {})
            if isinstance(graph, dict):
                sop_name = graph.get("sop_name", "")
                if sop_name:
                    print(f"      SOP: {sop_name}")
            src = item.get("source_text", "")
            if src:
                print(f"      来源: {src[:80]}...")
            print()
        return

    # 直连 PG
    try:
        get_pg, release_pg = _get_pg()
        conn = get_pg()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, source_text, status, created_at "
                    "FROM sop_pending WHERE status = 'pending' "
                    "ORDER BY created_at DESC LIMIT 20"
                )
                rows = cur.fetchall()
            if not rows:
                print("  无待审核 SOP")
            else:
                print(f"  共 {len(rows)} 条\n")
                for r in rows:
                    print(f"  #{r[0]} | {r[2]} | {r[3]}")
                    print(f"      {str(r[1])[:80]}")
        finally:
            release_pg(conn)
    except Exception as e:
        print(f"  [!] 无法连接: {e}")


def view_sop_active():
    """查看已审核 SOP"""
    _print_separator("已审核 SOP（含待批准）")

    data = _api_get("/sop/active")
    if data:
        items = data.get("items", [])
        print(f"  共 {data.get('count', 0)} 条\n")
        for i, item in enumerate(items, 1):
            badge = "已批准" if item.get("approved") else "待批准"
            print(f"  [{i}] #{item['id']} [{badge}] {item.get('sop_name', '')}")
            if item.get("policy_name"):
                print(f"      关联政策: {item['policy_name']}")
            if item.get("approved_by"):
                print(f"      审核人: {item['approved_by']} | {item.get('approved_at', '')}")
            print()
        return

    # 直连 PG
    try:
        get_pg, release_pg = _get_pg()
        conn = get_pg()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, sop_name, policy_name, approved, approved_by, approved_at "
                    "FROM sop_active ORDER BY id DESC LIMIT 20"
                )
                rows = cur.fetchall()
            if not rows:
                print("  无已审核 SOP")
            else:
                print(f"  共 {len(rows)} 条\n")
                for r in rows:
                    badge = "已批准" if r[3] else "待批准"
                    print(f"  #{r[0]} [{badge}] {r[1]}")
                    if r[2]:
                        print(f"      关联政策: {r[2]}")
                    if r[4]:
                        print(f"      审核人: {r[4]} | {r[5]}")
        finally:
            release_pg(conn)
    except Exception as e:
        print(f"  [!] 无法连接: {e}")


def view_stats():
    """查看系统运行统计"""
    _print_separator("系统运行统计")

    # PostgreSQL 统计
    try:
        get_pg, release_pg = _get_pg()
        conn = get_pg()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM stock_basic")
                stock_cnt = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM stock_basic WHERE list_status='L' AND is_st=FALSE")
                active_cnt = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM sop_pending WHERE status='pending'")
                sop_p_cnt = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM sop_active")
                sop_a_cnt = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM sop_active WHERE approved=TRUE")
                sop_appr_cnt = cur.fetchone()[0]

            print(f"\n  PostgreSQL:")
            print(f"    stock_basic:  {stock_cnt} 条 (活跃 {active_cnt}, ST/退市 {stock_cnt - active_cnt})")
            print(f"    sop_pending:  {sop_p_cnt} 条待审核")
            print(f"    sop_active:   {sop_a_cnt} 条 ({sop_appr_cnt} 已批准, {sop_a_cnt - sop_appr_cnt} 待批准)")
        finally:
            release_pg(conn)
    except Exception as e:
        print(f"  PostgreSQL: 无法连接 ({e})")

    # ChromaDB 统计
    try:
        from src.infrastructure.database import chroma_collection
        if chroma_collection:
            cnt = chroma_collection.count()
            print(f"\n  ChromaDB:")
            print(f"    stock_business: {cnt} 条向量化记录")
        else:
            print(f"\n  ChromaDB: 未连接")
    except Exception:
        print(f"\n  ChromaDB: 未连接")

    # Redis 统计
    redis = _get_redis()
    if redis:
        try:
            llm_keys = len(redis.keys("llm:*"))
            today_alerts_key = f"dynamic:alerts:{__import__('datetime').datetime.now().strftime('%Y%m%d')}"
            alerts_cnt = redis.llen(today_alerts_key) or 0
            has_pool = 1 if redis.exists("static:stock_pool") else 0

            print(f"\n  Redis:")
            print(f"    LLM 缓存:      {llm_keys} 条")
            print(f"    今日预警:      {alerts_cnt} 条")
            print(f"    静态图谱结果:  {'有' if has_pool else '无'}")
        except Exception:
            print(f"\n  Redis: 连接异常")
    else:
        print(f"\n  Redis: 未连接")


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main():
    # 命令行参数模式
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        dispatch = {
            "alerts": view_alerts,
            "stockpool": view_stock_pool,
            "sop-pending": view_sop_pending,
            "sop-active": view_sop_active,
            "stats": view_stats,
        }
        fn = dispatch.get(cmd)
        if fn:
            fn()
        else:
            print(f"用法: python scripts/view_output.py [{'|'.join(dispatch.keys())}]")
        return

    # 交互式菜单
    while True:
        print(f"\n  {'═' * 44}")
        print(f"   系统输出查看器")
        print(f"  {'─' * 44}")
        print(f"   [1] 今日三共振预警")
        print(f"   [2] 静态图谱股池")
        print(f"   [3] 待审核 SOP")
        print(f"   [4] 已审核 SOP")
        print(f"   [5] 系统运行统计")
        print(f"   [Q] 返回")
        print(f"  {'═' * 44}")

        c = input("  请选择 [1-5/Q]: ").strip().lower()
        if c == "1":
            view_alerts()
        elif c == "2":
            view_stock_pool()
        elif c == "3":
            view_sop_pending()
        elif c == "4":
            view_sop_active()
        elif c == "5":
            view_stats()
        elif c in ("q", ""):
            break
        else:
            print("  [!] 无效选择")


if __name__ == "__main__":
    main()
