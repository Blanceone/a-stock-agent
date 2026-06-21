"""
conftest.py — 共享测试 fixtures。

所有测试模块共用这些 fixture，用于初始化基础设施、Mock 外部依赖等。
运行测试：pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 加载 .env（如果存在）
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")


@pytest.fixture(scope="session")
def project_root():
    """项目根目录"""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def settings():
    """全局配置"""
    from config.settings import Settings
    return Settings()


@pytest.fixture(scope="session")
def mock_redis():
    """Mock Redis 客户端"""
    mock = MagicMock()
    mock.get.return_value = None
    mock.set.return_value = True
    mock.setex.return_value = True
    mock.lpush.return_value = 1
    mock.expire.return_value = True
    mock.ping.return_value = True
    return mock


@pytest.fixture(scope="session")
def sample_stock_pool():
    """测试用候选股池"""
    return [
        {"ts_code": "000001.SZ", "name": "平安银行", "concept": "金融",
         "layer": "核心层", "node": "银行", "llm_score": 0.9, "circ_mv": 1500000},
        {"ts_code": "300750.SZ", "name": "宁德时代", "concept": "新能源",
         "layer": "核心层", "node": "动力电池", "llm_score": 0.95, "circ_mv": 800000},
        {"ts_code": "002594.SZ", "name": "比亚迪", "concept": "新能源",
         "layer": "核心层", "node": "整车", "llm_score": 0.85, "circ_mv": 1200000},
    ]


@pytest.fixture(scope="session")
def sample_kline_df():
    """测试用K线数据（60个交易日）"""
    import pandas as pd
    import numpy as np

    np.random.seed(42)
    n = 60
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    close = 10 + np.cumsum(np.random.randn(n) * 0.2)
    close = np.maximum(close, 1)  # 确保价格 > 0

    df = pd.DataFrame({
        "trade_date": dates.strftime("%Y%m%d"),
        "open": close + np.random.randn(n) * 0.1,
        "high": close + abs(np.random.randn(n) * 0.3),
        "low": close - abs(np.random.randn(n) * 0.3),
        "close": close,
        "vol": np.random.randint(100000, 1000000, n).astype(float),
        "amount": np.random.randint(1000000, 10000000, n).astype(float),
    })
    return df


@pytest.fixture
def sample_concepts():
    """测试用概念词库"""
    return [
        {"concept": "固态电池", "source_section": "第3章", "confidence": 0.9},
        {"concept": "人形机器人", "source_section": "第5章", "confidence": 0.85},
        {"concept": "低空经济", "source_section": "第4章", "confidence": 0.8},
    ]


@pytest.fixture
def sample_news_items():
    """测试用新闻条目"""
    from src.infrastructure.rss_fetcher import NewsItem
    from datetime import datetime
    return [
        NewsItem(
            article_id="https://example.com/news/1",
            title="固态电池量产突破：宁德时代宣布良率达95%",
            summary="宁德时代在固态电池领域取得重大突破，量产良率达到95%。",
            pub_time=datetime(2026, 6, 20, 10, 0, 0),
            source="财联社",
        ),
        NewsItem(
            article_id="https://example.com/news/2",
            title="央行宣布降准50个基点",
            summary="中国人民银行决定下调金融机构存款准备金率0.5个百分点。",
            pub_time=datetime(2026, 6, 20, 14, 0, 0),
            source="财联社",
        ),
        NewsItem(
            article_id="https://example.com/news/3",
            title="某公司年报预告大幅增长",
            summary="某上市公司发布年报预告，净利润同比增长200%。",
            pub_time=datetime(2026, 6, 20, 16, 0, 0),
            source="财联社",
        ),
    ]
