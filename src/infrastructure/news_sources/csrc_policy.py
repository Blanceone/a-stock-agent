"""
csrc_policy.py — 证监会公告直连 API 源（60 秒轮询）。

调用 csrc.gov.cn 的 searchList API 获取最新政策法规和公告。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

import aiohttp
from loguru import logger

from config.settings import settings
from src.infrastructure.news_sources.base import NewsItem, NewsSource


class CSRCSource(NewsSource):
    """证监会政策法规 — 直连 API，60 秒轮询"""

    source_name = "证监会"
    source_type = "policy"
    interval_sec: float = 60.0

    # 证监会要闻 channelId
    _CHANNEL_ID = "a1a078ee0bc54721ab6b148884c784a8"

    _HEADERS = {
        "Referer": "https://www.csrc.gov.cn/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
    }

    def __init__(self):
        self.interval_sec = settings.csrc_poll_interval
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # 政府网站 SSL 证书链验证常失败，跳过验证
            conn = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(
                headers=self._HEADERS,
                timeout=aiohttp.ClientTimeout(total=15),
                connector=conn,
            )
        return self._session

    async def fetch(self) -> list[NewsItem]:
        session = await self._get_session()
        url = f"{settings.csrc_api_url}/{self._CHANNEL_ID}"
        params = {
            "_isAgg": "true",
            "_isJson": "true",
            "_pageSize": "10",
            "_template": "index",
            "_rangeTimeGte": "",
            "_channelName": "",
            "page": "1",
        }
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

        # 解析 response: data.results[]
        results = (data.get("data") or {}).get("results") or []

        items: list[NewsItem] = []
        for entry in results:
            title = entry.get("title", "")
            entry_url = entry.get("url", "")
            if not title:
                continue
            # 用 manuscriptId 或 URL hash 作为 ID
            manuscript_id = entry.get("manuscriptId") or ""
            if manuscript_id:
                item_id = f"csrc:{manuscript_id}"
            elif entry_url:
                url_hash = hashlib.md5(entry_url.encode()).hexdigest()[:12]
                item_id = f"csrc:{url_hash}"
            else:
                continue
            # 解析时间
            pub_time = self._parse_time(
                entry.get("publishedTime") or entry.get("publishedTimeStr", "")
            )
            # content 可能是 HTML，取纯文本
            content = entry.get("content") or title
            items.append(NewsItem(
                article_id=item_id,
                title=title,
                summary=content[:500] if len(content) > 500 else content,
                pub_time=pub_time,
                source="证监会",
                source_type="policy",
                url=entry_url,
                extra={
                    "channel": entry.get("channelName", ""),
                },
            ))
        return items

    @staticmethod
    def _parse_time(raw) -> datetime:
        """尝试解析多种时间格式"""
        if isinstance(raw, (int, float)) and raw > 1e12:
            return datetime.fromtimestamp(raw / 1000)
        if isinstance(raw, str):
            if raw.isdigit() and len(raw) >= 13:
                return datetime.fromtimestamp(int(raw) / 1000)
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y.%m.%d"):
                try:
                    return datetime.strptime(raw, fmt)
                except ValueError:
                    continue
        return datetime.now()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
