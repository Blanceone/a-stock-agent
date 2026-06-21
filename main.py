"""
main.py — A股宏观锚定投研智能体 系统入口

用法：
  python main.py --mode static --pdf <policy_pdf_path>   # 静态图谱构建
  python main.py --mode dynamic                          # 动态监控（定时任务）
  python main.py --mode init                             # 初始化基础设施（建表、全A股入库）
  python main.py --mode semantic                         # 语义知识库初始化（主营业务向量化入库）
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

# 项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# 加载环境变量（.env 文件）
load_dotenv(PROJECT_ROOT / ".env")


def run_static(pdf_path: str) -> None:
    """静态产业链图谱构建流水线"""
    from src.infrastructure.database import init_all
    init_all()

    from src.graphs.static_graph import run_static_pipeline
    logger.info("[Main] 启动静态图谱构建: {}", pdf_path)

    final_state = run_static_pipeline(pdf_path)

    tier1 = final_state.get("ranked_stocks", {}).get("tier1", [])
    tier2 = final_state.get("ranked_stocks", {}).get("tier2", [])
    logger.info("[Main] ===== 静态图谱结果 =====")
    logger.info("[Main] 第一梯队 ({} 只):", len(tier1))
    for s in tier1:
        logger.info("  {} {} | score={:.3f} | {}", s["ts_code"], s["name"], s["score"], s["reason"])
    logger.info("[Main] 第二梯队 ({} 只):", len(tier2))
    for s in tier2:
        logger.info("  {} {} | score={:.3f} | {}", s["ts_code"], s["name"], s["score"], s["reason"])


# ── 概念词库持久化（内存共享）─────────────────────────────────────────────────
_shared_concepts: list[dict] = []


async def run_dynamic() -> None:
    """动态监控流水线（APScheduler 定时任务）"""
    global _shared_concepts
    from src.infrastructure.database import init_all
    init_all()

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    from src.graphs.dynamic_graph import process_news_item
    from src.infrastructure.rss_fetcher import poll_rss

    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # RSS 轮询 → 动态流水线
    async def rss_pipeline_job():
        global _shared_concepts
        async for news_item in poll_rss():
            result = process_news_item(news_item, _shared_concepts)
            if result.get("concepts_updated"):
                _shared_concepts = result["concepts_updated"]
            for alert in result.get("resonance_alerts", []):
                logger.warning(
                    "🚨 [预警] {} | {} | 消息{:.2f} 资金{:.1f}% 量比{:.1f}",
                    alert["ts_code"], alert["news_title"],
                    alert["news_score"], alert["capital_inflow_pct"],
                    alert["volume_ratio"],
                )

    # 盘后龙虎榜：工作日 15:30
    async def top_list_update_job():
        logger.debug("[Scheduler] top_list_update_job 触发")
        from datetime import datetime
        from src.infrastructure.data_fetcher import fetch_top_list
        try:
            df = fetch_top_list(datetime.now().strftime("%Y%m%d"))
            logger.info("[Main] 龙虎榜更新: {} 条记录", len(df))
        except Exception as e:
            logger.warning("[Main] 龙虎榜更新失败: {}", e)

    scheduler.add_job(rss_pipeline_job, IntervalTrigger(minutes=1))
    scheduler.add_job(top_list_update_job, CronTrigger(
        day_of_week="mon-fri", hour=15, minute=30
    ))

    # SOP 自学习：周日 02:00
    async def sop_learning_job():
        logger.info("[Scheduler] sop_learning_job 触发")
        from src.nodes.sop_learner import run as sop_run
        try:
            result = sop_run({})
            logger.info("[Main] SOP 学习完成: {} 条", result.get("sop_processed", 0))
        except Exception as e:
            logger.warning("[Main] SOP 学习失败: {}", e)

    scheduler.add_job(sop_learning_job, CronTrigger(
        day_of_week="sun", hour=2, minute=0
    ))

    scheduler.start()
    logger.info("[Main] 动态监控已启动，按 Ctrl+C 停止")
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


def run_init() -> None:
    """初始化：建表 + 全A股基础信息入库"""
    from src.infrastructure.database import init_all, get_pg_conn, release_pg_conn
    from src.infrastructure.data_fetcher import fetch_stock_basic

    init_all()
    logger.info("[Main] 开始拉取全A股基础信息...")

    df = fetch_stock_basic()
    logger.info("[Main] 获取 {} 条股票基础信息", len(df))

    conn = get_pg_conn()
    try:
        with conn.cursor() as cur:
            # 清空旧数据后重新写入
            cur.execute("TRUNCATE stock_basic")
            for _, row in df.iterrows():
                is_st = "ST" in str(row.get("name", ""))
                cur.execute(
                    """INSERT INTO stock_basic (ts_code, name, industry, circ_mv, is_st, list_status)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (ts_code) DO UPDATE SET
                         name=EXCLUDED.name, industry=EXCLUDED.industry,
                         circ_mv=EXCLUDED.circ_mv, is_st=EXCLUDED.is_st,
                         list_status=EXCLUDED.list_status, updated_at=NOW()""",
                    (row["ts_code"], row["name"], row.get("industry"),
                     row.get("circ_mv"), is_st, row.get("list_status", "L")),
                )
        conn.commit()
        logger.info("[Main] 全A股基础信息入库完成: {} 条", len(df))
    finally:
        release_pg_conn(conn)


def run_semantic() -> None:
    """语义知识库初始化：主营业务文本 → V4-Flash 摘要 → ChromaDB 向量化"""
    from src.infrastructure.database import init_all
    from src.infrastructure.semantic_init import run as semantic_run

    init_all()
    logger.info("[Main] 启动语义知识库初始化")

    stats = semantic_run()
    logger.info("[Main] 语义知识库初始化完成: {}", stats)


def main():
    parser = argparse.ArgumentParser(description="A股宏观锚定投研智能体")
    parser.add_argument(
        "--mode", required=True, choices=["static", "dynamic", "init", "semantic"],
        help="运行模式：static=静态图谱, dynamic=动态监控, init=初始化入库, semantic=语义知识库"
    )
    parser.add_argument("--pdf", type=str, default="", help="政策PDF路径（static模式）")
    args = parser.parse_args()

    if args.mode == "static":
        if not args.pdf:
            parser.error("--pdf 参数在 static 模式下必填")
        run_static(args.pdf)
    elif args.mode == "dynamic":
        asyncio.run(run_dynamic())
    elif args.mode == "init":
        run_init()
    elif args.mode == "semantic":
        run_semantic()


if __name__ == "__main__":
    main()
