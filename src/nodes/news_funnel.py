"""
news_funnel.py — 步骤5：新闻漏斗与概念更新

输入 state:
  raw_news: NewsItem          # 来自 RSS 轮询的单条新闻
  concepts: list[dict]        # 已有概念词库

输出 state 增量:
  concepts_updated: list[dict]  # 更新后的概念词库
  news_result: dict | None       # 漏斗结果（粗筛通过才返回）

流程:
  1. V4-Flash 一级粗筛（批量 20 条，此处处理单条）
  2. V4-Pro 二级深读
  3. 新概念词更新概念词库
"""
from __future__ import annotations

import json
from datetime import datetime

from loguru import logger

from src.nodes.llm_utils import call_llm_json, load_prompt


def _coarse_filter(title: str, summary: str) -> dict:
    """V4-Flash 一级粗筛"""
    template = load_prompt("news_coarse")
    prompt = template.format(
        news_title=title,
        news_summary=summary or "",
    )
    return call_llm_json(prompt, model="flash", max_tokens=512)


def _deep_read(title: str, summary: str, concepts: list[dict]) -> dict:
    """V4-Pro 二级深读"""
    template = load_prompt("news_deep")
    concept_list = [c.get("concept", "") for c in concepts]
    prompt = template.format(
        news_title=title,
        news_content=summary or "",
        concept_list=json.dumps(concept_list, ensure_ascii=False),
        current_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    return call_llm_json(prompt, model="pro", max_tokens=2048)


def _update_concepts(concepts: list[dict], new_terms: list[str]) -> list[dict]:
    """将新发现的概念词合并入词库（去重）"""
    existing = {c.get("concept") for c in concepts}
    for term in new_terms:
        if term and term not in existing:
            concepts.append({
                "concept": term,
                "source_section": "news_discovered",
                "confidence": 0.8,
            })
            existing.add(term)
            logger.info("[news_funnel] 新概念入库: {}", term)
    return concepts


def run(state: dict) -> dict:
    raw_news = state.get("raw_news")
    concepts = state.get("concepts", [])

    if raw_news is None:
        return {"news_result": None, "concepts_updated": concepts}

    title = raw_news.title
    summary = getattr(raw_news, "summary", "") or ""
    url = getattr(raw_news, "article_id", "") or getattr(raw_news, "url", "")
    logger.info("[news_funnel] 处理: {}", title)

    # 1. 一级粗筛
    try:
        coarse = _coarse_filter(title, summary)
    except Exception as e:
        logger.warning("[news_funnel] 粗筛失败 reason={}", e)
        return {"news_result": None, "concepts_updated": concepts}

    news_score = coarse.get("news_score", 0)
    is_relevant = coarse.get("is_relevant", True)
    if not is_relevant:
        logger.debug("[news_funnel] 粗筛未通过 score={:.2f}: {}", news_score, title)
        return {"news_result": None, "concepts_updated": concepts}

    logger.info("[news_funnel] 粗筛通过 score={:.2f}: {}", news_score, title)

    # 2. 二级深读
    try:
        deep = _deep_read(title, summary, concepts)
    except Exception as e:
        logger.warning("[news_funnel] 深读失败 reason={}", e)
        return {"news_result": None, "concepts_updated": concepts}

    news_score = deep.get("news_score", 0)
    new_terms = deep.get("new_concept_terms", [])
    ts_codes = deep.get("related_ts_codes", [])

    logger.info("[news_funnel] 深读 score={:.2f} codes={} 新概念={}",
                news_score, ts_codes, new_terms)

    # 3. 更新概念词库
    concepts = _update_concepts(concepts, new_terms)

    news_result = {
        "news_score": news_score,
        "news_title": title,
        "news_url": url,
        "impact_type": deep.get("impact_type"),
        "impact_concept": deep.get("impact_concept"),
        "impact_node": deep.get("impact_node"),
        "sentiment": deep.get("sentiment"),
        "reason": deep.get("reason"),
        "mentioned_companies": deep.get("mentioned_companies", []),
        "supply_chain_impact": deep.get("supply_chain_impact", []),
        "related_ts_codes": ts_codes,
        "new_concept_terms": new_terms,
        "news_timestamp": datetime.now().isoformat(),
    }

    return {
        "news_result": news_result,
        "concepts_updated": concepts,
        "error_node": None,
        "error_msg": None,
    }
