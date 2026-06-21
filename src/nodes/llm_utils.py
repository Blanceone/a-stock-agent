"""
llm_utils.py — LLM 调用封装（供所有节点模块共用）。

双模型策略：
  - Flash (deepseek-chat)：高频/粗筛/摘要
  - Pro   (deepseek-reasoner)：深度推理/拆解/精判
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from loguru import logger
from openai import OpenAI

from config.settings import settings
from src.infrastructure.database import REDIS_KEY_LLM_CACHE, redis_client

# ── OpenAI 兼容客户端 ─────────────────────────────────────────────────────────
_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
    return _client


# ── 缓存 ──────────────────────────────────────────────────────────────────────
def _cache_key(messages: list[dict]) -> str:
    raw = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    h = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return REDIS_KEY_LLM_CACHE.format(prompt_hash=h)


def _get_cached(messages: list[dict]) -> str | None:
    if redis_client is None:
        return None
    key = _cache_key(messages)
    return redis_client.get(key)


def _set_cache(messages: list[dict], result: str) -> None:
    if redis_client is None:
        return
    key = _cache_key(messages)
    redis_client.setex(key, settings.redis_llm_cache_ttl, result)


# ── 核心调用 ──────────────────────────────────────────────────────────────────
def call_llm(
    prompt: str,
    *,
    model: str = "flash",
    system: str = "你是一位专业的A股投研分析师，仅输出 JSON，不要解释。",
    temperature: float = 0.0,
    max_tokens: int = 4096,
    use_cache: bool = True,
) -> str:
    """
    调用 DeepSeek 模型，返回原始文本响应。
    model: "flash" 或 "pro"
    """
    model_id = settings.model_flash if model == "flash" else settings.model_pro
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    if use_cache:
        cached = _get_cached(messages)
        if cached:
            logger.debug("[LLM] 缓存命中 model={}", model_id)
            return cached

    client = _get_client()
    logger.debug("[LLM] 调用 model={} prompt_len={}", model_id, len(prompt))

    resp = client.chat.completions.create(
        model=model_id,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    result = resp.choices[0].message.content.strip()

    if use_cache:
        _set_cache(messages, result)

    return result


def call_llm_json(
    prompt: str,
    *,
    model: str = "flash",
    system: str = "你是一位专业的A股投研分析师，仅输出 JSON，不要解释。",
    temperature: float = 0.0,
    max_tokens: int = 4096,
    use_cache: bool = True,
) -> Any:
    """调用 LLM 并解析 JSON 返回（支持 list 或 dict）。"""
    raw = call_llm(
        prompt,
        model=model,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
        use_cache=use_cache,
    )
    # 去掉 markdown code fence
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw[:-3]
    raw = raw.strip()
    return json.loads(raw)


# ── Prompt 模板加载 ────────────────────────────────────────────────────────────
_PROMPT_DIR = Path(__file__).parent.parent.parent / "config" / "prompts"


def load_prompt(name: str) -> str:
    """加载 config/prompts/{name}.txt 模板"""
    path = _PROMPT_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
