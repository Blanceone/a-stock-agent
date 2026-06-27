"""
base.py — 新闻源抽象基类、NewsItem 数据模型、NewsAggregator 多源聚合器。

设计原则：
- NewsItem 向后兼容：新增字段均有默认值，4参数构造仍有效
- NewsSource ABC：各源实现 fetch()，safe_fetch() 保证不抛异常
- NewsAggregator：每源一个 asyncio.Task，独立间隔轮询，Redis ID 去重
"""
from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from loguru import logger

from config.settings import settings
from src.infrastructure.database import (
    REDIS_KEY_NEWS_DEDUP, REDIS_KEY_NEWS_FEED, redis_client,
)


@dataclass
class NewsItem:
    """新闻条目数据模型。向后兼容：前4个字段不变，新增字段有默认值。"""
    article_id: str       # 唯一 ID（带源前缀，如 cls:12345, gov:abc123）
    title: str
    summary: str
    pub_time: datetime
    source: str = "财联社"
    source_type: str = "telegraph"   # "telegraph" | "policy"
    url: str = ""                     # 原文链接（政策源需要）
    extra: dict = field(default_factory=dict)  # 源特有字段 (level, subjects 等)


class NewsSource(ABC):
    """所有新闻源的抽象基类"""

    source_name: str = ""       # 人类可读名称，如 "财联社"
    source_type: str = ""       # "telegraph" | "policy"
    interval_sec: float = 60.0  # 最小轮询间隔（秒）

    @abstractmethod
    async def fetch(self) -> list[NewsItem]:
        """拉取最新条目。失败时应抛异常（由 safe_fetch 捕获）。"""
        ...

    async def safe_fetch(self) -> list[NewsItem]:
        """带异常捕获的 fetch 包装，保证不抛出异常"""
        try:
            return await self.fetch()
        except Exception as e:
            err_msg = str(e) or type(e).__name__
            logger.warning("[NewsSource] {} 拉取失败: {}({})", self.source_name, type(e).__name__, err_msg)
            return []


class NewsAggregator:
    """多源新闻聚合器：各源独立轮询，统一去重，推入 Queue"""

    def __init__(
        self,
        queue: asyncio.Queue,
        sources: list[NewsSource],
        redis: Optional[object] = None,
        on_process: Optional[callable] = None,
    ):
        self._queue = queue
        self._sources = sources
        self._redis = redis or redis_client
        self._running = False
        self._on_process = on_process  # async callback(item) for immediate processing
        self._semaphore = asyncio.Semaphore(5)  # max 5 concurrent processing tasks
        self._bg_tasks: set[asyncio.Task] = set()  # 持有 task 引用防止 GC 回收

    async def start(self) -> None:
        """启动所有源的并行轮询任务"""
        self._running = True
        tasks = [
            asyncio.create_task(self._poll_source(src))
            for src in self._sources
        ]
        logger.info("[Aggregator] 启动 {} 个新闻源", len(self._sources))
        await asyncio.gather(*tasks)

    async def stop(self) -> None:
        self._running = False

    async def _poll_source(self, source: NewsSource) -> None:
        """单源独立轮询循环"""
        logger.info(
            "[Aggregator] 启动源: {} (间隔 {:.0f}s, 类型: {})",
            source.source_name, source.interval_sec, source.source_type,
        )
        while self._running:
            try:
                items = await source.safe_fetch()
                new_count = 0
                for item in items:
                    dedup_key = REDIS_KEY_NEWS_DEDUP.format(article_id=item.article_id)
                    if self._redis and self._redis.exists(dedup_key):
                        continue
                    # 标记已见（TTL 7天）
                    if self._redis:
                        self._redis.setex(dedup_key, settings.redis_url_dedup_ttl, "1")
                        # 写入新闻 feed（供仪表盘展示）
                        entry = {
                            "article_id": item.article_id,
                            "title": item.title,
                            "summary": item.summary,
                            "pub_time": item.pub_time.isoformat(),
                            "source": item.source,
                            "source_type": item.source_type,
                            "url": item.url,
                        }
                        self._redis.lpush(
                            REDIS_KEY_NEWS_FEED,
                            json.dumps(entry, ensure_ascii=False),
                        )
                        self._redis.ltrim(REDIS_KEY_NEWS_FEED, 0, 499)
                        self._redis.expire(REDIS_KEY_NEWS_FEED, 7 * 86400)  # 7天循环覆盖
                    await self._queue.put(item)
                    new_count += 1
                    # 立即触发并行处理（不等待定时任务）
                    if self._on_process:
                        task = asyncio.create_task(self._run_process(item))
                        self._bg_tasks.add(task)
                        task.add_done_callback(self._bg_tasks.discard)
                if new_count > 0:
                    logger.debug(
                        "[Aggregator] {} 新增 {} 条（总拉取 {} 条）",
                        source.source_name, new_count, len(items),
                    )
            except Exception as e:
                logger.error("[Aggregator] {} 轮询异常: {}", source.source_name, e)
            await asyncio.sleep(source.interval_sec)

    async def _run_process(self, item: NewsItem) -> None:
        """带信号量控制的单条新闻处理（并行执行，最多5路）"""
        async with self._semaphore:
            try:
                await self._on_process(item)
            except Exception as e:
                logger.warning("[Aggregator] 处理异常 article_id={}: {}", item.article_id, e)
