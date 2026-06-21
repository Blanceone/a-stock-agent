"""
resonance_alert.py — 步骤6：三共振条件触发与预警

输入 state:
  news_result: dict              # 来自 news_funnel（粗筛通过的新闻）
  ranked_stocks: dict            # 来自 tech_ranker

输出 state 增量:
  resonance_alerts: list[dict]   # 预警信号列表
  capital_flow_cache: dict       # 更新后的资金流缓存

三共振条件（全部满足才预警）:
  1. 消息面评分 > resonance_news_score_threshold (默认 0.7)
  2. 主力净流入占成交额比例 > resonance_capital_inflow_pct (默认 2%)
  3. 量比 > resonance_volume_ratio (默认 2.0)
"""
from __future__ import annotations

import time

import pandas as pd
from loguru import logger

from config.settings import settings
from src.infrastructure.data_fetcher import fetch_moneyflow_intraday
from src.tools.indicator_calc import calc_volume_ratio


def _check_capital_flow(ts_code: str) -> dict:
    """
    检查单只股票的主力资金流向。
    返回 {"inflow_pct": float, "net_buy_amt": float, "source": str}
    """
    try:
        df = fetch_moneyflow_intraday(ts_code)
        if df.empty:
            return {"inflow_pct": 0, "net_buy_amt": 0, "source": "empty"}

        # a-stock-data 字段: buy_lg_amount(大单买入), sell_lg_amount(大单卖出)
        #                  buy_elg_amount(特大单买入), sell_elg_amount(特大单卖出)
        if "buy_lg_amount" in df.columns:
            net_buy = (df["buy_lg_amount"].sum() + df.get("buy_elg_amount", pd.Series([0])).sum()
                       - df["sell_lg_amount"].sum() - df.get("sell_elg_amount", pd.Series([0])).sum())
        else:
            net_buy = 0

        turnover = df.get("trade_amount", pd.Series([1])).sum()
        inflow_pct = net_buy / max(turnover, 1) * 100

        return {
            "inflow_pct": round(inflow_pct, 2),
            "net_buy_amt": round(net_buy, 0),
            "source": "a-stock-data",
        }
    except Exception as e:
        logger.warning("[resonance] 资金流查询失败 ts_code={} reason={}", ts_code, e)
        return {"inflow_pct": 0, "net_buy_amt": 0, "source": "error"}


def _check_volume_ratio(ts_code: str) -> float:
    """检查量比"""
    try:
        from datetime import datetime, timedelta
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
        from src.infrastructure.data_fetcher import fetch_daily
        df = fetch_daily(ts_code, start_date, end_date)
        if df.empty or len(df) < 10:
            return 0
        return calc_volume_ratio(df)
    except Exception as e:
        logger.warning("[resonance] 量比计算失败 ts_code={} reason={}", ts_code, e)
        return 0


def run(state: dict) -> dict:
    news_result = state.get("news_result")
    if news_result is None:
        return {"resonance_alerts": []}

    ts_codes = news_result.get("related_ts_codes", [])
    news_score = news_result.get("news_score", 0)
    news_title = news_result.get("news_title", "")

    if not ts_codes:
        logger.debug("[resonance] 无关联个股，跳过")
        return {"resonance_alerts": []}

    logger.info("[resonance] 检查 {} 只股票三共振条件 news_score={:.2f}",
                len(ts_codes), news_score)

    alerts: list[dict] = []
    threshold_news = settings.resonance_news_score_threshold
    threshold_capital = settings.resonance_capital_inflow_pct * 100  # 转百分比
    threshold_vr = settings.resonance_volume_ratio

    for ts_code in ts_codes:
        # 条件1：消息面
        if news_score < threshold_news:
            continue

        # 条件2：资金面
        flow = _check_capital_flow(ts_code)
        if flow["inflow_pct"] < threshold_capital:
            continue

        # 条件3：技术面
        vr = _check_volume_ratio(ts_code)
        if vr < threshold_vr:
            continue

        # 三共振满足
        alert = {
            "ts_code": ts_code,
            "news_title": news_title,
            "news_score": news_score,
            "capital_inflow_pct": flow["inflow_pct"],
            "volume_ratio": round(vr, 2),
            "timestamp": int(time.time()),
        }
        alerts.append(alert)
        logger.warning(
            "🚨 [RESONANCE ALERT] {} | 消息{:.2f} 资金{:.1f}% 量比{:.1f} | {}",
            ts_code, news_score, flow["inflow_pct"], vr, news_title,
        )

    if not alerts:
        logger.info("[resonance] 无三共振信号")

    return {
        "resonance_alerts": alerts,
        "error_node": None,
        "error_msg": None,
    }
