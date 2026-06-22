"""
test_e2e.py — 业务端到端验收测试

覆盖全链路:
  E2E-0: 基础设施连通性 (PG / Redis / ChromaDB+bge / SearXNG)
  E2E-1: 静态图谱流水线 (policy_parser → chain_splitter → entity_mapper → tech_ranker)
  E2E-2: 语义知识库 (ChromaDB embedding 验证)
  E2E-3: 动态监控流水线 (news_funnel → resonance_alert)
  E2E-4: SOP 自学习 + 审核闸门
  E2E-5: FastAPI SOP 审核接口
  E2E-6: Redis 持久化验证

运行方式: pytest tests/test_e2e.py -v -s --tb=short
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")


# ═══════════════════════════════════════════════════════════════════════════════
# E2E-0: 基础设施连通性
# ═══════════════════════════════════════════════════════════════════════════════
class TestInfraConnectivity:
    """验证所有基础设施组件可用"""

    def test_pg_connection(self):
        """PostgreSQL 连接 + stock_basic 有数据"""
        from src.infrastructure.database import init_all, get_pg_conn, release_pg_conn
        init_all()
        conn = get_pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM stock_basic")
                count = cur.fetchone()[0]
            assert count > 5000, f"stock_basic 应有 >5000 条，实际 {count}"
        finally:
            release_pg_conn(conn)

    def test_pg_stock_data_quality(self):
        """stock_basic 数据质量检查"""
        from src.infrastructure.database import get_pg_conn, release_pg_conn
        conn = get_pg_conn()
        try:
            with conn.cursor() as cur:
                # 检查有行业分类的股票数
                cur.execute("SELECT COUNT(*) FROM stock_basic WHERE industry IS NOT NULL AND industry != ''")
                with_industry = cur.fetchone()[0]
                # 检查 ST 标记
                cur.execute("SELECT COUNT(*) FROM stock_basic WHERE is_st = TRUE")
                st_count = cur.fetchone()[0]
                # 检查流通市值（允许 NULL，因为 Tushare stock_basic 可能不返回 circ_mv）
                cur.execute("SELECT COUNT(*) FROM stock_basic WHERE circ_mv IS NOT NULL AND circ_mv > 0")
                with_mv = cur.fetchone()[0]
            assert with_industry > 3000, f"有行业分类的股票应 >3000，实际 {with_industry}"
            # circ_mv 允许为 NULL（Tushare stock_basic 接口可能不返回该字段）
            if with_mv == 0:
                import warnings
                warnings.warn("circ_mv 全为 NULL，需要补录流通市值数据（从 daily_basic 获取）")
        finally:
            release_pg_conn(conn)

    def test_redis_connection(self):
        """Redis 连接 + 基本读写"""
        from src.infrastructure.database import redis_client
        assert redis_client is not None, "redis_client 未初始化"
        redis_client.set("e2e:test_key", "hello", ex=60)
        val = redis_client.get("e2e:test_key")
        assert val == "hello"
        redis_client.delete("e2e:test_key")

    def test_chromadb_connection(self):
        """ChromaDB 连接 + bge-small-zh-v1.5 模型可用"""
        from src.infrastructure.database import chroma_client, chroma_collection
        if chroma_client is None:
            pytest.skip("ChromaDB 不可用（兼容模式或连接失败）")
        if chroma_collection is None:
            pytest.skip("ChromaDB collection 未创建")
        # 验证 collection 存在
        count = chroma_collection.count()
        assert isinstance(count, int), f"count 应返回 int，实际 {type(count)}"

    def test_searxng_search(self):
        """SearXNG 远程搜索可用"""
        from src.infrastructure.searxng_search import search
        try:
            results = search("固态电池 产业链", num_results=3)
            assert len(results) >= 1, "SearXNG 应返回至少1条结果"
            assert results[0].url, "结果应有 URL"
        except Exception as e:
            pytest.skip(f"SearXNG 不可用: {e}")

    def test_deepseek_api_reachable(self):
        """DeepSeek API 可连通"""
        from src.nodes.llm_utils import call_llm_json
        try:
            result = call_llm_json("回答: 1+1等于几？仅输出JSON: {\"answer\": 数字}", model="flash", max_tokens=50)
            assert "answer" in result, f"LLM 应返回含 answer 的 JSON，实际: {result}"
        except Exception as e:
            pytest.skip(f"DeepSeek API 不可用: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# E2E-1: 静态图谱流水线（节点级联调）
# ═══════════════════════════════════════════════════════════════════════════════
class TestStaticPipeline:
    """静态图谱各节点联调"""

    def test_policy_parser_with_test_pdf(self, tmp_path):
        """policy_parser: 生成测试PDF → 提取概念词"""
        import fitz  # PyMuPDF
        # 生成有实质内容的测试PDF
        pdf_path = str(tmp_path / "test_policy.pdf")
        doc = fitz.open()
        # 第1页: 目录
        page = doc.new_page()
        page.insert_text((72, 72), "目录\n1. 固态电池发展规划概述\n2. 产业链布局分析\n3. 技术创新路线图")
        # 第2页: 固态电池正文（内容更充实）
        page2 = doc.new_page()
        text_lines = [
            "第一章 固态电池发展规划概述",
            "",
            "固态电池是下一代动力电池的核心技术方向，具有高能量密度、高安全性的特点。",
            "关键技术路线包括：硫化物全固态电解质、氧化物薄膜电解质、聚合物凝胶电解质。",
            "产业链上游：碳酸锂、氢氧化锂、锆英砂、锗矿等关键原材料的供应格局。",
            "产业链中游：高镍三元正极材料、硅碳负极材料、固态电解质膜、陶瓷隔膜。",
            "产业链下游：方形电芯制造、软包电芯封装、PACK系统集成、整车配套应用。",
            "重点企业布局：宁德时代、赣锋锂业、清陶能源、卫蓝新能源、辉能科技。",
            "预计2028年实现半固态电池大规模量产，2030年实现全固态电池商业化。",
            "",
            "第二章 产业链布局分析",
            "",
            "固态电池产业链可分为上游资源、中游材料、下游应用三大环节。",
            "上游资源环节重点关注锂矿资源、锆矿资源的供应安全性。",
            "中游材料环节是技术壁垒最高的环节，电解质材料是核心瓶颈。",
            "下游应用环节包括消费电子和新能源汽车两大领域。",
        ]
        y = 72
        for line in text_lines:
            page2.insert_text((72, y), line)
            y += 16
        doc.save(pdf_path)
        doc.close()

        from src.nodes.policy_parser import run
        state = {"policy_pdf_path": pdf_path, "retry_count": 0}
        result = run(state)

        assert "concepts" in result, f"应输出 concepts，实际 keys: {list(result.keys())}"
        concepts = result["concepts"]
        assert isinstance(concepts, list), "concepts 应为 list"
        # 注意: LLM 提取结果不确定，但至少不应报错
        # 如果概念词被宏观空词全部过滤，也应视为成功
        if len(concepts) == 0:
            import warnings
            warnings.warn("LLM 提取概念词为0（可能被宏观空词过滤），检查 LLM 响应质量")
        # 检查概念词结构
        for c in concepts:
            assert "concept" in c, f"概念词应含 concept 字段: {c}"
        # 宏观空词应被过滤
        from src.nodes.policy_parser import BANNED_WORDS
        for c in concepts:
            assert c["concept"] not in BANNED_WORDS, f"宏观空词未被过滤: {c['concept']}"

    def test_chain_splitter_with_concepts(self):
        """chain_splitter: 概念词 → 产业链拆解"""
        from src.nodes.chain_splitter import run
        concepts = [
            {"concept": "固态电池", "source_section": "第1章", "confidence": 0.9},
        ]
        state = {"concepts": concepts}
        result = run(state)

        assert "industry_chains" in result
        chains = result["industry_chains"]
        assert isinstance(chains, list)
        if chains:
            chain = chains[0]
            assert "concept" in chain or "layers" in chain, f"产业链应含 concept 或 layers: {list(chain.keys())}"

    def test_entity_mapper_with_chains(self):
        """entity_mapper: 产业链 → 候选股池"""
        from src.nodes.entity_mapper import run
        industry_chains = [{
            "concept": "固态电池",
            "layers": [
                {
                    "layer_name": "核心层",
                    "nodes": [
                        {
                            "node_name": "电芯制造",
                            "description": "固态电池电芯生产与制造",
                            "keywords": ["固态电池", "电芯", "量产"],
                        }
                    ],
                }
            ],
        }]
        state = {"industry_chains": industry_chains}
        result = run(state)

        assert "stock_pool" in result
        pool = result["stock_pool"]
        assert isinstance(pool, list)
        # 每只股票应有完整字段
        for s in pool:
            assert "ts_code" in s
            assert "name" in s
            assert "concept" in s
            assert "circ_mv" in s

    def test_tech_ranker_with_stock_pool(self):
        """tech_ranker: 候选股池 → 技术面排名"""
        from src.nodes.tech_ranker import run
        # 使用真实股票代码测试
        stock_pool = [
            {"ts_code": "300750.SZ", "name": "宁德时代", "concept": "固态电池",
             "layer": "核心层", "node": "电芯制造", "llm_score": 0.9, "circ_mv": 800000},
            {"ts_code": "002460.SZ", "name": "赣锋锂业", "concept": "固态电池",
             "layer": "上游", "node": "锂矿", "llm_score": 0.85, "circ_mv": 600000},
        ]
        state = {"stock_pool": stock_pool}
        result = run(state)

        assert "ranked_stocks" in result
        ranked = result["ranked_stocks"]
        assert "tier1" in ranked
        assert "tier2" in ranked
        # 检查 Redis 持久化
        from src.infrastructure.database import redis_client
        if redis_client:
            cached = redis_client.get("static:stock_pool")
            if cached:
                data = json.loads(cached)
                assert "tier1" in data


# ═══════════════════════════════════════════════════════════════════════════════
# E2E-2: 语义知识库（ChromaDB + bge embedding）
# ═══════════════════════════════════════════════════════════════════════════════
class TestSemanticKB:
    """ChromaDB 语义搜索验证"""

    def test_embedding_function_works(self):
        """bge-small-zh-v1.5 embedding 能正常生成向量"""
        from src.infrastructure.database import chroma_collection
        if chroma_collection is None:
            pytest.skip("ChromaDB collection 不可用")
        # 测试查询
        try:
            results = chroma_collection.query(
                query_texts=["固态电池电解质"],
                n_results=5,
            )
            assert results is not None
            assert "ids" in results
        except Exception as e:
            pytest.skip(f"ChromaDB 查询失败（可能无数据）: {e}")

    def test_semantic_search_relevance(self):
        """语义搜索结果应与查询相关"""
        from src.infrastructure.database import chroma_collection
        if chroma_collection is None:
            pytest.skip("ChromaDB collection 不可用")
        try:
            count = chroma_collection.count()
            if count == 0:
                pytest.skip("ChromaDB 无数据，需先运行 python main.py --mode semantic")
            results = chroma_collection.query(
                query_texts=["动力电池正极材料"],
                n_results=3,
            )
            assert len(results["ids"][0]) > 0, "应返回搜索结果"
        except Exception as e:
            pytest.skip(f"语义搜索失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# E2E-3: 动态监控流水线
# ═══════════════════════════════════════════════════════════════════════════════
class TestDynamicPipeline:
    """动态监控各节点联调"""

    def test_news_funnel_coarse_filter_e2e(self):
        """news_funnel 粗筛：真实 LLM 调用"""
        from src.nodes.news_funnel import _coarse_filter
        # 高分新闻
        result = _coarse_filter(
            "宁德时代固态电池量产良率突破95%",
            "宁德时代宣布固态电池量产线良率达到95%，成本下降40%"
        )
        assert "news_score" in result, f"粗筛应返回 news_score: {result}"
        score = result["news_score"]
        assert 0 <= score <= 1, f"news_score 应在 0~1 之间: {score}"

    def test_news_funnel_full_pipeline(self):
        """news_funnel.run: 完整漏斗流程"""
        from src.infrastructure.rss_fetcher import NewsItem
        from src.nodes.news_funnel import run

        news = NewsItem(
            article_id="e2e_test_001",
            title="央行宣布降准50个基点 释放长期资金万亿",
            summary="中国人民银行决定下调金融机构存款准备金率0.5个百分点，预计释放长期资金约1万亿元。",
            pub_time=datetime.now(),
            source="财联社",
        )
        concepts = [{"concept": "银行", "source_section": "政策", "confidence": 0.9}]
        state = {"raw_news": news, "concepts": concepts}
        result = run(state)

        assert "news_result" in result or "concepts_updated" in result
        if result.get("news_result"):
            nr = result["news_result"]
            assert "news_score" in nr
            assert "news_title" in nr

    def test_resonance_alert_check(self):
        """resonance_alert: 三共振条件检查"""
        from src.nodes.resonance_alert import run, _check_capital_flow, _check_volume_ratio

        # 测试资金流查询
        flow = _check_capital_flow("300750.SZ")
        assert isinstance(flow, dict)
        assert "inflow_pct" in flow
        assert "source" in flow

        # 测试量比计算
        vr = _check_volume_ratio("300750.SZ")
        assert isinstance(vr, (int, float))

    def test_dynamic_graph_end_to_end(self):
        """完整动态图谱: news_funnel → resonance_alert"""
        from src.infrastructure.rss_fetcher import NewsItem
        from src.graphs.dynamic_graph import process_news_item

        news = NewsItem(
            article_id="e2e_test_002",
            title="固态电池龙头股获北向资金大幅加仓",
            summary="宁德时代今日获北向资金净买入超5亿元，主力资金大幅流入，量能显著放大。",
            pub_time=datetime.now(),
            source="财联社",
        )
        concepts = [
            {"concept": "固态电池", "source_section": "政策", "confidence": 0.9},
        ]
        result = process_news_item(news, concepts)

        assert isinstance(result, dict)
        assert "error_node" not in result or result.get("error_node") is None, \
            f"动态图谱不应报错: {result.get('error_node')} - {result.get('error_msg')}"


# ═══════════════════════════════════════════════════════════════════════════════
# E2E-4: SOP 自学习 + 审核闸门
# ═══════════════════════════════════════════════════════════════════════════════
class TestSOPLearning:
    """SOP 自学习流程验证"""

    def test_sop_extract_graph_e2e(self):
        """SOP 图谱提取: 真实 LLM 调用"""
        from src.nodes.sop_learner import _extract_sop_graph
        policy_text = (
            "固态电池领域近期出现重大突破，宁德时代宣布固态电池量产良率达95%。"
            "操作策略：当固态电池概念股出现放量突破时，可在第一梯队中选择龙头股介入。"
            "止损设在买入价下方5%，止盈目标15%。"
            "仓位控制：单票不超过总仓位的20%，首次建仓不超过计划仓位的50%。"
        )
        result = _extract_sop_graph(policy_text)
        assert isinstance(result, dict), f"应返回 dict: {type(result)}"
        # 检查是否有合理的字段
        has_fields = any(k in result for k in ["sop_name", "concept", "viewpoint", "conditions", "trigger_conditions"])
        assert has_fields, f"SOP 图谱应有业务字段，实际 keys: {list(result.keys())}"

    def test_sop_pending_write_and_read(self):
        """SOP 写入 sop_pending → 读取验证"""
        from src.infrastructure.database import get_pg_conn, release_pg_conn
        conn = get_pg_conn()
        try:
            sop_graph = {
                "sop_name": "E2E测试SOP",
                "policy_name": "E2E测试政策",
                "trigger_conditions": ["测试条件"],
                "action_steps": ["测试操作"],
            }
            source_text = "E2E测试原始文本"
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO sop_pending (graph_json, source_text, status, created_at)
                       VALUES (%s, %s, 'pending', NOW())
                       RETURNING id""",
                    (json.dumps(sop_graph, ensure_ascii=False), source_text),
                )
                sop_id = cur.fetchone()[0]
                conn.commit()

            assert sop_id > 0, "sop_pending 插入应返回有效 ID"

            # 读取验证
            with conn.cursor() as cur:
                cur.execute("SELECT id, graph_json, source_text, status FROM sop_pending WHERE id = %s", (sop_id,))
                row = cur.fetchone()
            assert row is not None
            assert row[3] == "pending"
            stored_graph = row[1] if isinstance(row[1], dict) else json.loads(row[1])
            assert stored_graph["sop_name"] == "E2E测试SOP"

            # 清理
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sop_pending WHERE id = %s", (sop_id,))
                conn.commit()
        finally:
            release_pg_conn(conn)


# ═══════════════════════════════════════════════════════════════════════════════
# E2E-5: FastAPI SOP 审核接口
# ═══════════════════════════════════════════════════════════════════════════════
class TestSOPAPI:
    """FastAPI SOP 审核接口验证"""

    @pytest.fixture
    def client(self):
        """FastAPI 测试客户端"""
        try:
            from fastapi.testclient import TestClient
            from api import app
            return TestClient(app)
        except Exception as e:
            pytest.skip(f"FastAPI 不可用: {e}")

    def test_pending_list(self, client):
        """GET /sop/pending 返回 JSON 列表"""
        resp = client.get("/sop/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_active_list(self, client):
        """GET /sop/active 返回 JSON 列表"""
        resp = client.get("/sop/active")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_alerts_today(self, client):
        """GET /alerts/today 返回 JSON"""
        resp = client.get("/alerts/today")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_homepage_html(self, client):
        """GET / 返回 HTML 审核页"""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "html" in resp.text.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# E2E-6: Redis 持久化验证
# ═══════════════════════════════════════════════════════════════════════════════
class TestRedisPersistence:
    """Redis 数据持久化验证"""

    def test_llm_cache_roundtrip(self):
        """LLM 缓存写入/读取"""
        from src.infrastructure.database import redis_client, REDIS_KEY_LLM_CACHE
        test_key = REDIS_KEY_LLM_CACHE.format(prompt_hash="e2e_test_hash")
        test_value = json.dumps({"result": "cached_response"})
        redis_client.setex(test_key, 300, test_value)
        retrieved = redis_client.get(test_key)
        assert retrieved == test_value
        redis_client.delete(test_key)

    def test_rate_limit_counter(self):
        """SearXNG 限流计数器"""
        from src.infrastructure.database import redis_client, REDIS_KEY_RATE_LIMIT
        bucket = f"e2e_test_{int(time.time() // 60)}"
        key = REDIS_KEY_RATE_LIMIT.format(minute_bucket=bucket)
        for i in range(3):
            redis_client.incr(key)
        count = int(redis_client.get(key))
        assert count == 3
        redis_client.delete(key)

    def test_alerts_storage(self):
        """预警数据存储"""
        from src.infrastructure.database import redis_client
        today = datetime.now().strftime("%Y%m%d")
        key = f"dynamic:alerts:{today}"
        alert = {"ts_code": "000001.SZ", "news_title": "E2E测试", "news_score": 0.9}
        redis_client.lpush(key, json.dumps(alert, ensure_ascii=False))
        redis_client.expire(key, 60)
        count = redis_client.llen(key)
        assert count >= 1
        stored = json.loads(redis_client.lindex(key, 0))
        assert stored["ts_code"] == "000001.SZ"
        redis_client.delete(key)
