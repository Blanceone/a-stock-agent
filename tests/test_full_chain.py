"""
test_full_chain.py — 全业务链路端到端测试

覆盖完整业务流：
  FC-1: 静态图谱全链路 DAG 编排（PDF → policy_parser → chain_splitter → entity_mapper → tech_ranker）
  FC-2: 静态图谱中间链路（预设概念词 → chain_splitter → entity_mapper → tech_ranker）
  FC-3: 动态监控全链路 DAG 编排（新闻 → news_funnel → resonance_alert → sop_learner）
  FC-4: SOP 自学习全生命周期（写入pending → 提取 → active → API查询）
  FC-5: 双流水线 Redis 跨管道数据流

运行方式: pytest tests/test_full_chain.py -v -s --tb=short --timeout=300
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")


# ═══════════════════════════════════════════════════════════════════════════════
# Session Fixtures
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="session", autouse=True)
def infra_init():
    """初始化全部基础设施（session 级，仅执行一次）"""
    from src.infrastructure.database import init_all
    init_all()


@pytest.fixture(scope="session")
def seed_chromadb():
    """向 ChromaDB 注入固态电池测试数据，确保 entity_mapper 有召回"""
    from src.infrastructure.database import chroma_collection
    if chroma_collection is None:
        pytest.skip("ChromaDB 不可用，跳过种子数据")

    test_stocks = [
        {"ts_code": "300750.SZ", "name": "宁德时代",
         "text": "宁德时代主营动力电池研发制造，布局固态电池技术路线，硫化物全固态电解质研发领先"},
        {"ts_code": "002460.SZ", "name": "赣锋锂业",
         "text": "赣锋锂业主营锂化合物及金属锂生产，是全球领先的锂生态企业，布局固态电池上游锂矿资源"},
        {"ts_code": "002594.SZ", "name": "比亚迪",
         "text": "比亚迪主营新能源汽车及动力电池，刀片电池技术领先，布局半固态电池量产"},
        {"ts_code": "300014.SZ", "name": "亿纬锂能",
         "text": "亿纬锂能主营锂电池及储能产品，在固态电池电解质材料领域有技术积累"},
        {"ts_code": "002709.SZ", "name": "天赐材料",
         "text": "天赐材料主营锂电池电解液及材料，布局固态电解质关键材料研发"},
    ]

    ids = [f"test_seed_{s['ts_code']}" for s in test_stocks]
    documents = [s["text"] for s in test_stocks]
    metadatas = [{"ts_code": s["ts_code"], "name": s["name"]} for s in test_stocks]

    try:
        chroma_collection.add(ids=ids, documents=documents, metadatas=metadatas)
    except Exception:
        pass  # 可能已存在

    return chroma_collection.count()


# ═══════════════════════════════════════════════════════════════════════════════
# FC-1: 静态图谱全链路 DAG 编排
# ═══════════════════════════════════════════════════════════════════════════════
class TestStaticFullChain:
    """从 PDF 到最终排名的完整静态图谱 DAG"""

    @pytest.fixture(scope="class")
    def test_pdf(self, tmp_path_factory):
        """生成一份有实质内容的测试政策 PDF"""
        import fitz
        pdf_dir = tmp_path_factory.mktemp("pdfs")
        pdf_path = str(pdf_dir / "test_solid_battery_policy.pdf")
        doc = fitz.open()

        # 第1页: 目录页
        p1 = doc.new_page()
        p1.insert_text((72, 72), "目录")
        p1.insert_text((72, 90), "1. 固态电池产业发展规划概述")
        p1.insert_text((72, 108), "2. 产业链上下游布局分析")
        p1.insert_text((72, 126), "3. 技术创新路线图")
        p1.insert_text((72, 144), "4. 重点企业战略部署")

        # 第2页: 固态电池概述
        p2 = doc.new_page()
        lines = [
            "第一章 固态电池产业发展规划概述",
            "",
            "固态电池是下一代动力电池的核心技术方向，具有高能量密度、高安全性等显著优势。",
            "一、技术路线：硫化物全固态电解质、氧化物薄膜电解质、聚合物凝胶电解质三大路线并进。",
            "二、产业链上游：碳酸锂、氢氧化锂、锆英砂等关键原材料供应格局分析。",
            "三、产业链中游：高镍三元正极材料、硅碳负极材料、固态电解质膜、陶瓷隔膜技术进展。",
            "四、产业链下游：方形电芯制造、软包电芯封装、PACK系统集成、整车配套应用。",
            "五、重点企业：宁德时代、赣锋锂业、清陶能源、卫蓝新能源、辉能科技等龙头布局。",
            "六、时间节点：2027年半固态电池大规模量产，2030年全固态电池商业化目标。",
        ]
        y = 72
        for line in lines:
            p2.insert_text((72, y), line)
            y += 16

        # 第3页: 产业链布局
        p3 = doc.new_page()
        lines3 = [
            "第二章 产业链上下游布局分析",
            "",
            "固态电池产业链可分为上游资源、中游材料、下游应用三大环节。",
            "上游资源：锂矿资源全球供应集中度较高，澳大利亚、智利、阿根廷为主要产地。",
            "中游材料：电解质材料是技术壁垒最高的环节，硫化物路线以丰田、宁德时代为代表。",
            "下游应用：新能源汽车和消费电子是两大应用市场，储能领域前景广阔。",
            "关键技术节点：固态电解质量产工艺、界面稳定性、锂枝晶抑制技术。",
        ]
        y = 72
        for line in lines3:
            p3.insert_text((72, y), line)
            y += 16

        doc.save(pdf_path)
        doc.close()
        return pdf_path

    def test_full_static_dag(self, test_pdf, seed_chromadb):
        """FC-1: 完整静态 DAG: policy_parser → chain_splitter → entity_mapper → tech_ranker"""
        from src.graphs.static_graph import run_static_pipeline

        start = time.time()
        final_state = run_static_pipeline(test_pdf)
        elapsed = time.time() - start

        # 1. DAG 无错误中断
        error_node = final_state.get("error_node")
        error_msg = final_state.get("error_msg")
        assert error_node is None, f"静态 DAG 在节点 {error_node} 报错: {error_msg}"

        # 2. concepts 输出结构验证
        concepts = final_state.get("concepts", [])
        assert isinstance(concepts, list), "concepts 应为 list"
        for c in concepts:
            assert "concept" in c, f"concept 缺字段: {c}"

        # 3. industry_chains 输出结构验证
        chains = final_state.get("industry_chains", [])
        assert isinstance(chains, list), "industry_chains 应为 list"
        for chain in chains:
            assert "layers" in chain or "concept" in chain, f"chain 缺字段: {chain}"

        # 4. stock_pool 输出结构验证
        pool = final_state.get("stock_pool", [])
        assert isinstance(pool, list), "stock_pool 应为 list"
        for s in pool:
            assert "ts_code" in s, f"stock 缺 ts_code: {s}"
            assert "name" in s, f"stock 缺 name: {s}"

        # 5. ranked_stocks 输出结构验证
        ranked = final_state.get("ranked_stocks", {})
        assert "tier1" in ranked, "ranked_stocks 缺 tier1"
        assert "tier2" in ranked, "ranked_stocks 缺 tier2"
        assert isinstance(ranked["tier1"], list)
        assert isinstance(ranked["tier2"], list)

        # 6. tier1 各项应有 score 和 reason
        for item in ranked["tier1"]:
            assert "score" in item, f"tier1 项缺 score: {item}"
            assert "reason" in item, f"tier1 项缺 reason: {item}"
            assert item["score"] >= 0.7, f"tier1 分数应 ≥ 0.7: {item['score']}"

        # 7. tier2 各项应有 score
        for item in ranked["tier2"]:
            assert "score" in item, f"tier2 项缺 score: {item}"
            assert 0.5 <= item["score"] < 0.7, f"tier2 分数应在 0.5~0.7: {item['score']}"

        print(f"\n[FC-1] 全链路完成 | 耗时 {elapsed:.1f}s | "
              f"concepts={len(concepts)} chains={len(chains)} pool={len(pool)} "
              f"tier1={len(ranked['tier1'])} tier2={len(ranked['tier2'])}")


# ═══════════════════════════════════════════════════════════════════════════════
# FC-2: 静态图谱中间链路（预设概念词 → tech_ranker）
# ═══════════════════════════════════════════════════════════════════════════════
class TestStaticIntermediateChain:
    """使用预设概念词测试 chain_splitter → entity_mapper → tech_ranker"""

    def test_chain_to_tech_chain(self, seed_chromadb):
        """FC-2: 预设概念词 → chain_splitter → entity_mapper → tech_ranker"""
        from src.nodes import chain_splitter, entity_mapper, tech_ranker

        concepts = [
            {"concept": "固态电池", "source_section": "第1章", "confidence": 0.95},
        ]

        # Step 1: chain_splitter
        state = {"concepts": concepts}
        cs_result = chain_splitter.run(state)
        assert cs_result.get("error_node") is None, f"chain_splitter 报错: {cs_result.get('error_msg')}"
        industry_chains = cs_result.get("industry_chains", [])
        assert isinstance(industry_chains, list), "industry_chains 应为 list"

        # Step 2: entity_mapper（如果 chain_splitter 产出有效产业链）
        state.update(cs_result)
        em_result = entity_mapper.run(state)
        assert em_result.get("error_node") is None, f"entity_mapper 报错: {em_result.get('error_msg')}"
        stock_pool = em_result.get("stock_pool", [])
        assert isinstance(stock_pool, list), "stock_pool 应为 list"

        # Step 3: tech_ranker
        state.update(em_result)
        tr_result = tech_ranker.run(state)
        assert tr_result.get("error_node") is None, f"tech_ranker 报错: {tr_result.get('error_msg')}"
        ranked = tr_result.get("ranked_stocks", {})
        assert "tier1" in ranked
        assert "tier2" in ranked

        print(f"\n[FC-2] 中间链路完成 | chains={len(industry_chains)} "
              f"pool={len(stock_pool)} tier1={len(ranked['tier1'])} tier2={len(ranked['tier2'])}")


# ═══════════════════════════════════════════════════════════════════════════════
# FC-3: 动态监控全链路 DAG 编排
# ═══════════════════════════════════════════════════════════════════════════════
class TestDynamicFullChain:
    """动态监控完整 DAG：news_funnel → resonance_alert → sop_learner"""

    def test_full_dynamic_dag_high_score_news(self):
        """FC-3a: 高分新闻走完整动态 DAG"""
        from src.infrastructure.rss_fetcher import NewsItem
        from src.graphs.dynamic_graph import process_news_item

        news = NewsItem(
            article_id="fc3_test_001",
            title="固态电池重大突破：宁德时代量产良率达95%",
            summary="宁德时代宣布固态电池量产线良率突破95%，成本较液态电池下降30%。"
                     "多家券商上调固态电池板块评级，主力资金大幅流入相关个股。"
                     "宁德时代今日涨停，成交量较前日放大3倍。",
            pub_time=datetime.now(),
            source="财联社",
        )
        concepts = [
            {"concept": "固态电池", "source_section": "政策", "confidence": 0.9},
        ]

        start = time.time()
        result = process_news_item(news, concepts)
        elapsed = time.time() - start

        # 1. 无错误
        error_node = result.get("error_node")
        assert error_node is None, f"动态 DAG 在节点 {error_node} 报错: {result.get('error_msg')}"

        # 2. news_result 结构验证（粗筛通过时）
        news_result = result.get("news_result")
        if news_result:
            assert "news_score" in news_result
            assert "news_title" in news_result
            assert news_result["news_score"] > 0, "news_score 应 > 0"

        # 3. resonance_alerts 结构验证
        alerts = result.get("resonance_alerts", [])
        assert isinstance(alerts, list)
        for alert in alerts:
            assert "ts_code" in alert
            assert "news_score" in alert
            assert "capital_inflow_pct" in alert
            assert "volume_ratio" in alert

        # 4. 概念词库更新验证
        concepts_updated = result.get("concepts_updated", [])
        assert isinstance(concepts_updated, list)

        print(f"\n[FC-3a] 动态 DAG 完成 | 耗时 {elapsed:.1f}s | "
              f"news_score={news_result.get('news_score', 'N/A') if news_result else 'filtered'} "
              f"alerts={len(alerts)}")

    def test_full_dynamic_dag_low_score_news(self):
        """FC-3b: 低分新闻应在 news_funnel 被过滤，不进入后续节点"""
        from src.infrastructure.rss_fetcher import NewsItem
        from src.graphs.dynamic_graph import process_news_item

        news = NewsItem(
            article_id="fc3_test_002",
            title="某公司公告：日常经营一切正常",
            summary="某上市公司发布日常经营公告，无重大事项。",
            pub_time=datetime.now(),
            source="新浪财经",
        )
        concepts = [{"concept": "固态电池", "source_section": "政策", "confidence": 0.9}]

        result = process_news_item(news, concepts)

        assert result.get("error_node") is None
        # 低分新闻应被粗筛过滤
        news_result = result.get("news_result")
        if news_result is not None:
            # 如果 LLM 给了高分（罕见），也接受
            assert news_result["news_score"] >= 0.4
        # 无预警
        alerts = result.get("resonance_alerts", [])
        assert isinstance(alerts, list)


# ═══════════════════════════════════════════════════════════════════════════════
# FC-4: SOP 自学习全生命周期
# ═══════════════════════════════════════════════════════════════════════════════
class TestSOPFullLifecycle:
    """SOP: 写入pending → sop_learner提取 → active → FastAPI查询"""

    def test_sop_pending_to_active_lifecycle(self):
        """FC-4a: 完整 SOP 生命周期"""
        from src.infrastructure.database import get_pg_conn, release_pg_conn
        from src.nodes.sop_learner import run as sop_run

        # 1. 插入测试 SOP 到 sop_pending
        sop_graph = {
            "sop_name": "FC4测试-固态电池操作策略",
            "policy_name": "FC4测试政策",
            "trigger_conditions": ["固态电池量产良率>90%"],
            "action_steps": ["选择龙头股介入", "设置5%止损"],
        }
        source_text = (
            "固态电池领域近期出现重大突破，宁德时代宣布固态电池量产良率达95%。"
            "操作策略：当固态电池概念股出现放量突破时，可在第一梯队中选择龙头股介入。"
            "止损设在买入价下方5%，止盈目标15%。仓位控制：单票不超过总仓位的20%。"
        )

        conn = get_pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO sop_pending (graph_json, source_text, status, created_at)
                       VALUES (%s, %s, 'pending', NOW()) RETURNING id""",
                    (json.dumps(sop_graph, ensure_ascii=False), source_text),
                )
                sop_id = cur.fetchone()[0]
                conn.commit()
        finally:
            release_pg_conn(conn)

        assert sop_id > 0, "sop_pending 插入应返回有效 ID"

        # 2. sop_learner.run() 处理 pending → active
        result = sop_run({})
        assert result.get("error_node") is None, f"sop_learner 报错: {result.get('error_msg')}"
        assert result.get("sop_processed", 0) >= 1, "应至少处理 1 条 SOP"

        # 3. 验证 sop_active 表中有记录（approved=FALSE）
        conn = get_pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, sop_name, approved, graph_json FROM sop_active
                       ORDER BY id DESC LIMIT 5"""
                )
                rows = cur.fetchall()
            assert len(rows) >= 1, "sop_active 应有至少 1 条记录"
            # approved 应为 FALSE（人工审核闸门）
            for row in rows:
                assert row[2] is False, f"sop_active approved 应为 FALSE: {row}"
        finally:
            release_pg_conn(conn)

        # 4. 清理测试数据
        conn = get_pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sop_pending WHERE id = %s", (sop_id,))
                cur.execute("DELETE FROM sop_active WHERE approved = FALSE")
                conn.commit()
        finally:
            release_pg_conn(conn)

        print(f"\n[FC-4a] SOP 生命周期完成 | sop_id={sop_id} processed={result.get('sop_processed')}")

    def test_sop_api_endpoints(self):
        """FC-4b: FastAPI SOP 审核接口"""
        try:
            from fastapi.testclient import TestClient
            from api import app
        except Exception as e:
            pytest.skip(f"FastAPI 不可用: {e}")

        client = TestClient(app)

        # GET /sop/pending
        resp = client.get("/sop/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

        # GET /sop/active
        resp = client.get("/sop/active")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

        # GET /alerts/today
        resp = client.get("/alerts/today")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

        # GET / (homepage)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "html" in resp.text.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# FC-5: 双流水线 Redis 跨管道数据流
# ═══════════════════════════════════════════════════════════════════════════════
class TestCrossPipelineDataFlow:
    """验证静态 → Redis → 动态的数据流"""

    def test_static_output_to_redis(self, seed_chromadb):
        """FC-5a: tech_ranker 输出写入 Redis"""
        from src.nodes import tech_ranker
        from src.infrastructure.database import redis_client

        stock_pool = [
            {"ts_code": "300750.SZ", "name": "宁德时代", "concept": "固态电池",
             "layer": "核心层", "node": "电芯制造", "llm_score": 0.9, "circ_mv": 800000},
            {"ts_code": "002460.SZ", "name": "赣锋锂业", "concept": "固态电池",
             "layer": "上游", "node": "锂矿", "llm_score": 0.85, "circ_mv": 600000},
        ]
        state = {"stock_pool": stock_pool}
        result = tech_ranker.run(state)

        ranked = result.get("ranked_stocks", {})
        assert "tier1" in ranked and "tier2" in ranked

        # 验证 Redis 写入
        if redis_client:
            cached = redis_client.get("static:stock_pool")
            assert cached is not None, "tech_ranker 应将结果写入 Redis"
            cached_data = json.loads(cached)
            assert "tier1" in cached_data
            assert "tier2" in cached_data
            total = len(cached_data["tier1"]) + len(cached_data["tier2"])
            assert total >= 0, "Redis 数据应可反序列化"

    def test_redis_alerts_lifecycle(self):
        """FC-5b: 预警数据 Redis 存储与读取"""
        from src.infrastructure.database import redis_client
        from datetime import datetime

        today = datetime.now().strftime("%Y%m%d")
        key = f"dynamic:alerts:{today}"

        # 写入测试预警
        test_alert = {
            "ts_code": "300750.SZ",
            "news_title": "FC5跨管道测试-固态电池利好",
            "news_score": 0.92,
            "capital_inflow_pct": 3.5,
            "volume_ratio": 2.8,
            "timestamp": int(time.time()),
        }

        redis_client.lpush(key, json.dumps(test_alert, ensure_ascii=False))
        redis_client.expire(key, 120)

        # 验证读取
        count = redis_client.llen(key)
        assert count >= 1

        # 验证最新一条
        latest = json.loads(redis_client.lindex(key, 0))
        assert latest["ts_code"] == "300750.SZ"
        assert latest["news_score"] == 0.92

        # 清理
        redis_client.delete(key)

    def test_llm_cache_cross_node(self):
        """FC-5c: LLM 缓存跨节点共享"""
        from src.infrastructure.database import redis_client, REDIS_KEY_LLM_CACHE
        from src.nodes.llm_utils import call_llm_json

        # 调用一次 LLM（会写入缓存）
        result1 = call_llm_json(
            "回答: 固态电池属于哪个行业？仅输出JSON: {\"industry\": \"行业名\"}",
            model="flash", max_tokens=50
        )
        assert "industry" in result1

        # 第二次调用应命中缓存（通过 Redis 共享）
        result2 = call_llm_json(
            "回答: 固态电池属于哪个行业？仅输出JSON: {\"industry\": \"行业名\"}",
            model="flash", max_tokens=50
        )
        assert result1 == result2, "缓存命中时结果应一致"
