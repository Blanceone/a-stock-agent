"""
gov_policy.py — 国务院政策库直连 API 源（60 秒轮询）。

调用 gov.cn 搜索 API 获取最新国务院政策文件。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

import aiohttp
from loguru import logger

from config.settings import settings
from src.infrastructure.news_sources.base import NewsItem, NewsSource


class GovPolicySource(NewsSource):
    """国务院政策文件 — 直连 API，60 秒轮询"""

    source_name = "国务院"
    source_type = "policy"
    interval_sec: float = 60.0

    _HEADERS = {
        "Referer": "https://www.gov.cn/zhengce/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
    }

    def __init__(self):
        self.interval_sec = settings.gov_poll_interval
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._HEADERS,
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def fetch(self) -> list[NewsItem]:
        session = await self._get_session()
        params = {
            "t": "zhengcelibrary",
            "q": "",
            "sort": "pubtime",
            "sortType": "1",
            "p": "1",
            "n": "10",
            "type": "gwyzcwjk",
        }
        async with session.get(settings.gov_api_url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

        # 解析 response: searchVO.catMap.gongwen.listVO[]
        search_vo = (data.get("searchVO") or {})
        cat_map = search_vo.get("catMap") or {}
        gongwen = cat_map.get("gongwen") or {}
        entries = gongwen.get("listVO") or []

        items: list[NewsItem] = []
        for entry in entries:
            title = entry.get("title", "")
            url = entry.get("url", "")
            if not title or not url:
                continue
            # 用 URL 的 MD5 前 12 位作为 ID（政策无数字 ID）
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            # pubtime 可能是 epoch ms 字符串或日期字符串
            pub_time = self._parse_time(entry.get("pubtime") or entry.get("pubtimeStr", ""))
            summary = entry.get("summary") or title
            pub_org = entry.get("puborg") or ""
            items.append(NewsItem(
                article_id=f"gov:{url_hash}",
                title=title,
                summary=summary,
                pub_time=pub_time,
                source="国务院",
                source_type="policy",
                url=url,
                extra={"pub_org": pub_org},
            ))
        return items

    @staticmethod
    def _parse_time(raw) -> datetime:
        """尝试解析多种时间格式"""
        if isinstance(raw, (int, float)) and raw > 1e12:
            return datetime.fromtimestamp(raw / 1000)
        if isinstance(raw, str):
            # epoch ms 字符串
            if raw.isdigit() and len(raw) >= 13:
                return datetime.fromtimestamp(int(raw) / 1000)
            # ISO 日期
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y.%m.%d"):
                try:
                    return datetime.strptime(raw, fmt)
                except ValueError:
                    continue
        return datetime.now()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
