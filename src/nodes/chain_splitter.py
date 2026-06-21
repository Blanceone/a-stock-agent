"""
chain_splitter.py — 步骤2：产业链深度拆解

输入 state:
  concepts: list[dict]    # 来自 policy_parser

输出 state 增量:
  industry_chains: list[dict]
  # Schema: {"concept": str, "layers": [...], "source_urls": [...]}
"""
from __future__ import annotations

from loguru import logger

from src.infrastructure.searxng_search import search
from src.infrastructure.web_fetcher import fetch_article
from src.nodes.llm_utils import call_llm_json, load_prompt

# 每个概念最多搜索和抓取的研报数量
MAX_REPORTS_PER_CONCEPT = 5
MAX_TEXT_PER_REPORT = 4000  # 字符数上限


def _search_reports(concept: str) -> list[str]:
    """通过 SearXNG 搜索研报 URL"""
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
        urls = _search_reports(concept)
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
