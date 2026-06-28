"""
main.py — A股宏观锚定投研智能体 系统入口

用法：
  python main.py --mode static --pdf <policy_pdf_path>   # 静态图谱构建
  python main.py --mode dynamic                          # 动态监控（定时任务）
  python main.py --mode init                             # 初始化基础设施（建表、全A股入库）
  python main.py --mode semantic                         # 语义知识库初始化（主营业务向量化入库）
  python main.py --mode concept_graph                    # 政策概念图谱构建
"""
from __future__ import annotations

import argparse
import asyncio
import json
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
    from src.infrastructure.env_check import ensure_env
    ensure_env(need_pg=True, need_redis=True)
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


# ── PostgreSQL 落盘辅助 ─────────────────────────────────────────────────────
def _pg_persist_news_analysis(news_result: dict, item) -> None:
    """将新闻分析结果写入 PG news_analysis 表（UPSERT）"""
    try:
        from src.infrastructure.database import get_pg_conn, release_pg_conn, pg_pool
        if pg_pool is None:
            return
        conn = get_pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO news_analysis (article_id, source, title, pub_time,
                        news_score, impact_type, impact_concept, sentiment, reason,
                        related_ts_codes, new_concept_terms,
                        mentioned_companies, supply_chain_impact, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    ON CONFLICT (article_id) DO UPDATE SET
                        news_score=EXCLUDED.news_score, impact_type=EXCLUDED.impact_type,
                        impact_concept=EXCLUDED.impact_concept, sentiment=EXCLUDED.sentiment,
                        reason=EXCLUDED.reason, related_ts_codes=EXCLUDED.related_ts_codes,
                        new_concept_terms=EXCLUDED.new_concept_terms,
                        mentioned_companies=EXCLUDED.mentioned_companies,
                        supply_chain_impact=EXCLUDED.supply_chain_impact,
                        updated_at=NOW()
                """, (
                    item.article_id,
                    getattr(item, 'source', ''),
                    news_result.get("news_title", ""),
                    getattr(item, 'pub_time', None),
                    news_result.get("news_score", 0),
                    news_result.get("impact_type", ""),
                    news_result.get("impact_concept", ""),
                    news_result.get("sentiment", "neutral"),
                    news_result.get("reason", ""),
                    json.dumps(news_result.get("related_ts_codes", []), ensure_ascii=False),
                    json.dumps(news_result.get("new_concept_terms", []), ensure_ascii=False),
                    json.dumps(news_result.get("mentioned_companies", []), ensure_ascii=False),
                    json.dumps(news_result.get("supply_chain_impact", []), ensure_ascii=False),
                ))
            conn.commit()
        finally:
            release_pg_conn(conn)
    except Exception as e:
        logger.debug("[Main] PG news_analysis 落盘失败: {}", e)


def _pg_persist_concept_stocks(concepts_data: list[dict]) -> None:
    """批量将概念-股票映射写入 PG concept_stocks 表（UPSERT）"""
    try:
        from src.infrastructure.database import get_pg_conn, release_pg_conn, pg_pool
        if pg_pool is None or not concepts_data:
            return
        conn = get_pg_conn()
        try:
            with conn.cursor() as cur:
                for board in concepts_data:
                    concept = board.get("concept", "")
                    source = board.get("source", "unknown")
                    for ts_code in board.get("stocks", []):
                        cur.execute("""
                            INSERT INTO concept_stocks (concept, ts_code, sources, stock_name, updated_at)
                            VALUES (%s, %s, %s, '', NOW())
                            ON CONFLICT (concept, ts_code) DO UPDATE SET
                                sources = (
                                    SELECT jsonb_agg(DISTINCT v)
                                    FROM (
                                        SELECT jsonb_array_elements(concept_stocks.sources) AS v
                                        UNION
                                        SELECT %s::jsonb AS v
                                    ) sub
                                ),
                                updated_at = NOW()
                        """, (concept, ts_code,
                              json.dumps([source], ensure_ascii=False),
                              json.dumps(source, ensure_ascii=False)))
            conn.commit()
        finally:
            release_pg_conn(conn)
    except Exception as e:
        logger.debug("[Main] PG concept_stocks 落盘失败: {}", e)


def _persist_concept_stocks(redis_client, concept_terms: list[str],
                            ts_codes: list[str], source: str = "llm",
                            stock_names: dict[str, str] | None = None):
    """将概念词→关联股票映射写入 Redis hash dynamic:concepts
    结构: field=concept_name, value=JSON{stocks_detail:{ts_code:{sources:[], name:""}}, sources:[], last_seen}
    """
    if not concept_terms or not redis_client:
        return
    from datetime import datetime
    now = datetime.now().isoformat()
    for term in concept_terms:
        if not term:
            continue
        try:
            existing_raw = redis_client.hget("dynamic:concepts", term)
            stocks_detail: dict[str, dict] = {}
            concept_sources_set: set[str] = set()
            if existing_raw:
                existing = json.loads(existing_raw)
                stocks_detail = existing.get("stocks_detail", {})
                concept_sources_set = set(existing.get("sources", []))
                # 兼容旧格式：从 stocks 数组迁移
                if not stocks_detail and "stocks" in existing:
                    for sc in existing["stocks"]:
                        stocks_detail[sc] = {"sources": ["llm"], "name": ""}

            # 添加/更新股票（合并来源）
            for ts_code in (ts_codes or []):
                if ts_code in stocks_detail:
                    if source not in stocks_detail[ts_code].get("sources", []):
                        stocks_detail[ts_code]["sources"].append(source)
                else:
                    name = (stock_names or {}).get(ts_code, "")
                    stocks_detail[ts_code] = {"sources": [source], "name": name}

            concept_sources_set.add(source)

            # 语义增强：ChromaDB 补充股票 + 语义打分
            from src.infrastructure.concept_sources import enrich_concept_stocks
            stocks_detail = enrich_concept_stocks(term, stocks_detail)
            stocks_list = list(stocks_detail.keys())

            redis_client.hset(
                "dynamic:concepts", term,
                json.dumps({
                    "stocks": stocks_list,
                    "stocks_detail": stocks_detail,
                    "sources": list(concept_sources_set),
                    "confidence": 0.5,  # LLM 发现的概念默认置信度
                    "last_seen": now,
                }, ensure_ascii=False),
            )
            redis_client.expire("dynamic:concepts", 7 * 86400)
        except Exception as e:
            logger.debug("[Main] 概念持久化失败 {}: {}", term, e)

    # PG 落盘（LLM 发现的概念-股票映射）
    try:
        from src.infrastructure.database import get_pg_conn, release_pg_conn, pg_pool
        if pg_pool is not None:
            conn = get_pg_conn()
            try:
                with conn.cursor() as cur:
                    for term in concept_terms:
                        if not term:
                            continue
                        for ts_code in (ts_codes or []):
                            cur.execute("""
                                INSERT INTO concept_stocks (concept, ts_code, sources, stock_name, updated_at)
                                VALUES (%s, %s, '["llm"]', '', NOW())
                                ON CONFLICT (concept, ts_code) DO UPDATE SET
                                    sources = (
                                        SELECT jsonb_agg(DISTINCT v)
                                        FROM (
                                            SELECT jsonb_array_elements(concept_stocks.sources) AS v
                                            UNION SELECT '"llm"'::jsonb AS v
                                        ) sub
                                    ),
                                    updated_at = NOW()
                            """, (term, ts_code))
                conn.commit()
            finally:
                release_pg_conn(conn)
    except Exception as e:
        logger.debug("[Main] 概念 PG 落盘失败: {}", e)


def sync_concepts_to_redis(redis_client, source: str, concepts_data: list[dict]):
    """将外部概念数据（akshare/tushare）批量写入 Redis dynamic:concepts"""
    if not redis_client or not concepts_data:
        return
    from datetime import datetime
    now = datetime.now().isoformat()
    count = 0
    for board in concepts_data:
        concept_name = board.get("concept", "")
        stocks = board.get("stocks", [])
        board_source = board.get("source", source)
        if not concept_name or not stocks:
            continue
        try:
            existing_raw = redis_client.hget("dynamic:concepts", concept_name)
            stocks_detail: dict[str, dict] = {}
            concept_sources_set: set[str] = set()
            if existing_raw:
                existing = json.loads(existing_raw)
                stocks_detail = existing.get("stocks_detail", {})
                concept_sources_set = set(existing.get("sources", []))
                if not stocks_detail and "stocks" in existing:
                    for sc in existing["stocks"]:
                        stocks_detail[sc] = {"sources": ["llm"], "name": ""}

            for ts_code in stocks:
                if ts_code in stocks_detail:
                    if board_source not in stocks_detail[ts_code].get("sources", []):
                        stocks_detail[ts_code]["sources"].append(board_source)
                else:
                    stocks_detail[ts_code] = {"sources": [board_source], "name": ""}

            concept_sources_set.add(board_source)

            # 语义增强：ChromaDB 补充股票 + 语义打分
            from src.infrastructure.concept_sources import enrich_concept_stocks
            stocks_detail = enrich_concept_stocks(concept_name, stocks_detail)
            stocks_list = list(stocks_detail.keys())

            payload = {
                "stocks": stocks_list,
                "stocks_detail": stocks_detail,
                "sources": list(concept_sources_set),
                "confidence": 0.9,  # 外部数据源（akshare/tushare）高可信度
                "last_seen": now,
            }
            # 保存 LLM 评估的分数和分类（如有）
            score = board.get("score", 0)
            category = board.get("category", "")
            if score:
                payload["policy_score"] = score
            if category:
                payload["category"] = category

            redis_client.hset(
                "dynamic:concepts", concept_name,
                json.dumps(payload, ensure_ascii=False),
            )
            count += 1
        except Exception as e:
            logger.debug("[Main] 概念同步失败 {}: {}", concept_name, e)

    if count > 0:
        redis_client.expire("dynamic:concepts", 7 * 86400)
        logger.info("[Main] 概念同步 {}: 写入 {} 个概念", source, count)


async def concept_sync_job():
    """定时同步 akshare + tushare 概念板块数据到 Redis"""
    import src.infrastructure.database as db
    from src.infrastructure.concept_sources import fetch_akshare_concepts, fetch_tushare_concepts

    if db.redis_client is None:
        logger.warning("[Main] concept_sync_job: Redis 未连接，跳过")
        return

    logger.info("[Main] ===== 概念板块同步开始 =====")
    akshare_data = []
    tushare_data = []

    # akshare 东财概念
    try:
        akshare_data = await asyncio.to_thread(fetch_akshare_concepts)
        if akshare_data:
            sync_concepts_to_redis(db.redis_client, "akshare_em", akshare_data)
        else:
            logger.warning("[Main] akshare 概念数据为空")
    except Exception as e:
        logger.warning("[Main] akshare 概念同步失败: {}", e)

    # tushare 概念（需5000+积分，2000积分不可用，静默跳过）
    try:
        tushare_data = await asyncio.to_thread(fetch_tushare_concepts)
        if tushare_data:
            sync_concepts_to_redis(db.redis_client, "tushare", tushare_data)
    except Exception as e:
        logger.debug("[Main] tushare 概念同步跳过: {}", e)

    # PG 落盘（概念-股票映射，在线程池中批量写入）
    all_concepts = akshare_data + tushare_data
    if all_concepts:
        await asyncio.to_thread(_pg_persist_concept_stocks, all_concepts)

    # 统计 Redis 中概念总数
    if db.redis_client:
        try:
            total = db.redis_client.hlen("dynamic:concepts")
            logger.info("[Main] ===== 概念板块同步完成 (Redis 共 {} 个概念) =====", total)
        except Exception:
            logger.info("[Main] ===== 概念板块同步完成 =====")
    else:
        logger.info("[Main] ===== 概念板块同步完成 =====")


async def run_dynamic() -> None:
    """动态监控流水线（APScheduler 定时任务）"""
    global _shared_concepts
    import src.infrastructure.database as db
    from src.infrastructure.env_check import ensure_env
    ensure_env(need_pg=True, need_redis=True)
    db.init_all()

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from src.graphs.dynamic_graph import process_news_item
    from src.infrastructure.news_sources import NewsAggregator
    from src.infrastructure.news_sources.cls_telegraph import CLSTelegraphSource
    from src.infrastructure.news_sources.gov_policy import GovPolicySource
    from src.infrastructure.news_sources.csrc_policy import CSRCSource

    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    news_queue: asyncio.Queue = asyncio.Queue()

    # ── 即时并行处理回调（每条新闻到达即处理，不等待定时任务）─────
    async def process_single_news(item):
        """在线程池中运行 LLM 流水线，结果写入 Redis"""
        global _shared_concepts
        # 先标记为"处理中"，让前端区分排队/处理中
        if db.redis_client is not None:
            try:
                db.redis_client.hset(
                    "dynamic:news_analysis",
                    item.article_id,
                    json.dumps({"status": "processing"}, ensure_ascii=False),
                )
            except Exception:
                pass
        try:
            result = await asyncio.to_thread(process_news_item, item, _shared_concepts)
        except Exception as e:
            logger.error("[Main] 新闻处理异常 article_id={}: {}", item.article_id, e, exc_info=True)
            # 异常时也写入状态，避免永远卡在"排队中"
            if db.redis_client is not None:
                try:
                    db.redis_client.hset(
                        "dynamic:news_analysis",
                        item.article_id,
                        json.dumps({"status": "error", "error_msg": str(e)[:200]}, ensure_ascii=False),
                    )
                    db.redis_client.expire("dynamic:news_analysis", 7 * 86400)
                except Exception:
                    pass
            return

        # 更新内存中的概念词库（CPython list 赋值是原子的）
        concepts_updated = result.get("concepts_updated")
        if concepts_updated:
            _shared_concepts = concepts_updated

        # 持久化分析结果 + 概念→股票映射到 Redis
        news_result = result.get("news_result")
        if db.redis_client is not None:
            try:
                if news_result:
                    analysis = {
                        "status": "analyzed",
                        "news_score": news_result.get("news_score", 0),
                        "impact_type": news_result.get("impact_type", ""),
                        "impact_concept": news_result.get("impact_concept", ""),
                        "impact_node": news_result.get("impact_node", ""),
                        "sentiment": news_result.get("sentiment", "neutral"),
                        "reason": news_result.get("reason", ""),
                        "mentioned_companies": json.dumps(
                            news_result.get("mentioned_companies", []),
                            ensure_ascii=False,
                        ),
                        "supply_chain_impact": json.dumps(
                            news_result.get("supply_chain_impact", []),
                            ensure_ascii=False,
                        ),
                        "related_ts_codes": json.dumps(
                            news_result.get("related_ts_codes", []),
                            ensure_ascii=False,
                        ),
                        "new_concept_terms": json.dumps(
                            news_result.get("new_concept_terms", []),
                            ensure_ascii=False,
                        ),
                    }
                    # 持久化概念→股票映射
                    _persist_concept_stocks(
                        db.redis_client,
                        news_result.get("new_concept_terms", []),
                        news_result.get("related_ts_codes", []),
                    )
                    # 概念→新闻反向映射（Sorted Set）+ 概念→股票评分（Hash）
                    import time as _time
                    _ts = _time.time()
                    _impact = [news_result["impact_concept"]] if news_result.get("impact_concept") else []
                    _all_concepts = list(set(_impact) | set(news_result.get("new_concept_terms", [])))
                    _all_concepts = [c for c in _all_concepts if c]
                    if _all_concepts:
                        _news_score = news_result.get("news_score", 0)
                        _news_summary = json.dumps({
                            "title": news_result.get("news_title", ""),
                            "sentiment": news_result.get("sentiment", "neutral"),
                            "score": _news_score,
                            "ts": _ts,
                        }, ensure_ascii=False)
                        _related_codes = news_result.get("related_ts_codes", [])
                        _pipe = db.redis_client.pipeline(transaction=False)
                        for _concept in _all_concepts:
                            _key_news = f"dynamic:concept_news:{_concept}"
                            _key_scores = f"dynamic:concept_stock_scores:{_concept}"
                            _pipe.zadd(_key_news, {_news_summary: _ts})
                            _pipe.zremrangebyrank(_key_news, 0, -201)  # 保留最近 200 条
                            _pipe.expire(_key_news, 7 * 86400)
                            for _tc in _related_codes:
                                _pipe.hincrbyfloat(_key_scores, _tc, _news_score)
                            _pipe.expire(_key_scores, 7 * 86400)
                        try:
                            _pipe.execute()
                        except Exception as _pe:
                            logger.debug("[Main] 反向映射写入失败: {}", _pe)
                else:
                    analysis = {"status": "filtered", "news_score": 0}

                db.redis_client.hset(
                    "dynamic:news_analysis",
                    item.article_id,
                    json.dumps(analysis, ensure_ascii=False),
                )
                db.redis_client.expire("dynamic:news_analysis", 7 * 86400)
            except Exception as e:
                logger.debug("[Main] 分析结果持久化失败: {}", e)

            # PG 落盘（新闻分析结果，异步线程池中执行避免阻塞）
            if news_result:
                await asyncio.to_thread(_pg_persist_news_analysis, news_result, item)

                # 概念图谱增量插入（新闻发现的新概念自动挂入图谱）
                new_terms = news_result.get("new_concept_terms", [])
                if new_terms and db.redis_client:
                    try:
                        from src.nodes.concept_graph_builder import incremental_insert
                        await asyncio.to_thread(incremental_insert, new_terms, news_result)
                    except Exception as _cge:
                        logger.debug("[Main] 概念图谱增量插入失败: {}", _cge)

        # 输出预警日志
        for alert in result.get("resonance_alerts", []):
            logger.warning(
                "🚨 [预警] {} | {} | 消息{:.2f} 资金{:.1f}% 量比{:.1f}",
                alert["ts_code"], alert["news_title"],
                alert["news_score"], alert["capital_inflow_pct"],
                alert["volume_ratio"],
            )

    # 启动多源新闻聚合器（生产者）
    # 财联社 5s + 国务院 60s + 证监会 60s，各自独立轮询
    sources = [CLSTelegraphSource(), GovPolicySource(), CSRCSource()]
    aggregator = NewsAggregator(
        news_queue, sources, db.redis_client,
        on_process=process_single_news,
    )
    _pending_tasks: set[asyncio.Task] = set()
    _aggr_task = asyncio.create_task(aggregator.start())
    _pending_tasks.add(_aggr_task)
    _aggr_task.add_done_callback(_pending_tasks.discard)

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

    # 新闻处理已通过 aggregator 回调即时并行执行，无需定时轮询
    scheduler.add_job(top_list_update_job, CronTrigger(
        day_of_week="mon-fri", hour=15, minute=30
    ))

    # 概念板块同步：工作日 09:00（akshare + tushare）
    scheduler.add_job(concept_sync_job, CronTrigger(
        day_of_week="mon-fri", hour=9, minute=0
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

    # 启动时立即执行一次概念同步（后台异步，不阻塞新闻流）
    _sync_task = asyncio.create_task(concept_sync_job())
    _pending_tasks.add(_sync_task)
    _sync_task.add_done_callback(_pending_tasks.discard)

    logger.info("[Main] 动态监控已启动，按 Ctrl+C 停止")
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown()
        # 等待 pending tasks 完成（最长 5 秒）
        for t in list(_pending_tasks):
            try:
                await asyncio.wait_for(t, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                t.cancel()
        await aggregator.stop()
        for src in sources:
            try:
                await src.close()
            except Exception:
                pass
        # 关闭所有数据库连接
        import src.infrastructure.database as _db
        _db.close_all()
        logger.info("[Main] 动态监控已安全关闭")


def run_init() -> None:
    """初始化：建表 + 全A股基础信息入库"""
    from src.infrastructure.env_check import ensure_env
    ensure_env(need_pg=True)
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
    from src.infrastructure.env_check import ensure_env
    ensure_env(need_pg=True, need_chromadb=True)
    from src.infrastructure.database import init_all
    from src.infrastructure.semantic_init import run as semantic_run

    init_all()
    logger.info("[Main] 启动语义知识库初始化")

    stats = semantic_run()
    logger.info("[Main] 语义知识库初始化完成: {}", stats)


def run_concept_graph(policy_text: str = "") -> None:
    """政策概念图谱构建"""
    from src.infrastructure.env_check import ensure_env
    ensure_env(need_pg=True, need_redis=True, need_chromadb=True)
    from src.infrastructure.database import init_all
    init_all()

    from src.nodes.concept_graph_builder import build_full
    logger.info("[Main] 启动政策概念图谱构建")

    stats = build_full(
        policy_text_path_or_content=policy_text or None,
        progress_callback=None,
    )
    logger.info("[Main] 概念图谱构建完成: {}", stats)


def main():
    parser = argparse.ArgumentParser(description="A股宏观锚定投研智能体")
    parser.add_argument(
        "--mode", required=True, choices=["static", "dynamic", "init", "semantic", "concept_graph"],
        help="运行模式：static=静态图谱, dynamic=动态监控, init=初始化入库, semantic=语义知识库, concept_graph=政策概念图谱"
    )
    parser.add_argument("--pdf", type=str, default="", help="政策PDF路径（static模式）")
    parser.add_argument("--policy-text", type=str, default="", help="政策文本路径或内容（concept_graph模式可选）")
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
    elif args.mode == "concept_graph":
        run_concept_graph(args.policy_text)


if __name__ == "__main__":
    main()
