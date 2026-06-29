"""
test_concept_graph.py — 政策概念图谱构建引擎单元测试

覆盖：
  1. Layer0 提取格式验证
  2. BFS 扩展队列处理 + visited 去重 + 深度限制
  3. 收敛判断（relevance < 阈值停止）
  4. 模糊匹配（精确 / 子串 / 编辑距离）
  5. Redis 写入（edges / roots / layer Set）
  6. 增量插入定位
  7. 进度报告
  8. 并发锁保护
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ── 1. Layer0 提取 ────────────────────────────────────────────────────────────
class TestExtractLayer0:
    @patch("src.nodes.concept_graph_builder.call_llm_json")
    def test_basic_extraction(self, mock_llm):
        """LLM 返回有效概念列表"""
        mock_llm.return_value = [
            {"concept": "固态电池", "policy_basis": "推进固态电池技术", "importance": 9, "category": "新能源"},
            {"concept": "低空经济", "policy_basis": "打造低空经济", "importance": 8, "category": "高端制造"},
            {"concept": "", "importance": 5},  # 空概念应过滤
            {"importance": 7},  # 缺 concept 应过滤
        ]
        from src.nodes.concept_graph_builder import extract_layer0
        result = extract_layer0("政策文本测试")
        assert len(result) == 2
        assert result[0]["concept"] == "固态电池"
        assert result[1]["concept"] == "低空经济"

    @patch("src.nodes.concept_graph_builder.call_llm_json")
    def test_importance_sorting(self, mock_llm):
        """按 importance 降序排列"""
        mock_llm.return_value = [
            {"concept": "概念甲", "importance": 5, "category": "其他"},
            {"concept": "概念乙", "importance": 9, "category": "半导体"},
            {"concept": "概念丙", "importance": 7, "category": "新能源"},
        ]
        from src.nodes.concept_graph_builder import extract_layer0
        result = extract_layer0("测试")
        assert result[0]["concept"] == "概念乙"
        assert result[1]["concept"] == "概念丙"
        assert result[2]["concept"] == "概念甲"

    @patch("src.nodes.concept_graph_builder.call_llm_json")
    def test_non_list_response(self, mock_llm):
        """LLM 返回非数组时返回空列表"""
        mock_llm.return_value = {"error": "something"}
        from src.nodes.concept_graph_builder import extract_layer0
        result = extract_layer0("测试")
        assert result == []


# ── 2. 模糊匹配 ──────────────────────────────────────────────────────────────
class TestMatchExisting:
    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_exact_match(self, mock_redis):
        """精确匹配"""
        mock_redis.hkeys.return_value = ["固态电池", "低空经济", "量子计算"]
        from src.nodes.concept_graph_builder import _match_existing_concept
        assert _match_existing_concept("固态电池") == "固态电池"

    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_substring_match(self, mock_redis):
        """子串匹配"""
        mock_redis.hkeys.return_value = ["固态电池", "低空经济", "量子计算"]
        from src.nodes.concept_graph_builder import _match_existing_concept
        # "固态" 是 "固态电池" 的子串
        assert _match_existing_concept("固态") == "固态电池"

    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_no_match(self, mock_redis):
        """无匹配返回 None"""
        mock_redis.hkeys.return_value = ["固态电池", "低空经济"]
        from src.nodes.concept_graph_builder import _match_existing_concept
        assert _match_existing_concept("完全不同的概念") is None

    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_redis_none(self, mock_redis):
        """Redis 不可用时返回 None"""
        mock_redis_val = MagicMock()
        mock_redis_val.hkeys.side_effect = Exception("Redis down")
        mock_redis.__bool__ = lambda self: False
        from src.nodes.concept_graph_builder import _match_existing_concept
        # redis_client is None path
        import src.nodes.concept_graph_builder as cgb
        orig = cgb.redis_client
        cgb.redis_client = None
        try:
            assert _match_existing_concept("anything") is None
        finally:
            cgb.redis_client = orig


# ── 2b. 概念池加载 ───────────────────────────────────────────────────────────────
class TestLoadConceptPool:
    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_load_and_sort(self, mock_redis):
        """加载概念并按 policy_score 降序排列"""
        mock_redis.hgetall.return_value = {
            "固态电池": json.dumps({"stocks": ["000001.SZ"], "policy_score": 9, "category": "新能源"}),
            "低空经济": json.dumps({"stocks": ["000002.SZ"], "policy_score": 7, "category": "高端制造"}),
            "量子计算": json.dumps({"stocks": ["000003.SZ"], "policy_score": 10, "category": "半导体"}),
        }
        from src.nodes.concept_graph_builder import _load_concept_pool
        pool = _load_concept_pool()
        assert len(pool) == 3
        assert pool[0]["name"] == "量子计算"  # score=10 最高
        assert pool[1]["name"] == "固态电池"  # score=9
        assert pool[2]["name"] == "低空经济"  # score=7

    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_exclude_set(self, mock_redis):
        """排除已加入图谱的概念"""
        mock_redis.hgetall.return_value = {
            "固态电池": json.dumps({"stocks": ["000001.SZ"], "policy_score": 9}),
            "低空经济": json.dumps({"stocks": ["000002.SZ"], "policy_score": 7}),
        }
        from src.nodes.concept_graph_builder import _load_concept_pool
        pool = _load_concept_pool(exclude={"固态电池"})
        assert len(pool) == 1
        assert pool[0]["name"] == "低空经济"

    def test_redis_none(self):
        """Redis 不可用时返回空列表"""
        import src.nodes.concept_graph_builder as cgb
        orig = cgb.redis_client
        cgb.redis_client = None
        try:
            from src.nodes.concept_graph_builder import _load_concept_pool
            assert _load_concept_pool() == []
        finally:
            cgb.redis_client = orig

    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_zero_stock_concepts_included(self, mock_redis):
        """零股票概念仍加载入池（由后续补股机制处理）"""
        mock_redis.hgetall.return_value = {
            "固态电池": json.dumps({"stocks": ["000001.SZ"], "policy_score": 9}),
            "金刚石半导体": json.dumps({"stocks": [], "policy_score": 8}),
            "低空经济": json.dumps({"stocks": ["000002.SZ"], "policy_score": 7}),
        }
        from src.nodes.concept_graph_builder import _load_concept_pool
        pool = _load_concept_pool()
        names = [c["name"] for c in pool]
        assert "金刚石半导体" in names
        assert len(pool) == 3


# ── 2c. 候选概念粗筛 ───────────────────────────────────────────────────────────────
class TestFilterCandidates:
    def test_same_category_first(self):
        """同类别概念优先"""
        from src.nodes.concept_graph_builder import _filter_candidates
        pool = [
            {"name": "A", "category": "新能源", "policy_score": 5, "stock_count": 10},
            {"name": "B", "category": "半导体", "policy_score": 9, "stock_count": 20},
            {"name": "C", "category": "新能源", "policy_score": 3, "stock_count": 5},
        ]
        result = _filter_candidates(pool, "新能源", set())
        # 同类别 A,C 应在前面
        assert result[0]["name"] == "A"
        assert result[1]["name"] == "C"
        assert result[2]["name"] == "B"

    def test_max_candidates_limit(self):
        """截断到 max_candidates"""
        from src.nodes.concept_graph_builder import _filter_candidates
        pool = [{"name": f"C{i}", "category": "", "policy_score": 0, "stock_count": 1} for i in range(100)]
        result = _filter_candidates(pool, "", set(), max_candidates=10)
        assert len(result) == 10

    def test_exclude_visited(self):
        """排除已访问概念"""
        from src.nodes.concept_graph_builder import _filter_candidates
        pool = [
            {"name": "A", "category": "", "policy_score": 5, "stock_count": 10},
            {"name": "B", "category": "", "policy_score": 3, "stock_count": 5},
        ]
        result = _filter_candidates(pool, "", {"A"})
        assert len(result) == 1
        assert result[0]["name"] == "B"


# ── 3. 进度管理 ──────────────────────────────────────────────────────────────
class TestProgress:
    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_update_and_get_progress(self, mock_redis):
        """进度写入和读取"""
        stored = {}

        def fake_setex(key, ttl, val):
            stored[key] = val
        mock_redis.setex = fake_setex
        mock_redis.get = lambda key: stored.get(key)

        from src.nodes.concept_graph_builder import _update_progress, get_progress
        _update_progress("running", current_depth=2, discovered_count=45, current_concept="固态电池")
        progress = get_progress()
        assert progress["status"] == "running"
        assert progress["current_depth"] == 2
        assert progress["discovered_count"] == 45
        assert progress["current_concept"] == "固态电池"


# ── 4. Redis 写入 ────────────────────────────────────────────────────────────
class TestRedisWrite:
    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_write_concept(self, mock_redis):
        """概念写入 dynamic:concepts + layer Set（有成分股时）"""
        # 模拟已有概念带成分股
        mock_redis.hget.return_value = json.dumps({
            "stocks": ["300750.SZ"], "stocks_detail": {}, "sources": ["akshare_em"],
        })

        calls = {}
        mock_redis.hset = lambda h, k, v: calls.setdefault("hset", []).append((h, k, v))
        mock_redis.sadd = lambda s, v: calls.setdefault("sadd", []).append((s, v))
        mock_redis.expire = lambda k, t: None

        from src.nodes.concept_graph_builder import _write_concept_to_redis
        _write_concept_to_redis("固态电池", depth=1, parent_concepts=["新能源"],
                                policy_anchor="测试", expansion_status="expanded")

        assert "hset" in calls
        hset_args = calls["hset"][0]
        assert hset_args[0] == "dynamic:concepts"
        assert hset_args[1] == "固态电池"
        data = json.loads(hset_args[2])
        assert data["graph_depth"] == 1
        assert data["parent_concepts"] == ["新能源"]

        assert "sadd" in calls

    @patch("src.infrastructure.concept_sources.find_stocks_for_concept", return_value={})
    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_write_concept_no_stocks_skip_layer(self, mock_redis, mock_enrich):
        """三重来源均未找到股票时不加入 layer Set"""
        # 模拟无成分股的已有概念
        mock_redis.hget.return_value = json.dumps({
            "stocks": [], "stocks_detail": {}, "sources": ["llm"],
        })

        calls = {}
        mock_redis.hset = lambda h, k, v: calls.setdefault("hset", []).append((h, k, v))
        mock_redis.sadd = lambda s, v: calls.setdefault("sadd", []).append((s, v))
        mock_redis.expire = lambda k, t: None

        from src.nodes.concept_graph_builder import _write_concept_to_redis
        _write_concept_to_redis("金刚石半导体", depth=2, parent_concepts=["新材料"],
                                policy_anchor="测试", expansion_status="pending")

        # hset 应写入（保留元数据）
        assert "hset" in calls
        # 但 sadd 不应调用（无成分股不入 layer）
        assert "sadd" not in calls

    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_write_edge(self, mock_redis):
        """边写入 concept_graph:edges"""
        mock_redis.hget.return_value = None
        calls = {}
        mock_redis.hset = lambda h, k, v: calls.setdefault("hset", []).append((h, k, v))
        mock_redis.expire = lambda k, t: None

        from src.nodes.concept_graph_builder import _write_edge
        _write_edge("固态电池", "新能源", 0.9, "细分方向")

        assert "hset" in calls
        hset_args = calls["hset"][0]
        assert hset_args[0] == "concept_graph:edges"
        data = json.loads(hset_args[2])
        assert data["parents"][0]["name"] == "新能源"
        assert data["parents"][0]["relevance"] == 0.9


# ── 5. 增量插入 ──────────────────────────────────────────────────────────────
class TestIncrementalInsert:
    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_skip_when_no_graph(self, mock_redis):
        """图谱未构建时跳过"""
        mock_redis.scard.return_value = 0
        from src.nodes.concept_graph_builder import incremental_insert
        result = incremental_insert(["新概念"], None)
        assert result["status"] == "skipped"

    def test_skip_empty_terms(self):
        """空概念列表跳过"""
        from src.nodes.concept_graph_builder import incremental_insert
        result = incremental_insert([], None)
        assert result["status"] == "skipped"

    @patch("src.nodes.concept_graph_builder.call_llm_json")
    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_insert_with_parent(self, mock_redis, mock_llm):
        """有新概念且有父节点时正确插入"""
        mock_redis.scard.return_value = 5
        mock_redis.hkeys.return_value = ["固态电池", "新能源"]
        mock_redis.hget.return_value = json.dumps({"graph_depth": 0, "parent_concepts": []})
        mock_redis.hset = MagicMock()
        mock_redis.sadd = MagicMock()
        mock_redis.expire = MagicMock()
        mock_redis.smembers.return_value = set()

        mock_llm.return_value = [
            {"term": "硫化物电解质", "parent": "固态电池", "relevance": 0.88, "relation": "上游材料"},
        ]

        from src.nodes.concept_graph_builder import incremental_insert
        result = incremental_insert(["硫化物电解质"], None)
        assert result["inserted"] >= 1


# ── 6. 图谱树读取 ────────────────────────────────────────────────────────────
class TestGetGraphTree:
    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_empty_graph(self, mock_redis):
        """空图谱返回空结构"""
        mock_redis.smembers.return_value = set()
        from src.nodes.concept_graph_builder import get_graph_tree
        result = get_graph_tree()
        assert result["roots"] == []
        assert result["nodes"] == []
        assert result["total_nodes"] == 0


# ── 7. 并发锁 ────────────────────────────────────────────────────────────────
class TestBuildLock:
    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_lock_prevents_concurrent_build(self, mock_redis):
        """有锁时拒绝重复构建"""
        mock_redis.get.return_value = "1"  # 锁已存在
        from src.nodes.concept_graph_builder import build_full
        result = build_full()
        assert result["status"] == "already_running"


# ── 8. 概念股票语义增强 ────────────────────────────────────────────────────────────────
class TestEnrichConceptStocks:
    @patch("src.infrastructure.database.chroma_collection", None)
    def test_chroma_unavailable(self):
        """ChromaDB 不可用时返回原始数据"""
        from src.infrastructure.concept_sources import enrich_concept_stocks
        detail = {"000001.SZ": {"sources": ["akshare_em"], "name": "平安银行"}}
        result = enrich_concept_stocks("固态电池", detail)
        assert result == detail

    @patch("src.infrastructure.database.chroma_collection")
    def test_merge_and_score(self, mock_chroma):
        """合并 API 股票 + ChromaDB 股票并打分"""
        mock_chroma.query.return_value = {
            "ids": [["id1", "id2", "id3"]],
            "metadatas": [[
                {"ts_code": "300750.SZ", "name": "宁德时代"},
                {"ts_code": "000001.SZ", "name": "平安银行"},
                {"ts_code": "688005.SH", "name": "容百科技"},
            ]],
            "distances": [[0.1, 0.8, 0.3]],
            "documents": [["", "", ""]],
        }
        from src.infrastructure.concept_sources import enrich_concept_stocks
        detail = {
            "000001.SZ": {"sources": ["akshare_em"], "name": "平安银行"},
        }
        result = enrich_concept_stocks("固态电池", detail)

        # 结果应包含 3 只股票（1只原有 + 2只新增）
        assert len(result) == 3

        # 宁德时代 distance=0.1 → score=90，应排第一
        keys = list(result.keys())
        assert keys[0] == "300750.SZ"
        assert result["300750.SZ"]["semantic_score"] == 90
        assert "semantic" in result["300750.SZ"]["sources"]

        # 容百科技 distance=0.3 → score=70，应排第二
        assert keys[1] == "688005.SH"
        assert result["688005.SH"]["semantic_score"] == 70

        # 平安银行 distance=0.8 → score=20（已在板块，ChromaDB也召回）
        assert keys[2] == "000001.SZ"
        assert result["000001.SZ"]["semantic_score"] == 20
        assert "akshare_em" in result["000001.SZ"]["sources"]  # 保留原来源

    @patch("src.infrastructure.database.chroma_collection")
    def test_api_stock_not_in_chroma(self, mock_chroma):
        """API股票未被 ChromaDB 召回时给低分"""
        mock_chroma.query.return_value = {
            "ids": [["id1"]],
            "metadatas": [[{"ts_code": "300750.SZ", "name": "宁德时代"}]],
            "distances": [[0.15]],
            "documents": [[""]],
        }
        from src.infrastructure.concept_sources import enrich_concept_stocks
        detail = {
            "000002.SZ": {"sources": ["akshare_em"], "name": "万科A"},
        }
        result = enrich_concept_stocks("固态电池", detail)
        # 宁德时代 score=85 排第一，万科A score=5 排第二
        keys = list(result.keys())
        assert keys[0] == "300750.SZ"
        assert result["000002.SZ"]["semantic_score"] == 5

    @patch("src.infrastructure.database.chroma_collection")
    def test_empty_chroma_results(self, mock_chroma):
        """ChromaDB 返回空结果时返回原始数据"""
        mock_chroma.query.return_value = {"ids": [[]], "metadatas": [[]], "distances": [[]]}
        from src.infrastructure.concept_sources import enrich_concept_stocks
        detail = {"000001.SZ": {"sources": ["akshare_em"], "name": "平安银行"}}
        result = enrich_concept_stocks("固态电池", detail)
        assert result == detail


# ── 12. fetch_concept_stocks 重试 ─────────────────────────────────────────────
class TestFetchConceptStocks:
    @patch("src.infrastructure.concept_sources.time.sleep")
    @patch("src.infrastructure.concept_sources._em_code_to_ts_code", side_effect=lambda x: f"{x}.SZ")
    def test_retry_succeeds_on_third_attempt(self, mock_code, mock_sleep):
        """前2次失败第3次成功，应返回结果"""
        import pandas as pd
        from src.infrastructure.concept_sources import fetch_concept_stocks
        with patch("akshare.stock_board_concept_cons_em",
                   side_effect=[
                       Exception("Connection aborted"),
                       Exception("RemoteDisconnected"),
                       pd.DataFrame({"代码": ["000001", "300750"]}),
                   ]):
            result = fetch_concept_stocks("固态电池")
        assert result == ["000001.SZ", "300750.SZ"]
        assert mock_sleep.call_count == 2  # 重试2次

    @patch("src.infrastructure.concept_sources.time.sleep")
    def test_all_retries_fail(self, mock_sleep):
        """3次全失败应返回空列表"""
        from src.infrastructure.concept_sources import fetch_concept_stocks
        with patch("akshare.stock_board_concept_cons_em",
                   side_effect=Exception("Connection aborted")):
            result = fetch_concept_stocks("固态电池")
        assert result == []
        assert mock_sleep.call_count == 2  # 前2次重试，第3次不sleep


# ── 13. sync_concepts_to_redis 语义增强覆盖 ───────────────────────────────────
class TestSyncConceptsEnrichFallback:
    @patch("src.infrastructure.database.chroma_collection")
    def test_empty_stocks_still_enriched(self, mock_chroma):
        """akshare 返回空股票时，ChromaDB 语义增强仍执行"""
        mock_chroma.query.return_value = {
            "ids": [["id1", "id2"]],
            "metadatas": [[
                {"ts_code": "300750.SZ", "name": "宁德时代"},
                {"ts_code": "000001.SZ", "name": "平安银行"},
            ]],
            "distances": [[0.1, 0.3]],
            "documents": [["", ""]],
        }
        mock_redis = MagicMock()
        mock_redis.hget.return_value = None

        from main import sync_concepts_to_redis
        concepts = [{"concept": "固态电池", "stocks": [], "source": "akshare_em", "score": 8, "category": "新能源"}]
        sync_concepts_to_redis(mock_redis, "akshare_em", concepts)

        # 应调用 hset 写入 Redis（ChromaDB 补充了2只股票）
        mock_redis.hset.assert_called_once()
        call_args = mock_redis.hset.call_args
        assert call_args[0][0] == "dynamic:concepts"
        assert call_args[0][1] == "固态电池"
        payload = json.loads(call_args[0][2])
        assert len(payload["stocks"]) == 2
        assert "300750.SZ" in payload["stocks"]

    def test_both_empty_skip(self):
        """API 和 ChromaDB 都没找到股票时跳过"""
        mock_redis = MagicMock()
        mock_redis.hget.return_value = None

        from main import sync_concepts_to_redis
        concepts = [{"concept": "固态电池", "stocks": [], "source": "akshare_em", "score": 8, "category": "新能源"}]
        with patch("src.infrastructure.database.chroma_collection", None):
            sync_concepts_to_redis(mock_redis, "akshare_em", concepts)

        # 不应写入
        mock_redis.hset.assert_not_called()


# ── 14. 三重来源补股 ─────────────────────────────────────────────────────────
class TestFindStocksForConcept:
    @patch("src.infrastructure.concept_sources.enrich_concept_stocks", return_value={})
    @patch("src.infrastructure.concept_sources.fetch_concept_stocks")
    def test_tier1_akshare(self, mock_fetch, mock_enrich):
        """Tier1: akshare 成功则直接返回"""
        mock_fetch.return_value = ["000001.SZ", "300750.SZ"]
        mock_enrich.return_value = {
            "000001.SZ": {"sources": ["akshare_em"], "name": "", "semantic_score": 50},
            "300750.SZ": {"sources": ["akshare_em", "semantic"], "name": "宁德时代", "semantic_score": 90},
        }
        from src.infrastructure.concept_sources import find_stocks_for_concept
        result = find_stocks_for_concept("金刚石半导体")
        assert len(result) == 2
        assert "300750.SZ" in result

    @patch("src.infrastructure.concept_sources._web_search_stocks", return_value={})
    @patch("src.infrastructure.database.chroma_collection")
    @patch("src.infrastructure.concept_sources.fetch_concept_stocks", return_value=[])
    def test_tier2_chromadb(self, mock_fetch, mock_chroma, mock_web):
        """Tier2: akshare 失败时走 ChromaDB"""
        mock_chroma.query.return_value = {
            "ids": [["id1"]],
            "metadatas": [[{"ts_code": "300750.SZ", "name": "宁德时代"}]],
            "distances": [[0.2]],
        }
        from src.infrastructure.concept_sources import find_stocks_for_concept
        result = find_stocks_for_concept("金刚石半导体")
        assert len(result) == 1
        assert "300750.SZ" in result
        mock_web.assert_not_called()  # Tier3 不应被调用

    @patch("src.infrastructure.concept_sources._web_search_stocks")
    @patch("src.infrastructure.database.chroma_collection", None)
    @patch("src.infrastructure.concept_sources.fetch_concept_stocks", return_value=[])
    def test_tier3_web_search(self, mock_fetch, mock_web):
        """Tier3: akshare + ChromaDB 都失败时走 web_search"""
        mock_web.return_value = {
            "300750.SZ": {"sources": ["web_search"], "name": "宁德时代"},
        }
        from src.infrastructure.concept_sources import find_stocks_for_concept
        result = find_stocks_for_concept("金刚石半导体")
        assert len(result) == 1
        assert result["300750.SZ"]["name"] == "宁德时代"

    @patch("src.infrastructure.concept_sources._web_search_stocks", return_value={})
    @patch("src.infrastructure.database.chroma_collection", None)
    @patch("src.infrastructure.concept_sources.fetch_concept_stocks", return_value=[])
    def test_all_tiers_fail(self, mock_fetch, mock_web):
        """三重来源全部失败返回空 dict"""
        from src.infrastructure.concept_sources import find_stocks_for_concept
        result = find_stocks_for_concept("不存在概念")
        assert result == {}


# ── 15. _write_concept_to_redis 自动补股 ──────────────────────────────────────
class TestWriteConceptAutoEnrich:
    @patch("src.infrastructure.concept_sources.find_stocks_for_concept")
    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_auto_enrich_on_zero_stocks(self, mock_redis, mock_find):
        """零股票概念写入时自动触发三重补股"""
        mock_redis.hget.return_value = json.dumps({
            "stocks": [], "stocks_detail": {}, "sources": ["llm"],
        })
        mock_find.return_value = {
            "300750.SZ": {"sources": ["semantic"], "name": "宁德时代", "semantic_score": 85},
            "000001.SZ": {"sources": ["semantic"], "name": "平安银行", "semantic_score": 70},
        }

        calls = {}
        mock_redis.hset = lambda h, k, v: calls.setdefault("hset", []).append((h, k, v))
        mock_redis.sadd = lambda s, v: calls.setdefault("sadd", []).append((s, v))
        mock_redis.expire = lambda k, t: None

        from src.nodes.concept_graph_builder import _write_concept_to_redis
        _write_concept_to_redis("金刚石半导体", depth=2, parent_concepts=["新材料"])

        # 补股后应有 stocks
        hset_data = json.loads(calls["hset"][0][2])
        assert len(hset_data["stocks"]) == 2
        assert "300750.SZ" in hset_data["stocks"]

        # 有股票后应加入 layer Set
        assert "sadd" in calls

    @patch("src.infrastructure.concept_sources.find_stocks_for_concept", return_value={})
    @patch("src.nodes.concept_graph_builder.redis_client")
    def test_auto_enrich_fail_skip_layer(self, mock_redis, mock_find):
        """补股失败时不加入 layer Set"""
        mock_redis.hget.return_value = json.dumps({
            "stocks": [], "stocks_detail": {}, "sources": ["llm"],
        })

        calls = {}
        mock_redis.hset = lambda h, k, v: calls.setdefault("hset", []).append((h, k, v))
        mock_redis.sadd = lambda s, v: calls.setdefault("sadd", []).append((s, v))
        mock_redis.expire = lambda k, t: None

        from src.nodes.concept_graph_builder import _write_concept_to_redis
        _write_concept_to_redis("金刚石半导体", depth=2, parent_concepts=["新材料"])

        assert "hset" in calls
        assert "sadd" not in calls  # 无股票不入 layer
