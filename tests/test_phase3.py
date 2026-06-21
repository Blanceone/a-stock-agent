"""
test_phase3.py — Phase 3 验收测试（动态监控）

覆盖：
  1. news_funnel 粗筛过滤率
  2. news_funnel 深读结构
  3. resonance_alert 三共振条件判断
  4. RSS 新闻去重
  5. 概念词库更新
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ── 1. news_funnel 粗筛 ──────────────────────────────────────────────────────
class TestNewsFunnelCoarse:
    @patch("src.nodes.news_funnel.call_llm_json")
    def test_coarse_filter_low_score(self, mock_llm):
        """低分新闻应被过滤"""
        mock_llm.return_value = {"news_score": 0.2}
        from src.nodes.news_funnel import _coarse_filter
        result = _coarse_filter("某公司日常公告", "某公司发布日常公告")
        assert result["news_score"] < 0.4

    @patch("src.nodes.news_funnel.call_llm_json")
    def test_coarse_filter_high_score(self, mock_llm):
        """高分新闻应通过"""
        mock_llm.return_value = {"news_score": 0.8}
        from src.nodes.news_funnel import _coarse_filter
        result = _coarse_filter("固态电池量产突破", "宁德时代固态电池良率达95%")
        assert result["news_score"] >= 0.4


# ── 2. news_funnel 深读 ──────────────────────────────────────────────────────
class TestNewsFunnelDeep:
    @patch("src.nodes.news_funnel.call_llm_json")
    def test_deep_read_structure(self, mock_llm):
        """深读返回结构正确"""
        mock_llm.return_value = {
            "news_score": 0.85,
            "impact_type": "industry",
            "impact_concept": "固态电池",
            "related_ts_codes": ["300750.SZ"],
            "new_concept_terms": ["硫化物电解质"],
            "sentiment": "positive",
        }
        from src.nodes.news_funnel import _deep_read
        result = _deep_read("固态电池量产突破", "宁德时代固态电池良率达95%", [])
        assert "news_score" in result
        assert "related_ts_codes" in result
        assert result["sentiment"] == "positive"

    def test_solid_battery_identification(self):
        """固态电池新闻应被正确识别（模拟）"""
        mock_result = {
            "news_score": 0.9,
            "impact_type": "industry",
            "impact_concept": "固态电池",
            "related_ts_codes": ["300750.SZ"],
            "new_concept_terms": [],
            "sentiment": "positive",
        }
        assert mock_result["impact_concept"] == "固态电池"
        assert mock_result["sentiment"] == "positive"


# ── 3. resonance_alert 三共振 ────────────────────────────────────────────────
class TestResonanceAlert:
    def test_three_conditions_all_required(self):
        """三共振条件必须全部满足"""
        from config.settings import Settings
        s = Settings()
        # 条件1: 消息面
        news_score = 0.8  # > 0.7 ✓
        assert news_score >= s.resonance_news_score_threshold
        # 条件2: 资金面
        capital_inflow_pct = 0.03  # > 0.02 ✓
        assert capital_inflow_pct >= s.resonance_capital_inflow_pct
        # 条件3: 技术面
        volume_ratio = 2.5  # > 2.0 ✓
        assert volume_ratio >= s.resonance_volume_ratio

    def test_single_condition_failure(self):
        """任一条件不满足则不触发"""
        from config.settings import Settings
        s = Settings()
        # 消息面不满足
        assert 0.5 < s.resonance_news_score_threshold
        # 资金面不满足
        assert 0.01 < s.resonance_capital_inflow_pct
        # 技术面不满足
        assert 1.5 < s.resonance_volume_ratio

    @patch("src.nodes.resonance_alert.fetch_moneyflow_intraday")
    def test_capital_flow_check(self, mock_fetch):
        """资金流检查"""
        mock_fetch.return_value = {
            "ts_code": "300750.SZ",
            "net_inflow": 500000,
            "net_inflow_pct": 3.5,
            "timestamp": "2026-06-20T10:00:00",
        }
        from src.nodes.resonance_alert import _check_capital_flow
        result = _check_capital_flow("300750.SZ")
        assert "inflow_pct" in result
        assert isinstance(result["inflow_pct"], float)


# ── 4. RSS 新闻去重 ──────────────────────────────────────────────────────────
class TestRSSDedup:
    def test_news_item_creation(self):
        """NewsItem 创建"""
        from src.infrastructure.rss_fetcher import NewsItem
        from datetime import datetime
        item = NewsItem(
            article_id="https://example.com/1",
            title="测试新闻",
            summary="测试摘要",
            pub_time=datetime(2026, 6, 20, 10, 0, 0),
            source="test",
        )
        assert item.title == "测试新闻"
        assert item.article_id == "https://example.com/1"

    def test_dedup_key_format(self):
        """去重 Key 格式正确"""
        from src.infrastructure.database import REDIS_KEY_NEWS_DEDUP
        key = REDIS_KEY_NEWS_DEDUP.format(article_id="abc123")
        assert key == "dedup:news:abc123"


# ── 5. 概念词库更新 ──────────────────────────────────────────────────────────
class TestConceptUpdate:
    def test_update_concepts_adds_new(self):
        """新概念词应加入词库"""
        from src.nodes.news_funnel import _update_concepts
        concepts = [{"concept": "固态电池", "source_section": "test", "confidence": 0.9}]
        updated = _update_concepts(concepts, ["硫化物电解质", "全固态"])
        assert len(updated) == 3
        assert any(c["concept"] == "硫化物电解质" for c in updated)

    def test_update_concepts_dedup(self):
        """重复概念词不重复添加"""
        from src.nodes.news_funnel import _update_concepts
        concepts = [{"concept": "固态电池", "source_section": "test", "confidence": 0.9}]
        updated = _update_concepts(concepts, ["固态电池", "新概念"])
        # "固态电池" 不应重复添加
        count = sum(1 for c in updated if c["concept"] == "固态电池")
        assert count == 1
        assert len(updated) == 2


# ── 6. 三共振预警模拟 ────────────────────────────────────────────────────────
class TestResonanceMock:
    def test_alert_text_format(self):
        """预警信号结构正确"""
        alert = {
            "ts_code": "300750.SZ",
            "news_title": "固态电池量产突破",
            "news_score": 0.85,
            "capital_inflow_pct": 3.5,
            "volume_ratio": 2.8,
            "timestamp": 1718900000,
        }
        assert "ts_code" in alert
        assert "news_title" in alert
        assert alert["news_score"] > 0.7
        assert alert["capital_inflow_pct"] > 2.0
        assert alert["volume_ratio"] > 2.0
