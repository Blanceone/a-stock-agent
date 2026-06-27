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


# ── Tool-Call 工具定义 ─────────────────────────────────────────────────────────

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "搜索互联网获取最新信息、概念含义、行业动态、政策背景等",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，应尽量精准",
                },
                "num_results": {
                    "type": "integer",
                    "description": "返回结果数量，默认5",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}

DEFAULT_TOOLS = [WEB_SEARCH_TOOL]


def _execute_tool(name: str, args: dict) -> str:
    """执行工具调用，返回 JSON 字符串"""
    if name == "web_search":
        try:
            from src.infrastructure.searxng_search import search
            results = search(args["query"], num_results=args.get("num_results", 5))
            return json.dumps(
                [{"title": r.title, "snippet": r.snippet, "url": r.url} for r in results],
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
    return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)


# ── Tool-Call LLM 调用 ─────────────────────────────────────────────────────────

def call_llm_with_tools(
    prompt: str,
    *,
    tools: list[dict] | None = None,
    max_rounds: int = 5,
    model: str = "flash",
    system: str = "你是一位专业的A股投研分析师。你可以使用工具搜索信息，最终仅输出 JSON。",
    temperature: float = 0.0,
    max_tokens: int = 4096,
    use_cache: bool = True,
) -> Any:
    """
    带 tool-call 的 LLM 调用。

    流程：LLM → tool_calls → 执行工具 → 结果回传 → LLM → ... → 最终回答
    最多 max_rounds 轮工具调用，防止死循环。
    返回解析后的 JSON（dict 或 list）。
    """
    if tools is None:
        tools = DEFAULT_TOOLS

    model_id = settings.model_flash if model == "flash" else settings.model_pro
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    client = _get_client()

    for round_num in range(max_rounds):
        logger.debug("[LLM-Tools] round={} model={} messages={}", round_num, model_id, len(messages))

        resp = client.chat.completions.create(
            model=model_id,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=temperature,
            max_tokens=max_tokens,
        )

        msg = resp.choices[0].message

        # 无 tool_calls → 最终回答
        if not msg.tool_calls:
            content = (msg.content or "").strip()
            logger.debug("[LLM-Tools] 最终回答（{} 轮）len={}", round_num + 1, len(content))
            return _parse_json(content)

        # 有 tool_calls → 执行工具并回传结果
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}

            logger.info("[LLM-Tools] 调用工具: {}({})", fn_name, fn_args.get("query", ""))
            result = _execute_tool(fn_name, fn_args)
            logger.debug("[LLM-Tools] 工具结果 len={}", len(result))

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    # 超过 max_rounds 仍未得到最终回答，取最后一次内容
    logger.warning("[LLM-Tools] 超过 {} 轮工具调用限制", max_rounds)
    last_content = messages[-1].get("content", "{}")
    return _parse_json(last_content)


# ── Prompt 模板加载 ────────────────────────────────────────────────────────────
_PROMPT_DIR = Path(__file__).parent.parent.parent / "config" / "prompts"


def load_prompt(name: str) -> str:
    """加载 config/prompts/{name}.txt 模板"""
    path = _PROMPT_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
