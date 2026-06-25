"""
rss_fetcher.py — 向后兼容层。

原 RSS 轮询逻辑已迁移至 news_sources 包。此文件保留 re-export，
确保所有 `from src.infrastructure.rss_fetcher import NewsItem` 继续工作。
"""
from __future__ import annotations

import asyncio
import json

from loguru import logger

# ── Re-export：保持所有外部 import 路径不变 ────────────────────────────────────
from src.infrastructure.news_sources.base import NewsItem, NewsAggregator

from config.settings import settings
from src.infrastructure.database import (
    REDIS_KEY_NEWS_DEDUP, REDIS_KEY_NEWS_FEED, redis_client,
)


async def poll_rss(queue: asyncio.Queue, interval_sec: int = 5) -> None:
    """
    兼容入口：使用 NewsAggregator 替代原 RSSHub 轮询。
    默认间隔已从 300s 降为 5s（财联社直连 API）。

    此函数保持 poll_rss 的调用签名不变，供 main.py 旧代码使用。
    新代码应直接使用 NewsAggregator。
    """
    from src.infrastructure.news_sources.cls_telegraph import CLSTelegraphSource
    from src.infrastructure.news_sources.gov_policy import GovPolicySource
    from src.infrastructure.news_sources.csrc_policy import CSRCSource

    sources = [
        CLSTelegraphSource(),
        GovPolicySource(),
        CSRCSource(),
    ]
    aggregator = NewsAggregator(queue, sources, redis_client)
    await aggregator.start()
