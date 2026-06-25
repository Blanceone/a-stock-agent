"""
cls_telegraph.py — 财联社电报直连 API 源（5 秒轮询）。

绕过 RSSHub，直接调用 cls.cn 的 /api/cache 端点获取实时电报。
该端点无需鉴权，返回 JSON 格式的最新约 20 条电报。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import aiohttp
from loguru import logger

from config.settings import settings
from src.infrastructure.news_sources.base import NewsItem, NewsSource


class CLSTelegraphSource(NewsSource):
    """财联社电报 — 直连 API，5 秒轮询"""

    source_name = "财联社"
    source_type = "telegraph"
    interval_sec: float = 5.0

    _API_URL = "https://www.cls.cn/api/cache"
    _PARAMS = {
        "app": "CailianpressWeb",
        "name": "telegraph",
        "os": "web",
        "sv": "8.7.9",
    }
    _HEADERS = {
        "Referer": "https://www.cls.cn/telegraph",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
    }

    def __init__(self):
        self.interval_sec = settings.cls_poll_interval
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    async def fetch(self) -> list[NewsItem]:
        session = await self._get_session()
        async with session.get(self._API_URL, params=self._PARAMS) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

        roll_data = (data.get("data") or {}).get("roll_data", [])
        items: list[NewsItem] = []
        for entry in roll_data:
            entry_id = entry.get("id")
            if not entry_id:
                continue
            ctime = entry.get("ctime", 0)
            pub_time = datetime.fromtimestamp(ctime) if ctime else datetime.now()
            title = entry.get("title") or ""
            brief = entry.get("brief") or ""
            content = entry.get("content") or brief
            # title 可能为空，用 brief 前 80 字兜底
            if not title:
                title = brief[:80] if brief else ""
            items.append(NewsItem(
                article_id=f"cls:{entry_id}",
                title=title,
                summary=content,
                pub_time=pub_time,
                source="财联社",
                source_type="telegraph",
                extra={
                    "level": entry.get("level"),          # B=重要, C=普通
                    "subjects": entry.get("subjects", []),
                    "stock_list": entry.get("stock_list", []),
                },
            ))
        return items

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
