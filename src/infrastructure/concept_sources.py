"""
concept_sources.py — 多源概念板块数据采集

数据源：
  - akshare 东方财富概念板块（免费，无需积分）
  - tushare 概念板块（需积分，用户 2000 积分充足）

返回统一格式：
  [{"concept": "固态电池", "stocks": ["300750.SZ", ...], "source": "akshare_em"}, ...]
"""
from __future__ import annotations

import time
from loguru import logger

from config.settings import settings


def _em_code_to_ts_code(em_code: str) -> str:
    """东方财富6位纯数字代码 → tushare ts_code 格式"""
    code = str(em_code).strip()
    if code.startswith("6"):
        return f"{code}.SH"
    elif code.startswith(("0", "3")):
        return f"{code}.SZ"
    elif code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


# 过滤掉无意义的短线情绪概念（非产业概念）
_FILTER_PREFIXES = ("昨日", "今日", "近端", "远端", "连续", "次新", "竞价")


def _should_filter(name: str) -> bool:
    """判断是否应过滤掉的概念（短线交易情绪类）"""
    for prefix in _FILTER_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def fetch_akshare_concepts(max_boards: int = 150, sleep_sec: float = 0.3) -> list[dict]:
    """
    从 akshare 获取东方财富概念板块列表 + 成分股。

    参数:
      max_boards: 最多获取多少个板块（0=全部，默认150取最热门）
      sleep_sec: 成分股请求间隔秒数（防限流）

    返回: [{"concept": "...", "stocks": ["300750.SZ",...], "source": "akshare_em"}, ...]
    """
    try:
        import akshare as ak
    except ImportError:
        logger.warning("[ConceptSources] akshare 未安装，跳过东财概念数据")
        return []

    results: list[dict] = []
    filtered_count = 0
    try:
        df_boards = ak.stock_board_concept_name_em()
        if df_boards is None or df_boards.empty:
            logger.warning("[ConceptSources] akshare 东财概念列表为空")
            return []

        board_names = df_boards["板块名称"].tolist()
        # 过滤无意义的短线情绪概念
        board_names = [n for n in board_names if not _should_filter(n)]
        if max_boards > 0:
            board_names = board_names[:max_boards]

        logger.info("[ConceptSources] akshare 东财概念板块: 总 {} 个, 过滤后取 {} 个",
                     len(df_boards), len(board_names))

        for idx, board_name in enumerate(board_names, 1):
            try:
                df_cons = ak.stock_board_concept_cons_em(symbol=board_name)
                if df_cons is None or df_cons.empty:
                    continue

                stocks = []
                for _, row in df_cons.iterrows():
                    code = str(row.get("代码", "")).strip()
                    if code:
                        stocks.append(_em_code_to_ts_code(code))

                if stocks:
                    results.append({
                        "concept": board_name,
                        "stocks": stocks,
                        "source": "akshare_em",
                    })

                if idx % 30 == 0:
                    logger.info("[ConceptSources] akshare 进度 {}/{} (已获取 {} 个板块)",
                                idx, len(board_names), len(results))

            except Exception as e:
                logger.warning("[ConceptSources] akshare 板块 '{}' 成分股失败: {}", board_name, e)

            if sleep_sec > 0:
                time.sleep(sleep_sec)

        logger.info("[ConceptSources] akshare 完成: {} 个板块（过滤 {} 个）", len(results), filtered_count)
    except Exception as e:
        logger.warning("[ConceptSources] akshare 东财概念获取失败: {}", e)

    return results


def fetch_tushare_concepts(sleep_sec: float = 0.25) -> list[dict]:
    """
    从 tushare 获取概念板块 + 成分股。

    使用 API:
      - pro.concept()           → 概念列表
      - pro.concept_detail(id)  → 概念成分股

    返回: [{"concept": "...", "stocks": ["300750.SZ",...], "source": "tushare"}, ...]
    """
    try:
        import tushare as ts
    except ImportError:
        logger.warning("[ConceptSources] tushare 未安装，跳过 tushare 概念数据")
        return []

    token = settings.tushare_token
    if not token:
        logger.warning("[ConceptSources] TUSHARE_TOKEN 未配置，跳过 tushare 概念数据")
        return []

    results: list[dict] = []
    try:
        pro = ts.pro_api(token)

        df_concepts = pro.concept()
        if df_concepts is None or df_concepts.empty:
            logger.warning("[ConceptSources] tushare 概念列表为空")
            return []

        logger.info("[ConceptSources] tushare 概念板块: {} 个", len(df_concepts))

        for idx, row in df_concepts.iterrows():
            concept_id = row.get("id", "")
            concept_name = row.get("name", "")
            if not concept_id or not concept_name:
                continue

            try:
                df_detail = pro.concept_detail(id=concept_id, fields="ts_code")
                if df_detail is None or df_detail.empty:
                    continue

                stocks = df_detail["ts_code"].tolist()
                if stocks:
                    results.append({
                        "concept": concept_name,
                        "stocks": stocks,
                        "source": "tushare",
                        "board_code": concept_id,
                    })

                if (idx + 1) % 30 == 0:
                    logger.info("[ConceptSources] tushare 进度 {}/{} (已获取 {} 个板块)",
                                idx + 1, len(df_concepts), len(results))

            except Exception as e:
                logger.warning("[ConceptSources] tushare 概念 '{}' 成分股失败: {}", concept_name, e)

            if sleep_sec > 0:
                time.sleep(sleep_sec)

        logger.info("[ConceptSources] tushare 完成: {} 个板块", len(results))
    except Exception as e:
        logger.warning("[ConceptSources] tushare 概念获取失败: {}", e)

    return results
