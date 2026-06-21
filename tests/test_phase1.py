"""
test_phase1.py — Phase 1 验收测试

覆盖：
  1. 配置加载（settings.py）
  2. 技术指标计算（indicator_calc.py — 红线模块）
  3. 数据降级链（data_fetcher.py — 单元逻辑）
  4. DDL 表结构完整性
  5. SearXNG 限流器（searxng_search.py）
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ── 1. 配置加载 ──────────────────────────────────────────────────────────────
class TestSettings:
    def test_settings_loads(self, settings):
        """settings 能正常加载，所有关键字段存在"""
        assert hasattr(settings, "pg_dsn")
        assert hasattr(settings, "tushare_token")
        assert hasattr(settings, "deepseek_api_key")
        assert hasattr(settings, "model_flash")
        assert hasattr(settings, "model_pro")
        assert hasattr(settings, "searxng_url")
        assert hasattr(settings, "resources_base")
        assert hasattr(settings, "embedding_model_path")

    def test_default_thresholds(self, settings):
        """三共振阈值有合理默认值"""
        assert 0 < settings.resonance_news_score_threshold <= 1
        assert 0 < settings.resonance_capital_inflow_pct < 1
        assert settings.resonance_volume_ratio >= 1.0

    def test_tech_weights_sum_to_one(self, settings):
        """技术面权重之和 ≈ 1.0"""
        total = (settings.tech_weight_ma_alignment +
                 settings.tech_weight_volume_ratio +
                 settings.tech_weight_recent_gain +
                 settings.tech_weight_small_cap)
        assert abs(total - 1.0) < 0.01

    def test_model_names(self, settings):
        """模型名称正确"""
        assert settings.model_flash == "deepseek-chat"
        assert settings.model_pro == "deepseek-reasoner"


# ── 2. 技术指标计算（红线模块）──────────────────────────────────────────────
class TestIndicatorCalc:
    def test_calc_ma(self, sample_kline_df):
        from src.tools.indicator_calc import calc_ma
        result = calc_ma(sample_kline_df)
        assert "ma5" in result.columns
        assert "ma10" in result.columns
        assert "ma20" in result.columns
        # MA5 前4个应为 NaN
        assert pd.isna(result["ma5"].iloc[0])

    def test_calc_volume_ratio(self, sample_kline_df):
        from src.tools.indicator_calc import calc_volume_ratio
        result = calc_volume_ratio(sample_kline_df)
        assert "vol_ratio" in result.columns
        assert result["vol_ratio"].iloc[-1] > 0

    def test_calc_recent_gain(self, sample_kline_df):
        from src.tools.indicator_calc import calc_recent_gain
        gain = calc_recent_gain(sample_kline_df, days=10)
        assert isinstance(gain, float)
        assert -1 < gain < 10  # 合理范围

    def test_is_ma_bullish(self, sample_kline_df):
        from src.tools.indicator_calc import is_ma_bullish
        result = is_ma_bullish(sample_kline_df)
        assert isinstance(result, bool)

    def test_calc_all(self, sample_kline_df):
        from src.tools.indicator_calc import calc_all
        result = calc_all(sample_kline_df, circ_mv=500000)
        assert "ma_align_score" in result
        assert "vol_ratio_score" in result
        assert "gain_score" in result
        assert "small_cap_score" in result
        assert "is_ma_bullish" in result
        # 评分应在 [0, 1] 范围
        for key in ["ma_align_score", "vol_ratio_score", "gain_score", "small_cap_score"]:
            assert 0 <= result[key] <= 1

    def test_score_small_cap(self):
        from src.tools.indicator_calc import score_small_cap
        # 小市值应得分高
        assert score_small_cap(100000) == 1.0  # 20亿以下
        # 超大市值应得 0.2
        assert score_small_cap(5_000_000) == 0.2


# ── 3. 数据降级链 ────────────────────────────────────────────────────────────
class TestDataFetcher:
    def test_fetch_daily_tushare_success(self):
        """Tushare 成功时不调用降级源"""
        mock_df = pd.DataFrame({"trade_date": ["20260101"], "close": [10.0]})
        with patch("src.infrastructure.data_fetcher._tushare_pro") as mock_pro:
            mock_pro.return_value.daily.return_value = mock_df
            from src.infrastructure.data_fetcher import fetch_daily
            result = fetch_daily("000001.SZ", "20260101", "20260601")
            assert not result.empty

    def test_fetch_daily_fallback_on_error(self):
        """Tushare 失败时降级到 a-stock-data"""
        with patch("src.infrastructure.data_fetcher._tushare_pro") as mock_pro:
            mock_pro.return_value.daily.side_effect = Exception("积分不足")
            with patch("src.infrastructure.data_fetcher.requests.get") as mock_get:
                mock_resp = MagicMock()
                mock_resp.json.return_value = {
                    "data": {
                        "klines": ["2026-01-02,10.0,10.5,10.8,9.9,500000,5000000"]
                    }
                }
                mock_get.return_value = mock_resp
                from src.infrastructure.data_fetcher import fetch_daily
                result = fetch_daily("000001.SZ", "20260101", "20260601")
                assert not result.empty

    def test_fetch_moneyflow_intraday(self):
        """盘中资金流接口返回 dict"""
        with patch("src.infrastructure.data_fetcher.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"data": {"f62": 1000000, "f184": 250}}
            mock_get.return_value = mock_resp
            from src.infrastructure.data_fetcher import fetch_moneyflow_intraday
            result = fetch_moneyflow_intraday("300750.SZ")
            assert isinstance(result, dict)
            assert "net_inflow" in result


# ── 4. DDL 表结构 ────────────────────────────────────────────────────────────
class TestDDL:
    def test_ddl_contains_required_tables(self):
        from src.infrastructure.database import _DDL
        assert "stock_basic" in _DDL
        assert "sop_pending" in _DDL
        assert "sop_active" in _DDL

    def test_ddl_stock_basic_fields(self):
        from src.infrastructure.database import _DDL
        for field in ["ts_code", "name", "industry", "circ_mv", "is_st", "list_status"]:
            assert field in _DDL

    def test_ddl_sop_pending_fields(self):
        from src.infrastructure.database import _DDL
        assert "graph_json" in _DDL
        assert "source_text" in _DDL
        assert "status" in _DDL

    def test_ddl_sop_active_fields(self):
        from src.infrastructure.database import _DDL
        assert "sop_name" in _DDL
        assert "approved" in _DDL


# ── 5. SearXNG 限流器 ────────────────────────────────────────────────────────
class TestSearxngRateLimit:
    def test_rate_limit_key_format(self):
        """限流 Key 格式正确"""
        from src.infrastructure.database import REDIS_KEY_RATE_LIMIT
        key = REDIS_KEY_RATE_LIMIT.format(minute_bucket="202606201430")
        assert key.startswith("rate:searxng:")

    def test_llm_cache_key_format(self):
        """LLM 缓存 Key 格式正确"""
        from src.infrastructure.database import REDIS_KEY_LLM_CACHE
        key = REDIS_KEY_LLM_CACHE.format(prompt_hash="abc123")
        assert key.startswith("llm:")
