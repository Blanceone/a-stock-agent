"""
searxng_search.py — SearXNG 搜索封装。

⚠️ 红线：仅供 static_graph 产业链拆解和 sop_learner 低频调用，
         严禁在 dynamic_graph 高频轮询节点中调用。

限流：Redis 滑动窗口计数器，≤10 次/分钟（可配置）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests
from loguru import logger

from config.settings import settings
from src.infrastructure.database import REDIS_KEY_RATE_LIMIT, redis_client


class RateLimitError(Exception):
    """SearXNG 限流触发"""


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def _check_rate_limit() -> None:
    """Redis 滑动窗口计数器，超限抛 RateLimitError"""
    if redis_client is None:
        return
    bucket_key = REDIS_KEY_RATE_LIMIT.format(minute_bucket=int(time.time() // 60))
    count = redis_client.incr(bucket_key)
    if count == 1:
        redis_client.expire(bucket_key, 120)
    if count > settings.searxng_rate_limit_per_minute:
        raise RateLimitError(
            f"SearXNG 已达 {settings.searxng_rate_limit_per_minute} 次/分钟限制"
        )


def search(query: str, num_results: int = 10) -> list[SearchResult]:
    """
    同步搜索接口。
    调用前检查限流；结果结构化返回。
    """
    _check_rate_limit()
    logger.debug("[SearXNG] query='{}' num={}", query, num_results)

    url = f"{settings.searxng_url}/search"
    params = {"q": query, "format": "json", "number_of_results": num_results}
    headers = {}
    if settings.searxng_api_key:
        headers["Authorization"] = f"Bearer {settings.searxng_api_key}"

    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    results: list[SearchResult] = []
    for item in data.get("results", [])[:num_results]:
        results.append(SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=item.get("content", item.get("snippet", "")),
        ))
    logger.info("[SearXNG] 返回 {} 条结果", len(results))
    return results
