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
        """概念写入 dynamic:concepts + layer Set"""
        mock_redis.hget.return_value = None

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
