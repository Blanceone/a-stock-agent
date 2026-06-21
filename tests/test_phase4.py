"""
test_phase4.py — Phase 4 验收测试（SOP 自学习）

覆盖：
  1. sop_learner 结构化提取
  2. sop_pending/sop_active 表写入逻辑
  3. 人工审核闸门（approved=FALSE）
  4. Prompt 模板完整性
  5. LLM 工具模块
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call

import pytest


# ── 1. SOP 结构化提取 ────────────────────────────────────────────────────────
class TestSOPExtractor:
    @patch("src.nodes.sop_learner.call_llm_json")
    def test_extract_sop_graph(self, mock_llm):
        """V4-Flash 提取 SOP 图谱"""
        mock_llm.return_value = {
            "sop_name": "固态电池利好操作策略",
            "policy_name": "新能源发展规划",
            "trigger_conditions": ["固态电池量产良率>90%"],
            "action_steps": ["买入相关个股", "设置止盈止损"],
            "risk_controls": ["单票仓位不超过20%"],
        }
        from src.nodes.sop_learner import _extract_sop_graph
        result = _extract_sop_graph("固态电池领域将实现重大突破...")
        assert "sop_name" in result
        assert "trigger_conditions" in result

    def test_sop_graph_schema(self):
        """SOP 图谱 schema 完整"""
        graph = {
            "sop_name": "测试SOP",
            "policy_name": "测试政策",
            "trigger_conditions": ["条件1"],
            "action_steps": ["步骤1"],
            "risk_controls": ["风控1"],
        }
        assert graph["sop_name"] == "测试SOP"
        assert len(graph["trigger_conditions"]) > 0


# ── 2. sop_pending 写入 ──────────────────────────────────────────────────────
class TestSOPPending:
    @patch("src.nodes.sop_learner.get_pg_conn")
    @patch("src.nodes.sop_learner.release_pg_conn")
    def test_write_to_active(self, mock_release, mock_get):
        """写入 sop_active 表（approved=FALSE）"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get.return_value = mock_conn

        from src.nodes.sop_learner import _write_to_active
        sop_graph = {"sop_name": "测试SOP", "policy_name": "测试政策"}
        _write_to_active(1, sop_graph)

        # 验证 SQL 中包含 approved=FALSE
        mock_cursor.execute.assert_called_once()
        sql_args = mock_cursor.execute.call_args
        assert "FALSE" in sql_args[0][0]


# ── 3. 人工审核闸门 ──────────────────────────────────────────────────────────
class TestApprovalGate:
    def test_sop_active_approved_default_false(self):
        """sop_active 表 approved 默认 FALSE"""
        from src.infrastructure.database import _DDL
        # DDL 中应有 DEFAULT FALSE
        assert "DEFAULT FALSE" in _DDL

    def test_sop_pending_status_field(self):
        """sop_pending 有 status 字段"""
        from src.infrastructure.database import _DDL
        assert "status" in _DDL
        assert "pending" in _DDL


# ── 4. Prompt 模板完整性 ─────────────────────────────────────────────────────
class TestPrompts:
    def test_all_prompts_exist(self, project_root):
        """所有 Prompt 模板文件存在"""
        prompts_dir = project_root / "config" / "prompts"
        required = [
            "policy_parser.txt",
            "chain_splitter.txt",
            "entity_mapper.txt",
            "news_coarse.txt",
            "news_deep.txt",
            "sop_extractor.txt",
        ]
        for name in required:
            path = prompts_dir / name
            assert path.exists(), f"Prompt 文件缺失: {name}"
            content = path.read_text(encoding="utf-8")
            assert len(content) > 50, f"Prompt 内容过短: {name}"

    def test_policy_parser_prompt_has_separator(self, project_root):
        """policy_parser prompt 有 --- 分隔符（两段）"""
        path = project_root / "config" / "prompts" / "policy_parser.txt"
        content = path.read_text(encoding="utf-8")
        assert "---" in content


# ── 5. LLM 工具模块 ──────────────────────────────────────────────────────────
class TestLLMUtils:
    def test_load_prompt(self, project_root):
        """load_prompt 能加载模板"""
        from src.nodes.llm_utils import load_prompt
        content = load_prompt("news_coarse")
        assert len(content) > 50
        assert "news_title" in content or "新闻" in content

    def test_cache_key_deterministic(self):
        """相同 messages 生成相同 cache key"""
        from src.nodes.llm_utils import _cache_key
        msgs = [{"role": "user", "content": "test"}]
        key1 = _cache_key(msgs)
        key2 = _cache_key(msgs)
        assert key1 == key2

    def test_cache_key_different_for_different_messages(self):
        """不同 messages 生成不同 cache key"""
        from src.nodes.llm_utils import _cache_key
        msgs1 = [{"role": "user", "content": "test1"}]
        msgs2 = [{"role": "user", "content": "test2"}]
        assert _cache_key(msgs1) != _cache_key(msgs2)

    @patch("src.nodes.llm_utils._get_client")
    @patch("src.nodes.llm_utils._get_cached")
    @patch("src.nodes.llm_utils._set_cache")
    def test_call_llm_uses_cache(self, mock_set, mock_get, mock_client):
        """call_llm 缓存命中时不调用 API"""
        mock_get.return_value = "cached result"
        from src.nodes.llm_utils import call_llm
        result = call_llm("test prompt")
        assert result == "cached result"
        mock_client.assert_not_called()

    def test_call_llm_json_strips_markdown(self):
        """call_llm_json 能正确去除 markdown fence"""
        raw = '```json\n{"key": "value"}\n```'
        # 模拟 call_llm 返回带 fence 的 JSON
        with patch("src.nodes.llm_utils.call_llm", return_value=raw), \
             patch("src.nodes.llm_utils._get_cached", return_value=None), \
             patch("src.nodes.llm_utils._set_cache"):
            from src.nodes.llm_utils import call_llm_json
            result = call_llm_json("test")
            assert result == {"key": "value"}


# ── 6. Redis Key 规范 ────────────────────────────────────────────────────────
class TestRedisKeys:
    def test_all_key_prefixes_defined(self):
        """所有 Redis Key 前缀已定义"""
        from src.infrastructure.database import (
            REDIS_KEY_NEWS_DEDUP,
            REDIS_KEY_URL_DEDUP,
            REDIS_KEY_LLM_CACHE,
            REDIS_KEY_RATE_LIMIT,
            REDIS_KEY_NEXT_WEIGHTS,
        )
        assert REDIS_KEY_NEWS_DEDUP.startswith("dedup:")
        assert REDIS_KEY_URL_DEDUP.startswith("dedup:")
        assert REDIS_KEY_LLM_CACHE.startswith("llm:")
        assert REDIS_KEY_RATE_LIMIT.startswith("rate:")
        assert REDIS_KEY_NEXT_WEIGHTS.startswith("weights:")
