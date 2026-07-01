"""
static_graph.py — 静态图谱 DAG 编排

流水线：policy_parser → chain_splitter → entity_mapper → tech_ranker

触发条件：按需（用户提供政策 PDF 时）
输出：ranked_stocks（第一梯队 + 第二梯队候选股）

使用 LangGraph StateGraph 实现。
"""
from __future__ import annotations

import time as _time
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from loguru import logger

from src.nodes import chain_splitter, entity_mapper, policy_parser, tech_ranker


# ── State Schema ──────────────────────────────────────────────────────────────
class StaticState(TypedDict, total=False):
    # 输入
    policy_pdf_path: str
    retry_count: int

    # 中间态
    concepts: list[dict]
    industry_chains: list[dict]
    stock_pool: list[dict]

    # 输出
    ranked_stocks: dict

    # 错误追踪
    error_node: str | None
    error_msg: str | None


# ── 节点包装（错误隔离）───────────────────────────────────────────────────────
def _safe_call(node_name: str, node_fn, state: StaticState) -> dict:
    """统一错误隔离包装器，含执行计时埋点"""
    t0 = _time.monotonic()
    try:
        result = node_fn(state)
        latency_ms = round((_time.monotonic() - t0) * 1000)
        logger.bind(
            event_type="node_exec", node_name=node_name,
            status="ok", latency_ms=latency_ms,
        ).info("Node {} ok {}ms", node_name, latency_ms)
        return result
    except Exception as e:
        latency_ms = round((_time.monotonic() - t0) * 1000)
        logger.error("[StaticGraph] 节点 {} 执行失败: {}", node_name, e, exc_info=True)
        logger.bind(
            event_type="node_exec", node_name=node_name,
            status="error", latency_ms=latency_ms,
        ).error("Node {} error {}ms", node_name, latency_ms)
        return {
            "error_node": node_name,
            "error_msg": str(e),
        }


def policy_parser_node(state: StaticState) -> dict:
    return _safe_call("policy_parser", policy_parser.run, state)


def chain_splitter_node(state: StaticState) -> dict:
    return _safe_call("chain_splitter", chain_splitter.run, state)


def entity_mapper_node(state: StaticState) -> dict:
    return _safe_call("entity_mapper", entity_mapper.run, state)


def tech_ranker_node(state: StaticState) -> dict:
    return _safe_call("tech_ranker", tech_ranker.run, state)


# ── 条件路由 ──────────────────────────────────────────────────────────────────
def check_error(state: StaticState) -> str:
    """如果上游节点报错，跳到 END"""
    if state.get("error_node"):
        logger.error("[StaticGraph] 中断: 节点 {} 报错: {}",
                     state["error_node"], state.get("error_msg"))
        return "end"
    return "continue"


def after_policy_parser(state: StaticState) -> str:
    return check_error(state)


def after_chain_splitter(state: StaticState) -> str:
    return check_error(state)


def after_entity_mapper(state: StaticState) -> str:
    return check_error(state)


# ── 构建 DAG ──────────────────────────────────────────────────────────────────
def build_static_graph() -> StateGraph:
    graph = StateGraph(StaticState)

    # 添加节点
    graph.add_node("policy_parser", policy_parser_node)
    graph.add_node("chain_splitter", chain_splitter_node)
    graph.add_node("entity_mapper", entity_mapper_node)
    graph.add_node("tech_ranker", tech_ranker_node)

    # 边
    graph.add_edge(START, "policy_parser")

    graph.add_conditional_edges("policy_parser", after_policy_parser, {
        "continue": "chain_splitter",
        "end": END,
    })

    graph.add_conditional_edges("chain_splitter", after_chain_splitter, {
        "continue": "entity_mapper",
        "end": END,
    })

    graph.add_conditional_edges("entity_mapper", after_entity_mapper, {
        "continue": "tech_ranker",
        "end": END,
    })

    graph.add_edge("tech_ranker", END)

    return graph.compile()


# 全局编译实例
static_graph = None


def get_static_graph():
    global static_graph
    if static_graph is None:
        static_graph = build_static_graph()
    return static_graph


def run_static_pipeline(pdf_path: str) -> dict:
    """
    便捷入口函数，供 main.py 调用。
    返回最终的 state dict。
    """
    graph = get_static_graph()
    initial_state: StaticState = {
        "policy_pdf_path": pdf_path,
        "retry_count": 0,
    }
    logger.info("[StaticGraph] 启动, PDF: {}", pdf_path)
    final_state = graph.invoke(initial_state)
    logger.info("[StaticGraph] 完成")

    tier1 = final_state.get("ranked_stocks", {}).get("tier1", [])
    tier2 = final_state.get("ranked_stocks", {}).get("tier2", [])
    logger.info("[StaticGraph] 结果: 第一梯队 {} 只, 第二梯队 {} 只",
                len(tier1), len(tier2))

    return final_state
