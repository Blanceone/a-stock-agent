"""
news_sources — 多源新闻采集包。

替代原 RSSHub 轮询方案，直连各数据源 API 实现秒级延迟。
"""
from src.infrastructure.news_sources.base import (
    NewsItem,
    NewsSource,
    NewsAggregator,
)

__all__ = ["NewsItem", "NewsSource", "NewsAggregator"]
