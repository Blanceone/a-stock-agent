"""
web_fetcher.py — 网页正文提取三层降级链。

三层职责（互不越界）：
  SearXNG 找 URL → defuddle 读静态 HTML → Playwright 兜底 JS 渲染 → BS4 最终兜底
"""
from __future__ import annotations

import hashlib

import requests
from bs4 import BeautifulSoup
from loguru import logger

from config.settings import settings
from src.infrastructure.database import (
    REDIS_KEY_URL_DEDUP,
    redis_client,
)


def _url_cache_key(url: str) -> str:
    h = hashlib.md5(url.encode()).hexdigest()
    return REDIS_KEY_URL_DEDUP.format(url_md5=h)


def fetch_article(url: str) -> str:
    """
    返回正文 Markdown 字符串。
    1. 先查 Redis URL 缓存（TTL 7d）
    2. L1 defuddle（Node.js 微服务）
    3. L2 Playwright（无头浏览器）
    4. L3 requests + BeautifulSoup（最终兜底）
    """
    # 缓存命中
    cache_key = _url_cache_key(url)
    if redis_client:
        cached = redis_client.get(cache_key)
        if cached:
            return cached

    # L1: defuddle 微服务
    try:
        resp = requests.post(
            settings.defuddle_service_url,
            json={"url": url},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        markdown = data.get("markdown", "").strip()
        if len(markdown) > 200:
            logger.debug("[WebFetcher] L1 defuddle 命中 url={}", url[:60])
            _cache_result(cache_key, markdown)
            return markdown
    except Exception as e:
        logger.warning("[WebFetcher] L1 defuddle 失败 url={} reason={}", url[:60], e)

    # L2: Playwright
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            html = page.content()
            browser.close()
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        if len(text) > 200:
            logger.debug("[WebFetcher] L2 Playwright 命中 url={}", url[:60])
            _cache_result(cache_key, text)
            return text
    except Exception as e:
        logger.warning("[WebFetcher] L2 Playwright 失败 url={} reason={}", url[:60], e)

    # L3: requests + BeautifulSoup（最终兜底）
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0.0.0 Safari/537.36"
        })
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        logger.info("[WebFetcher] L3 BS4 兜底 url={} len={}", url[:60], len(text))
        _cache_result(cache_key, text)
        return text
    except Exception as e:
        logger.error("[WebFetcher] 三层均失败 url={} reason={}", url[:60], e)
        raise


def _cache_result(cache_key: str, text: str) -> None:
    """将抓取结果缓存到 Redis（TTL 7天）"""
    if redis_client:
        redis_client.setex(cache_key, settings.redis_url_dedup_ttl, text)
