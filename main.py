"""
main.py — A股宏观锚定投研智能体 系统入口

用法：
  python main.py --mode static --pdf <policy_pdf_path>   # 静态图谱构建
  python main.py --mode dynamic                          # 动态监控（定时任务）
  python main.py --mode init                             # 初始化基础设施（建表、全A股入库）
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

    logger.info("[Main] 启动静态图谱构建: {}", pdf_path)
    # TODO: 调用 build_static_graph().invoke(...)
    logger.warning("[Main] static_graph 尚未实现，请先完成 nodes/ 模块")


async def run_dynamic() -> None:
    """动态监控流水线（APScheduler 定时任务）"""
    from src.infrastructure.database import init_all
    init_all()

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # 新闻轮询：每分钟
    async def rss_and_funnel_job():
        logger.debug("[Scheduler] rss_and_funnel_job 触发")
        # TODO: 实现 news_funnel 逻辑

    # 三共振检测：工作日 9:30-15:00 每10分钟
    async def resonance_check_job():
        logger.debug("[Scheduler] resonance_check_job 触发")
        # TODO: 实现 resonance_alert 逻辑

    # 盘后龙虎榜：工作日 15:30
    async def top_list_update_job():
        logger.debug("[Scheduler] top_list_update_job 触发")
        # TODO: 实现 top_list 更新逻辑

    scheduler.add_job(rss_and_funnel_job, IntervalTrigger(minutes=1))
    scheduler.add_job(resonance_check_job, CronTrigger(
        day_of_week="mon-fri", hour="9-14", minute="*/10"
    ))
    scheduler.add_job(top_list_update_job, CronTrigger(
        day_of_week="mon-fri", hour=15, minute=30
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


def main():
    parser = argparse.ArgumentParser(description="A股宏观锚定投研智能体")
    parser.add_argument(
        "--mode", required=True, choices=["static", "dynamic", "init"],
        help="运行模式：static=静态图谱, dynamic=动态监控, init=初始化入库"
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


if __name__ == "__main__":
    main()
