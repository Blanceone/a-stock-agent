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
    return _parse_json(raw)


def _parse_json(raw: str) -> Any:
    """鲁棒 JSON 解析：处理 markdown fence、多余文本、控制字符。"""
    import re
    raw = raw.strip()

    # 去掉 markdown code fence (```json ... ``` 或 ``` ... ```)
    fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
    if fence_match:
        raw = fence_match.group(1).strip()

    # 尝试直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 提取第一个 { 到最后一个 } 或第一个 [ 到最后一个 ]
    brace_start = raw.find('{')
    bracket_start = raw.find('[')
    if brace_start >= 0 and (bracket_start < 0 or brace_start < bracket_start):
        brace_end = raw.rfind('}')
        if brace_end > brace_start:
            candidate = raw[brace_start:brace_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
    elif bracket_start >= 0:
        bracket_end = raw.rfind(']')
        if bracket_end > bracket_start:
            candidate = raw[bracket_start:bracket_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

    # 最后尝试修复常见格式问题（尾逗号、控制字符）
    cleaned = re.sub(r',\s*([}\]])', r'\1', raw)  # 去尾逗号
    cleaned = re.sub(r'[\x00-\x1f]', ' ', cleaned)  # 去控制字符
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("[LLM] JSON 解析失败: {}\n原始内容前200字: {}", e, raw[:200])
        raise


# ── Prompt 模板加载 ────────────────────────────────────────────────────────────
_PROMPT_DIR = Path(__file__).parent.parent.parent / "config" / "prompts"


def load_prompt(name: str) -> str:
    """加载 config/prompts/{name}.txt 模板"""
    path = _PROMPT_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
