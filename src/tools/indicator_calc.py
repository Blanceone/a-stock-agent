"""
indicator_calc.py — 技术指标计算模块（红线：所有数值计算必须在此完成）。

⚠️ 禁止让 LLM 生成计算代码或直接推理数值。
所有节点的 MA、量比、涨幅等计算必须调用此模块函数。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def calc_ma(df: pd.DataFrame, windows: list[int] | None = None) -> pd.DataFrame:
    """
    计算多周期移动平均线。
    输入：df 必须含 trade_date(str, 升序排列), close(float)
    输出：在 df 上追加 ma5, ma10, ma20 列并返回
    """
    if windows is None:
        windows = [5, 10, 20]
    df = df.sort_values("trade_date").reset_index(drop=True)
    for w in windows:
        col = f"ma{w}"
        df[col] = df["close"].rolling(window=w).mean()
    return df


def calc_volume_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """
    量比 = 当日成交量 / 近5日平均成交量。
    输入：df 必须含 vol(float)
    输出：追加 vol_ratio 列
    """
    df = df.sort_values("trade_date").reset_index(drop=True)
    avg_5d_vol = df["vol"].rolling(window=5).mean()
    df["vol_ratio"] = df["vol"] / avg_5d_vol.replace(0, np.nan)
    df["vol_ratio"] = df["vol_ratio"].fillna(1.0)
    return df


def calc_recent_gain(df: pd.DataFrame, days: int = 10) -> float:
    """
    近 N 日涨幅 = (最新收盘 - N日前收盘) / N日前收盘。
    返回单一 float 值（如 0.12 表示 +12%）。
    """
    if len(df) < days + 1:
        return 0.0
    latest = df.iloc[-1]["close"]
    base = df.iloc[-(days + 1)]["close"]
    if base == 0:
        return 0.0
    return (latest - base) / base


def is_ma_bullish(df: pd.DataFrame) -> bool:
    """均线多头排列：最新 ma5 > ma10 > ma20"""
    if "ma5" not in df.columns or "ma10" not in df.columns or "ma20" not in df.columns:
        return False
    last = df.iloc[-1]
    return (
        pd.notna(last["ma5"]) and
        pd.notna(last["ma10"]) and
        pd.notna(last["ma20"]) and
        last["ma5"] > last["ma10"] > last["ma20"]
    )


def score_ma_alignment(df: pd.DataFrame) -> float:
    """
    均线多头排列得分：
      多头（ma5>ma10>ma20）→ 1.0
      ma5>ma10              → 0.6
      其余                  → 0.2
    """
    if "ma5" not in df.columns or "ma10" not in df.columns:
        return 0.2
    last = df.iloc[-1]
    if pd.isna(last["ma5"]) or pd.isna(last["ma10"]):
        return 0.2
    if is_ma_bullish(df):
        return 1.0
    if last["ma5"] > last["ma10"]:
        return 0.6
    return 0.2


def score_volume_ratio(vol_ratio: float) -> float:
    """
    量比得分：
      ≥ 3.0 → 1.0
      2.0~3.0 → 0.8
      1.5~2.0 → 0.6
      1.0~1.5 → 0.4
      < 1.0   → 0.1
    """
    if vol_ratio >= 3.0:
        return 1.0
    elif vol_ratio >= 2.0:
        return 0.8
    elif vol_ratio >= 1.5:
        return 0.6
    elif vol_ratio >= 1.0:
        return 0.4
    return 0.1


def score_recent_gain(gain: float) -> float:
    """
    近期涨幅得分（防追高兼顾弹性）：
      5%~15%   → 1.0（最佳启动区间）
      15%~25%  → 0.7
      > 25%    → 0.3（过热）
      0%~5%    → 0.5
      负值     → 0.2
    """
    if 0.05 <= gain < 0.15:
        return 1.0
    elif 0.15 <= gain < 0.25:
        return 0.7
    elif gain >= 0.25:
        return 0.3
    elif 0 <= gain < 0.05:
        return 0.5
    return 0.2


def score_small_cap(circ_mv: float) -> float:
    """
    小盘溢价得分（流通市值，单位：万元）：
      < 200000  (20亿)  → 1.0
      < 500000  (50亿)  → 0.8
      < 1000000 (100亿) → 0.5
      ≥ 1000000         → 0.2
    """
    if circ_mv < 200_000:
        return 1.0
    elif circ_mv < 500_000:
        return 0.8
    elif circ_mv < 1_000_000:
        return 0.5
    return 0.2


def calc_all(df: pd.DataFrame, circ_mv: float) -> dict:
    """
    计算单只股票全部指标并返回得分字典。
    输入：df（含 trade_date, close, vol），circ_mv（流通市值，万元）
    输出：
    {
      "ma_align_score":  float,
      "vol_ratio_score": float,
      "gain_score":      float,
      "small_cap_score": float,
      "is_ma_bullish":   bool,
      "latest_vol_ratio": float,
      "recent_gain_10d": float,
    }
    """
    df = calc_ma(df)
    df = calc_volume_ratio(df)

    last = df.iloc[-1]
    vol_ratio = last.get("vol_ratio", 1.0)
    recent_gain = calc_recent_gain(df, days=10)

    return {
        "ma_align_score":   score_ma_alignment(df),
        "vol_ratio_score":  score_volume_ratio(vol_ratio),
        "gain_score":       score_recent_gain(recent_gain),
        "small_cap_score":  score_small_cap(circ_mv),
        "is_ma_bullish":    is_ma_bullish(df),
        "latest_vol_ratio": vol_ratio,
        "recent_gain_10d":  recent_gain,
    }
