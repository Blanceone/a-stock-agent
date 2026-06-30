"""
resonance_alert.py — 步骤6：多因子共振触发与预警（LLM 综合判断）

输入 state:
  news_result: dict              # 来自 news_funnel（粗筛通过的新闻）
  ranked_stocks: dict            # 来自 tech_ranker

输出 state 增量:
  resonance_alerts: list[dict]   # 预警信号列表
  capital_flow_cache: dict       # 更新后的资金流缓存

判断方式：
  1. 收集所有关联股票的 news_score + 资金流 + 量比
  2. LLM flash 综合判断是否预警（不再使用硬编码三阈值 AND）
  3. 降级：LLM 失败时回退旧版三阈值 AND 逻辑
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from loguru import logger

from config.settings import settings
from src.infrastructure.data_fetcher import fetch_moneyflow_intraday
from src.infrastructure.database import redis_client
from src.nodes.llm_utils import call_llm_json, load_prompt
from src.tools.indicator_calc import calc_volume_ratio


def _check_capital_flow(ts_code: str) -> dict:
    """
    检查单只股票的主力资金流向。
    返回 {"inflow_pct": float, "net_buy_amt": float, "source": str}
    """
    try:
        data = fetch_moneyflow_intraday(ts_code)
        net_inflow = data.get("net_inflow", 0)
        net_inflow_pct = data.get("net_inflow_pct", 0)
        source = data.get("source", "eastmoney-push2")

        return {
            "inflow_pct": round(net_inflow_pct, 2),
            "net_buy_amt": round(net_inflow, 0),
            "source": source,
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
        df = calc_volume_ratio(df)
        return float(df.iloc[-1].get("vol_ratio", 0))
    except Exception as e:
        logger.warning("[resonance] 量比计算失败 ts_code={} reason={}", ts_code, e)
        return 0


def _collect_stock_data(ts_codes: list[str]) -> list[dict]:
    """收集所有关联股票的多因子数据"""
    stock_data = []
    for i, ts_code in enumerate(ts_codes):
        if i > 0:
            time.sleep(2)  # 东财 API 限流保护（push2 接口需较长间隔）

        flow = _check_capital_flow(ts_code)
        vr = _check_volume_ratio(ts_code)

        stock_data.append({
            "ts_code": ts_code,
            "capital_inflow_pct": flow["inflow_pct"],
            "net_buy_amt": flow["net_buy_amt"],
            "volume_ratio": round(vr, 2),
        })
    return stock_data


def _llm_judge(news_title: str, news_score: float, stock_data: list[dict]) -> list[dict]:
    """LLM flash 综合判断是否预警"""
    template = load_prompt("resonance_judge")
    prompt = template.format(
        news_title=news_title,
        news_score=f"{news_score:.2f}",
        stocks_data=json.dumps(stock_data, ensure_ascii=False),
    )
    try:
        result = call_llm_json(prompt, model="flash", max_tokens=2048)
        if isinstance(result, list):
            return result
        return []
    except Exception as e:
        logger.warning("[resonance] LLM 综合判断失败: {}", e)
        return []


def _fallback_judge(news_score: float, stock_data: list[dict]) -> list[dict]:
    """降级：旧版三阈值 AND 逻辑"""
    threshold_news = settings.resonance_news_score_threshold
    threshold_capital = settings.resonance_capital_inflow_pct  # 已是小数形式(0.02)
    threshold_vr = settings.resonance_volume_ratio

    alerts = []
    if news_score < threshold_news:
        return alerts

    for sd in stock_data:
        if sd["capital_inflow_pct"] >= threshold_capital and sd["volume_ratio"] >= threshold_vr:
            alerts.append({
                "ts_code": sd["ts_code"],
                "alert": True,
                "confidence": 0.7,
                "reason": f"三阈值AND降级: 消息{news_score:.2f} 资金{sd['capital_inflow_pct']:.1%} 量比{sd['volume_ratio']:.1f}",
            })
    return alerts


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

    logger.info("[resonance] 检查 {} 只股票多因子共振 news_score={:.2f}",
                len(ts_codes), news_score)

    # 1. 收集所有股票的多因子数据
    stock_data = _collect_stock_data(ts_codes)

    # 2. LLM 综合判断（失败则降级）
    llm_results = _llm_judge(news_title, news_score, stock_data)
    if not llm_results:
        logger.info("[resonance] LLM 判断为空，降级为三阈值 AND")
        llm_results = _fallback_judge(news_score, stock_data)

    # 3. 构建预警列表
    alerts: list[dict] = []
    for item in llm_results:
        if not item.get("alert", False):
            continue

        ts_code = item.get("ts_code", "")
        # 找到对应的 stock_data
        sd = next((s for s in stock_data if s["ts_code"] == ts_code), {})

        alert = {
            "ts_code": ts_code,
            "news_title": news_title,
            "news_score": news_score,
            "capital_inflow_pct": sd.get("capital_inflow_pct", 0),
            "volume_ratio": sd.get("volume_ratio", 0),
            "confidence": item.get("confidence", 0.5),
            "reason": item.get("reason", ""),
            "timestamp": int(time.time()),
        }
        alerts.append(alert)
        logger.warning(
            "🚨 [RESONANCE ALERT] {} | conf={:.2f} | {} | {}",
            ts_code, alert["confidence"], alert["reason"], news_title,
        )

    if not alerts:
        logger.info("[resonance] 无共振信号")

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
