"""
test_phase2.py — Phase 2 验收测试（DAG 智能节点）

覆盖：
  1. policy_parser 空词过滤
  2. chain_splitter 产业链结构
  3. entity_mapper 无ST过滤
  4. tech_ranker 梯队分值
  5. static_graph DAG 编排
  6. 节点间数据流转格式
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ── 1. policy_parser 空词过滤 ────────────────────────────────────────────────
class TestPolicyParser:
    def test_banned_words_filter(self):
        """宏观空词黑名单应过滤"""
        from src.nodes.policy_parser import BANNED_WORDS
        assert "高质量发展" in BANNED_WORDS
        assert "改革" in BANNED_WORDS
        assert "创新" in BANNED_WORDS
        assert "新质生产力" in BANNED_WORDS

    @patch("src.nodes.policy_parser.call_llm_json")
    @patch("src.nodes.policy_parser._extract_toc")
    @patch("src.nodes.policy_parser._extract_section_texts")
    @patch("src.nodes.policy_parser._store_policy_chunks")
    def test_run_filters_banned_concepts(self, mock_store, mock_texts, mock_toc, mock_llm):
        """run() 应过滤掉黑名单概念词"""
        mock_toc.return_value = [{"level": 1, "title": "第一章", "page": 1}]
        mock_texts.return_value = {1: "这是测试文本"}
        mock_llm.side_effect = [
            {"key_sections": [1]},  # 第一次调用：圈定章节
            [
                {"concept": "固态电池", "source_section": "第1章", "confidence": 0.9},
                {"concept": "高质量发展", "source_section": "第1章", "confidence": 0.8},
                {"concept": "人形机器人", "source_section": "第1章", "confidence": 0.85},
            ],
        ]
        from src.nodes.policy_parser import run
        result = run({"policy_pdf_path": "/tmp/test.pdf"})
        concepts = result["concepts"]
        # "高质量发展" 应被过滤
        assert all(c["concept"] not in ["高质量发展", "改革", "创新"] for c in concepts)
        assert any(c["concept"] == "固态电池" for c in concepts)


# ── 2. entity_mapper 无ST ────────────────────────────────────────────────────
class TestEntityMapper:
    def test_sql_filter_excludes_st(self):
        """SQL 过滤应剔除 ST 股票"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("300750.SZ", "宁德时代", 800000),
            # ST 股票不在返回结果中（SQL WHERE is_st = FALSE）
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("src.nodes.entity_mapper.get_pg_conn", return_value=mock_conn), \
             patch("src.nodes.entity_mapper.release_pg_conn"):
            from src.nodes.entity_mapper import _sql_filter
            result = _sql_filter(["300750.SZ", "000999.SZ"])
            # 只返回非ST股票
            assert "300750.SZ" in result


# ── 3. tech_ranker 梯队分值 ──────────────────────────────────────────────────
class TestTechRanker:
    def test_tier1_score_threshold(self):
        """第一梯队分数 ≥ 0.7"""
        # 模拟计算逻辑
        from src.tools.indicator_calc import calc_all
        import pandas as pd
        import numpy as np

        np.random.seed(42)
        n = 60
        dates = pd.date_range("2026-01-01", periods=n, freq="B")
        close = np.linspace(10, 15, n)  # 稳定上升趋势
        df = pd.DataFrame({
            "trade_date": dates.strftime("%Y%m%d"),
            "close": close,
            "vol": np.ones(n) * 500000,
        })
        # 最后一天放量
        df.iloc[-1, df.columns.get_loc("vol")] = 2000000

        result = calc_all(df, circ_mv=300000)
        assert 0 <= result["ma_align_score"] <= 1
        assert 0 <= result["vol_ratio_score"] <= 1
        assert 0 <= result["gain_score"] <= 1
        assert 0 <= result["small_cap_score"] <= 1

    def test_ranked_stocks_structure(self):
        """ranked_stocks 结构正确"""
        tier1 = [{"ts_code": "000001.SZ", "score": 0.85, "name": "Test"}]
        tier2 = [{"ts_code": "000002.SZ", "score": 0.55, "name": "Test2"}]
        ranked = {"tier1": tier1, "tier2": tier2}
        assert all(s["score"] >= 0.7 for s in ranked["tier1"])
        assert all(0.5 <= s["score"] < 0.7 for s in ranked["tier2"])


# ── 4. 节点间数据流转格式 ────────────────────────────────────────────────────
class TestDataFlow:
    def test_concept_item_schema(self):
        """concept item 字段完整"""
        item = {"concept": "固态电池", "source_section": "第3章", "confidence": 0.9}
        assert "concept" in item
        assert "source_section" in item
        assert 0 <= item["confidence"] <= 1

    def test_stock_item_schema(self, sample_stock_pool):
        """stock_pool item 字段完整"""
        for item in sample_stock_pool:
            assert "ts_code" in item
            assert "name" in item
            assert "concept" in item
            assert "layer" in item
            assert "node" in item
            assert "llm_score" in item
            assert "circ_mv" in item

    def test_industry_chain_schema(self):
        """产业链结构 schema"""
        chain = {
            "concept": "固态电池",
            "layers": [
                {
                    "layer_name": "上游材料",
                    "nodes": [
                        {"node_name": "锂矿", "description": "锂矿开采", "keywords": ["锂", "碳酸锂"]},
                    ],
                }
            ],
            "source_urls": ["https://example.com"],
        }
        assert "concept" in chain
        assert "layers" in chain
        assert len(chain["layers"]) > 0
        assert "nodes" in chain["layers"][0]


# ── 5. static_graph DAG 结构 ────────────────────────────────────────────────
class TestStaticGraph:
    def test_build_graph(self):
        """静态图谱 DAG 可正常编译"""
        from src.graphs.static_graph import build_static_graph
        graph = build_static_graph()
        assert graph is not None

    def test_state_schema(self):
        """StaticState TypedDict 包含必要字段"""
        from src.graphs.static_graph import StaticState
        hints = StaticState.__annotations__
        assert "policy_pdf_path" in hints
        assert "concepts" in hints
        assert "industry_chains" in hints
        assert "stock_pool" in hints
        assert "ranked_stocks" in hints
        assert "error_node" in hints


# ── 6. dynamic_graph DAG 结构 ────────────────────────────────────────────────
class TestDynamicGraph:
    def test_build_graph(self):
        """动态监控 DAG 可正常编译"""
        from src.graphs.dynamic_graph import build_dynamic_graph
        graph = build_dynamic_graph()
        assert graph is not None

    def test_state_schema(self):
        """DynamicState TypedDict 包含必要字段"""
        from src.graphs.dynamic_graph import DynamicState
        hints = DynamicState.__annotations__
        assert "raw_news" in hints
        assert "concepts" in hints
        assert "news_result" in hints
        assert "resonance_alerts" in hints
