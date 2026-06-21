"""
rss_fetcher.py — 每分钟轮询 RSSHub 财联社电报。
去重后将新增条目推入 asyncio.Queue 供 news_funnel.py 消费。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import feedparser
from loguru import logger

from config.settings import settings
from src.infrastructure.database import REDIS_KEY_NEWS_DEDUP, redis_client


@dataclass
class NewsItem:
    article_id: str       # RSS <guid> 作为去重 key
    title: str
    summary: str          # RSS <description>
    pub_time: datetime
    source: str = "财联社"


def _parse_entries(feed: feedparser.FeedParserDict) -> list[NewsItem]:
    """将 feedparser entries 转为 NewsItem 列表"""
    items: list[NewsItem] = []
    for entry in feed.get("entries", []):
        article_id = entry.get("id", entry.get("link", ""))
        if not article_id:
            continue
        pub_time_raw = entry.get("published_parsed") or entry.get("updated_parsed")
        pub_time = datetime(*pub_time_raw[:6]) if pub_time_raw else datetime.now()
        items.append(NewsItem(
            article_id=article_id,
            title=entry.get("title", ""),
            summary=entry.get("summary", entry.get("description", "")),
            pub_time=pub_time,
            source="财联社",
        ))
    return items


async def poll_rss(queue: asyncio.Queue, interval_sec: int = 60) -> None:
    """
    无限轮询任务。每 interval_sec 秒拉取一次 RSSHub，
    将未见过的 NewsItem 推入 queue，并在 Redis 标记去重（TTL 7天）。
    """
    feed_url = f"{settings.rsshub_url}/cls/telegraph"
    logger.info("[RSS] 启动轮询: {} 间隔={}s", feed_url, interval_sec)

    while True:
        try:
            feed = feedparser.parse(feed_url)
            items = _parse_entries(feed)
            new_count = 0
            for item in items:
                dedup_key = REDIS_KEY_NEWS_DEDUP.format(article_id=item.article_id)
                if redis_client and redis_client.exists(dedup_key):
                    continue
                # 标记已见（TTL 7天）
                if redis_client:
                    redis_client.setex(dedup_key, settings.redis_url_dedup_ttl, "1")
                await queue.put(item)
                new_count += 1
            if new_count > 0:
                logger.debug("[RSS] 本轮新增 {} 条（总解析 {} 条）", new_count, len(items))
        except Exception as e:
            logger.error("[RSS] 轮询异常: {}", e)
        await asyncio.sleep(interval_sec)
