"""
chain_splitter.py — 步骤2：产业链深度拆解

输入 state:
  concepts: list[dict]    # 来自 policy_parser

输出 state 增量:
  industry_chains: list[dict]
  # Schema: {"concept": str, "layers": [...], "source_urls": [...]}
"""
from __future__ import annotations

import json

from loguru import logger

from src.infrastructure.searxng_search import search
from src.infrastructure.web_fetcher import fetch_article
from src.nodes.llm_utils import call_llm_json, call_llm_with_tools, load_prompt

# 每个概念最多搜索和抓取的研报数量
MAX_REPORTS_PER_CONCEPT = 5
MAX_TEXT_PER_REPORT = 4000  # 字符数上限


def _search_reports_with_llm(concept: str) -> list[str]:
    """通过 LLM tool-call 智能搜索研报 URL（LLM 自主决定搜索关键词）"""
    prompt = f"""请搜索与A股概念「{concept}」相关的研究报告或行业分析文章。
你需要：
1. 使用 web_search 工具搜索（可以尝试不同关键词组合）
2. 从搜索结果中选取最有价值的 URL（优先选券商研报、行业深度分析）
3. 返回选中的 URL 列表

输出 JSON：
{{"urls": ["url1", "url2", ...], "search_queries_used": ["关键词1", ...]}}"""

    try:
        result = call_llm_with_tools(
            prompt,
            model="flash",
            system="你是A股研报搜索助手。使用 web_search 工具搜索，最终仅输出 JSON。",
            max_rounds=3,
            max_tokens=2048,
        )
        urls = result.get("urls", []) if isinstance(result, dict) else []
        queries = result.get("search_queries_used", []) if isinstance(result, dict) else []
        if queries:
            logger.debug("[chain_splitter] LLM 搜索关键词: {}", queries)
        return [u for u in urls if u][:MAX_REPORTS_PER_CONCEPT]
    except Exception as e:
        logger.warning("[chain_splitter] LLM 搜索失败: {}，降级为固定模板", e)
        # 降级：固定搜索模板
        query = f"{concept} 产业链 研报"
        results = search(query, num_results=MAX_REPORTS_PER_CONCEPT)
        return [r.url for r in results if r.url]


def _fetch_report_texts(urls: list[str]) -> list[str]:
    """批量抓取研报正文，过滤过短的内容"""
    texts: list[str] = []
    for url in urls:
        try:
            text = fetch_article(url)
            if len(text) > 500:
                texts.append(text[:MAX_TEXT_PER_REPORT])
        except Exception as e:
            logger.warning("[chain_splitter] 抓取失败 url={} reason={}", url, e)
    return texts


def _split_chain(concept: str, report_texts: list[str]) -> dict:
    """调用 V4-Pro 拆解产业链"""
    template = load_prompt("chain_splitter")
    combined = "\n\n---\n\n".join(report_texts)
    prompt = template.format(
        concept=concept,
        report_texts=combined[:8000],
    )
    result = call_llm_json(prompt, model="pro", max_tokens=4096)
    # 类型防护：LLM 可能返回 list 而非 dict
    if not isinstance(result, dict):
        logger.warning("[chain_splitter] LLM 返回非 dict: {}", type(result).__name__)
        result = {"layers": [], "concept": concept}
    result["source_urls"] = []  # 占位，实际 URLs 由外层填入
    return result


def run(state: dict) -> dict:
    concepts = state.get("concepts", [])
    logger.info("[chain_splitter] 开始拆解 {} 个概念", len(concepts))

    industry_chains: list[dict] = []
    for concept_item in concepts:
        concept = concept_item["concept"]
        logger.info("[chain_splitter] 拆解概念: {}", concept)

        # 1. 搜索研报
        urls = _search_reports_with_llm(concept)
        logger.debug("[chain_splitter] 搜索到 {} 个 URL", len(urls))

        # 2. 抓取正文
        texts = _fetch_report_texts(urls)
        logger.debug("[chain_splitter] 有效研报 {} 篇", len(texts))

        if not texts:
            logger.warning("[chain_splitter] 无有效研报，跳过概念: {}", concept)
            continue

        # 3. V4-Pro 拆解
        try:
            chain = _split_chain(concept, texts)
            chain["source_urls"] = urls
            industry_chains.append(chain)
            layer_count = len(chain.get("layers", []))
            node_count = sum(len(l.get("nodes", [])) for l in chain.get("layers", []))
            logger.info("[chain_splitter] {} 拆解完成: {} 层, {} 节点",
                        concept, layer_count, node_count)
        except Exception as e:
            logger.error("[chain_splitter] 拆解失败 concept={} reason={}", concept, e)

    return {
        "industry_chains": industry_chains,
        "error_node": None,
        "error_msg": None,
    }
