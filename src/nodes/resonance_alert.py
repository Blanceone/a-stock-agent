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

import json
import time
from datetime import datetime

from loguru import logger

from config.settings import settings
from src.infrastructure.data_fetcher import fetch_moneyflow_intraday
from src.infrastructure.database import redis_client
from src.tools.indicator_calc import calc_volume_ratio


def _check_capital_flow(ts_code: str) -> dict:
    """
    检查单只股票的主力资金流向。
    返回 {"inflow_pct": float, "net_buy_amt": float, "source": str}
    """
    try:
        data = fetch_moneyflow_intraday(ts_code)
        # fetch_moneyflow_intraday 返回 dict: {ts_code, net_inflow, net_inflow_pct, timestamp}
        net_inflow = data.get("net_inflow", 0)
        net_inflow_pct = data.get("net_inflow_pct", 0)

        return {
            "inflow_pct": round(net_inflow_pct, 2),
            "net_buy_amt": round(net_inflow, 0),
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
        df = calc_volume_ratio(df)  # 返回 DataFrame 追加 vol_ratio 列
        return float(df.iloc[-1].get("vol_ratio", 0))
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

    # 持久化到 Redis: dynamic:alerts:{date}
    if alerts and redis_client is not None:
        try:
            today = datetime.now().strftime("%Y%m%d")
            key = f"dynamic:alerts:{today}"
            for alert in alerts:
                redis_client.lpush(key, json.dumps(alert, ensure_ascii=False))
            redis_client.expire(key, 86400 * 3)  # 保3天
            logger.debug("[resonance] {} 条预警已写入 Redis {}", len(alerts), key)
        except Exception as e:
            logger.warning("[resonance] Redis 写入失败: {}", e)

    return {
        "resonance_alerts": alerts,
        "error_node": None,
        "error_msg": None,
    }
