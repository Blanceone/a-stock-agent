"""
tech_ranker.py — 步骤4：技术面多因子打分与梯队排序

输入 state:
  stock_pool: list[dict]   # 来自 entity_mapper

输出 state 增量:
  ranked_stocks: dict      # {"tier1": [...], "tier2": [...]}

⚠️ 红线：所有数值计算必须调用 src/tools/indicator_calc.py，禁止 LLM 计算。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from loguru import logger

from config.settings import settings
from src.infrastructure.data_fetcher import fetch_daily
from src.infrastructure.database import redis_client
from src.tools.indicator_calc import calc_all


def _fetch_klines(ts_codes: list[str]) -> dict:
    """批量拉取近60个交易日K线数据"""
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
    klines: dict = {}
    for ts_code in ts_codes:
        try:
            df = fetch_daily(ts_code, start_date, end_date)
            if not df.empty:
                klines[ts_code] = df
        except Exception as e:
            logger.warning("[tech_ranker] K线拉取失败 ts_code={} reason={}", ts_code, e)
    return klines


def _compute_final_score(indicators: dict, weights: dict) -> float:
    """多因子加权总分"""
    return (
        indicators["ma_align_score"]  * weights["ma"] +
        indicators["vol_ratio_score"] * weights["vr"] +
        indicators["gain_score"]      * weights["gain"] +
        indicators["small_cap_score"] * weights["cap"]
    )


def run(state: dict) -> dict:
    stock_pool = state.get("stock_pool", [])
    logger.info("[tech_ranker] 开始技术面排序: {} 只股票", len(stock_pool))

    if not stock_pool:
        return {"ranked_stocks": {"tier1": [], "tier2": []},
                "error_node": None, "error_msg": None}

    ts_codes = [s["ts_code"] for s in stock_pool]
    circ_mv_map = {s["ts_code"]: s.get("circ_mv", 0) for s in stock_pool}
    name_map = {s["ts_code"]: s.get("name", "") for s in stock_pool}

    # 1. 拉取K线
    klines = _fetch_klines(ts_codes)
    logger.info("[tech_ranker] 成功获取K线: {} 只", len(klines))

    weights = {
        "ma":   settings.tech_weight_ma_alignment,
        "vr":   settings.tech_weight_volume_ratio,
        "gain": settings.tech_weight_recent_gain,
        "cap":  settings.tech_weight_small_cap,
    }

    tier1: list[dict] = []
    tier2: list[dict] = []

    for ts_code, df in klines.items():
        try:
            indicators = calc_all(df, circ_mv_map.get(ts_code, 0))
            score = _compute_final_score(indicators, weights)

            item = {
                "ts_code": ts_code,
                "name": name_map.get(ts_code, ""),
                "score": round(score, 3),
                "is_ma_bullish": bool(indicators["is_ma_bullish"]),
                "vol_ratio": round(float(indicators["latest_vol_ratio"]), 2),
                "reason": _format_reason(indicators),
            }

            if score >= 0.7 and indicators["is_ma_bullish"]:
                tier1.append(item)
            elif 0.5 <= score < 0.7:
                tier2.append(item)
        except Exception as e:
            logger.warning("[tech_ranker] 计算失败 ts_code={} reason={}", ts_code, e)

    tier1.sort(key=lambda x: x["score"], reverse=True)
    tier2.sort(key=lambda x: x["score"], reverse=True)

    logger.info("[tech_ranker] 第一梯队 {} 只, 第二梯队 {} 只", len(tier1), len(tier2))

    # 持久化到 Redis，供 dynamic_graph 读取
    ranked = {"tier1": tier1, "tier2": tier2}
    if redis_client is not None:
        try:
            redis_client.set("static:stock_pool", json.dumps(ranked, ensure_ascii=False), ex=86400 * 7)
            logger.debug("[tech_ranker] stock_pool 已写入 Redis")
        except Exception as e:
            logger.warning("[tech_ranker] Redis 写入失败: {}", e)

    return {
        "ranked_stocks": ranked,
        "error_node": None,
        "error_msg": None,
    }


def _format_reason(indicators: dict) -> str:
    parts = []
    if indicators["is_ma_bullish"]:
        parts.append("均线多头")
    parts.append(f"量比{indicators['latest_vol_ratio']:.1f}")
    gain = indicators["recent_gain_10d"] * 100
    parts.append(f"近10日涨幅{gain:+.1f}%")
    return "，".join(parts)
