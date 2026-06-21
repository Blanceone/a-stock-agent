"""
dynamic_graph.py — 动态监控 DAG 编排

流水线：news_funnel → resonance_alert

触发条件：定时（APScheduler 驱动，盘中每 N 分钟触发一次）
输出：resonance_alerts（三共振预警信号）

使用 LangGraph StateGraph 实现。
"""
from __future__ import annotations

import asyncio
import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from loguru import logger

from src.nodes import news_funnel, resonance_alert, sop_learner


# ── State Schema ──────────────────────────────────────────────────────────────
class DynamicState(TypedDict, total=False):
    # 输入
    raw_news: object        # NewsItem dataclass
    concepts: list[dict]    # 当前概念词库

    # 中间态
    news_result: dict | None
    concepts_updated: list[dict]

    # 输出
    resonance_alerts: list[dict]

    # 错误追踪
    error_node: str | None
    error_msg: str | None


# ── 节点包装 ──────────────────────────────────────────────────────────────────
def _safe_call(node_name: str, node_fn, state: DynamicState) -> dict:
    try:
        return node_fn(state)
    except Exception as e:
        logger.error("[DynamicGraph] 节点 {} 执行失败: {}", node_name, e, exc_info=True)
        return {"error_node": node_name, "error_msg": str(e)}


def news_funnel_node(state: DynamicState) -> dict:
    return _safe_call("news_funnel", news_funnel.run, state)


def resonance_alert_node(state: DynamicState) -> dict:
    return _safe_call("resonance_alert", resonance_alert.run, state)


def sop_learner_node(state: DynamicState) -> dict:
    return _safe_call("sop_learner", sop_learner.run, state)


# ── 条件路由 ──────────────────────────────────────────────────────────────────
def after_news_funnel(state: DynamicState) -> str:
    """
    新闻漏斗后判断：
      - 报错 → end
      - 粗筛未通过（news_result 为 None）→ end
      - 通过 → resonance_alert
    """
    if state.get("error_node"):
        return "end"
    if state.get("news_result") is None:
        return "end"
    return "continue"


def after_resonance(state: DynamicState) -> str:
    """共振检查后进入 SOP 学习"""
    if state.get("error_node"):
        return "end"
    return "sop"


# ── 构建 DAG ──────────────────────────────────────────────────────────────────
def build_dynamic_graph() -> StateGraph:
    graph = StateGraph(DynamicState)

    # 添加节点
    graph.add_node("news_funnel", news_funnel_node)
    graph.add_node("resonance_alert", resonance_alert_node)
    graph.add_node("sop_learner", sop_learner_node)

    # 边
    graph.add_edge(START, "news_funnel")

    graph.add_conditional_edges("news_funnel", after_news_funnel, {
        "continue": "resonance_alert",
        "end": END,
    })

    graph.add_conditional_edges("resonance_alert", after_resonance, {
        "sop": "sop_learner",
        "end": END,
    })

    graph.add_edge("sop_learner", END)

    return graph.compile()


# ── 全局编译实例 ──────────────────────────────────────────────────────────────
_dynamic_graph = None


def get_dynamic_graph():
    global _dynamic_graph
    if _dynamic_graph is None:
        _dynamic_graph = build_dynamic_graph()
    return _dynamic_graph


def process_news_item(news_item, concepts: list[dict]) -> dict:
    """
    处理单条新闻：走完整动态流水线。
    返回最终 state。
    """
    graph = get_dynamic_graph()
    initial_state: DynamicState = {
        "raw_news": news_item,
        "concepts": concepts,
    }
    return graph.invoke(initial_state)


async def run_dynamic_loop(
    rss_fetcher,
    concepts: list[dict],
    ranked_stocks: dict | None = None,
):
    """
    动态监控主循环（异步）。
    由 APScheduler 或 main.py 调用。

    参数:
      rss_fetcher: 异步生成器，yield NewsItem
      concepts: 当前概念词库
      ranked_stocks: 静态图谱输出的候选股（用于共振检查）
    """
    graph = get_dynamic_graph()
    logger.info("[DynamicGraph] 动态监控循环启动")

    async for news_item in rss_fetcher:
        initial_state: DynamicState = {
            "raw_news": news_item,
            "concepts": concepts,
        }

        try:
            final_state = graph.invoke(initial_state)

            # 更新概念词库（如果有新发现）
            if final_state.get("concepts_updated"):
                concepts = final_state["concepts_updated"]

            # 预警信号输出
            alerts = final_state.get("resonance_alerts", [])
            if alerts:
                for alert in alerts:
                    logger.warning(
                        "[DynamicGraph] 🚨 预警: {} | {} | 消息{:.2f} 资金{:.1f}% 量比{:.1f}",
                        alert["ts_code"], alert["news_title"],
                        alert["news_score"], alert["capital_inflow_pct"],
                        alert["volume_ratio"],
                    )
        except Exception as e:
            logger.error("[DynamicGraph] 处理异常: {}", e, exc_info=True)

    logger.info("[DynamicGraph] 动态监控循环结束")
