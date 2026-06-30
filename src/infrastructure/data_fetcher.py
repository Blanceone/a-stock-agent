"""
data_fetcher.py — 结构化数据降级链。

信源降级链规则（红线）：
  行情/财务数据：Tushare Pro（首选）→ a-stock-data（捕获异常后 fallback）
  主营业务文本：东方财富F10 → a-stock-data → AKShare → Tushare兜底
  每次降级必须在日志中记录降级事件。
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

import pandas as pd
import requests
import tushare as ts
from loguru import logger

from config.settings import settings


# ── 东财请求头（push2接口需要 UA+Referer，否则 RemoteDisconnected）──────────
_EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


# ── 异常定义 ──────────────────────────────────────────────────────────────────
class TushareQuotaError(Exception):
    """Tushare 积分不足"""


class TushareRateLimitError(Exception):
    """Tushare 接口超频"""


class DataFetchError(Exception):
    """所有数据源均失败"""


# ── Tushare 客户端 ────────────────────────────────────────────────────────────
def _tushare_pro() -> ts.pro_api:
    """获取 Tushare Pro 客户端单例"""
    if not settings.tushare_token:
        raise TushareQuotaError("TUSHARE_TOKEN 未配置")
    return ts.pro_api(settings.tushare_token)


def _handle_tushare_error(e: Exception, api_name: str, ts_code: str) -> None:
    """统一解析 Tushare 异常并转换为对应类型"""
    msg = str(e)
    if "积分" in msg or "2020" in msg:
        raise TushareQuotaError(f"[{api_name}] {ts_code} 积分不足") from e
    if "每分钟" in msg or "频次" in msg:
        raise TushareRateLimitError(f"[{api_name}] {ts_code} 超频") from e
    raise


# ── 行情/财务数据 ─────────────────────────────────────────────────────────────
def fetch_stock_basic() -> pd.DataFrame:
    """
    获取全A股基础信息（纯 Tushare，无降级，数据唯一权威源）。
    circ_mv 从 daily_basic 单独获取并合并（stock_basic API 不含 circ_mv 字段）。
    返回列：ts_code, name, industry, circ_mv, list_status
    """
    from datetime import datetime, timedelta

    pro = _tushare_pro()
    logger.info("[DataFetcher] 拉取全A股基础信息 (tushare.stock_basic)")

    # L1: stock_basic（不含 circ_mv）
    try:
        df = pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,name,industry,list_status",
        )
    except Exception as e:
        _handle_tushare_error(e, "stock_basic", "ALL")

    # 确保 circ_mv 列存在（默认 None）
    df["circ_mv"] = None

    # L2: 从 daily_basic 获取最新交易日的 circ_mv 并合并
    try:
        # 尝试最近 5 个自然日，找到有数据的交易日
        for days_ago in range(5):
            trade_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y%m%d")
            df_mv = pro.daily_basic(
                trade_date=trade_date,
                fields="ts_code,circ_mv",
            )
            if df_mv is not None and not df_mv.empty:
                logger.info(
                    "[DataFetcher] daily_basic circ_mv 获取成功: "
                    "trade_date={} 共 {} 条", trade_date, len(df_mv),
                )
                df = df.merge(df_mv, on="ts_code", how="left", suffixes=("", "_mv"))
                # merge 后 circ_mv 列可能变为 circ_mv_mv（如果原列也有值）
                if "circ_mv_mv" in df.columns:
                    df["circ_mv"] = df["circ_mv"].where(df["circ_mv"].notna(), df["circ_mv_mv"])
                    df = df.drop(columns=["circ_mv_mv"])
                break
        else:
            logger.warning("[DataFetcher] daily_basic 近5天均无数据，circ_mv 为空")
    except Exception as e:
        logger.warning("[DataFetcher] daily_basic circ_mv 获取失败: {}，circ_mv 为空", e)

    logger.info("[DataFetcher] 全A股基础信息: {} 条", len(df))
    return df


def fetch_daily(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    获取日线行情，降级链：Tushare pro.daily → a-stock-data。
    返回列：ts_code, trade_date, open, high, low, close, vol, amount
    """
    # L1: Tushare
    try:
        pro = _tushare_pro()
        df = pro.daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,open,high,low,close,vol,amount",
        )
        if df is not None and not df.empty:
            return df.sort_values("trade_date").reset_index(drop=True)
        raise DataFetchError(f"[daily] {ts_code} Tushare 返回空")
    except (TushareQuotaError, TushareRateLimitError) as e:
        logger.warning(
            "[DataFallback] ts_code={} api=tushare.daily "
            "reason={} fallback_to=a-stock-data", ts_code, e
        )
    except Exception as e:
        logger.warning(
            "[DataFallback] ts_code={} api=tushare.daily "
            "reason={} fallback_to=a-stock-data", ts_code, e
        )

    # L2: a-stock-data（东财日线接口）
    try:
        code = ts_code.split(".")[0]
        market = "1" if ts_code.endswith(".SH") else "0"
        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
            f"secid={market}.{code}&fields1=f1&fields2=f51,f52,f53,f54,f55,f56,f57"
            f"&klt=101&fqt=1&beg={start_date}&end={end_date}"
        )
        resp = requests.get(url, timeout=10, headers=_EASTMONEY_HEADERS).json()
        rows = [r.split(",") for r in resp["data"]["klines"]]
        df = pd.DataFrame(rows, columns=["trade_date", "open", "close", "high",
                                          "low", "vol", "amount"])
        for col in ["open", "close", "high", "low", "vol", "amount"]:
            df[col] = pd.to_numeric(df[col])
        df["ts_code"] = ts_code
        return df.sort_values("trade_date").reset_index(drop=True)
    except Exception as e2:
        raise DataFetchError(f"[daily] {ts_code} 所有来源均失败: {e2}") from e2


def fetch_moneyflow_intraday(ts_code: str) -> dict:
    """
    盘中主力净流入（直接调 a-stock-data 东财 push2 接口，不走 Tushare）。
    返回：{ts_code, net_inflow, net_inflow_pct, timestamp}
    """
    code = ts_code.split(".")[0]
    market = "1" if ts_code.endswith(".SH") else "0"
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/get?"
        f"secid={market}.{code}"
        f"&fields=f62,f184,f66,f69,f72,f75,f78,f81,f84,f87"
    )
    # 重试 5 次（东财 push2 频繁 RemoteDisconnected，需更长退避）
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            resp = requests.get(url, timeout=10, headers=_EASTMONEY_HEADERS).json()
            data = resp.get("data") or {}
            net_inflow = data.get("f62", 0) or 0
            net_inflow_pct = (data.get("f184", 0) or 0) / 100
            return {
                "ts_code": ts_code,
                "net_inflow": net_inflow,
                "net_inflow_pct": net_inflow_pct,
                "timestamp": datetime.now().isoformat(),
            }
        except (requests.ConnectionError, requests.Timeout) as e:
            # 瞬态错误：重试
            last_err = e
            if attempt < 4:
                time.sleep(2.0 * (attempt + 1))  # 退避: 2s, 4s, 6s, 8s
        except Exception as e:
            # 非瞬态错误：立即抛出，不浪费时间重试
            raise

    # 5次重试均失败（瞬态错误）
    raise DataFetchError(
        f"[moneyflow] {ts_code} 重试5次仍失败: {last_err}"
    ) if last_err else DataFetchError(f"[moneyflow] {ts_code} 未知错误")


def fetch_top_list(trade_date: str) -> pd.DataFrame:
    """
    T+1 龙虎榜数据，仅 Tushare pro.top_list（T+1 数据，盘后调用）。
    返回列：ts_code, trade_date, name, reason, buy_amount, sell_amount
    """
    pro = _tushare_pro()
    logger.info("[DataFetcher] 拉取龙虎榜 trade_date={}", trade_date)
    try:
        df = pro.top_list(
            trade_date=trade_date,
            fields="ts_code,trade_date,name,reason,buy_amount,sell_amount",
        )
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        _handle_tushare_error(e, "top_list", trade_date)


# ── 主营业务文本两级降级链 ────────────────────────────────────────────────────
def fetch_business_description(ts_code: str, company_name: str) -> str:
    """
    主营业务文本，两级降级：
    L1: 东方财富 F10 核心题材（支持 SH/SZ/BJ）
    L2: Tushare pro.stock_company main_business 字段（兜底）
    """
    code = ts_code.split(".")[0]
    # 市场后缀映射：SH / SZ / BJ
    if ts_code.endswith(".SH"):
        suffix = "SH"
    elif ts_code.endswith(".BJ"):
        suffix = "BJ"
    else:
        suffix = "SZ"

    # L1: 东方财富 F10
    try:
        url = (
            f"https://datacenter.eastmoney.com/securities/api/data/v1/get?"
            f"reportName=RPT_F10_CORETHEME_BOARDTYPE"
            f"&columns=ALL&filter=(SECUCODE=%22{code}.{suffix}%22)"
            f"&pageSize=50&pageNumber=1"
        )
        resp = requests.get(url, timeout=10).json()
        items = (resp.get("result") or {}).get("data", [])
        if items:
            text = "; ".join(
                f"{it.get('BOARD_NAME', '')}({it.get('BOARD_TYPE', '')})"
                for it in items
            )
            logger.debug("[DataFetcher] L1 东财F10 命中: {}", ts_code)
            return f"核心题材：{text}"
    except Exception as e:
        logger.debug("[DataFallback] ts_code={} api=东财F10 reason={}", ts_code, e)

    # L2: Tushare stock_company main_business（兜底）
    try:
        pro = _tushare_pro()
        df = pro.stock_company(ts_code=ts_code, fields="ts_code,main_business")
        if df is not None and not df.empty and df.iloc[0]["main_business"]:
            text = df.iloc[0]["main_business"]
            logger.debug("[DataFetcher] L2 Tushare兜底 命中: {}", ts_code)
            return f"主营业务：{text}"
    except Exception as e:
        logger.debug("[DataFallback] ts_code={} api=tushare兜底 reason={}", ts_code, e)

    raise DataFetchError(f"[business_desc] {ts_code} 两级来源均失败")
