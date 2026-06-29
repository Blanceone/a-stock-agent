"""
concept_sources.py — 多源概念板块数据采集 + LLM 智能筛选

数据源：
  - akshare 东方财富概念板块（免费，无需积分）
  - tushare 概念板块（需5000+积分，当前不可用，静默跳过）

流程：
  1. 获取全部东财概念名称（不做前缀过滤）
  2. LLM 分批评估：国家政策关联度评分 + 分类 + 保留/丢弃
  3. 按政策关联度降序排序，仅保留有价值的概念
  4. 仅为保留的概念获取成分股（节省API调用）

返回统一格式：
  [{"concept": "固态电池", "stocks": ["300750.SZ", ...], "source": "akshare_em",
    "score": 8, "category": "新能源/新材料"}, ...]
"""
from __future__ import annotations

import json
import time
from loguru import logger

from config.settings import settings


# ── 股票代码转换 ────────────────────────────────────────────────────────────────

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


# ── 缓存 key ───────────────────────────────────────────────────────────────────

_CACHE_KEY = "cache:concept_llm_eval"
_CACHE_TTL = 7 * 86400  # 7 天


# ── LLM 评估 Prompt ───────────────────────────────────────────────────────────

_EVAL_SYSTEM = "你是一位资深A股投研分析师，熟悉中国宏观经济与产业政策。仅输出JSON，不要解释。"

_EVAL_PROMPT_TEMPLATE = """请对以下A股概念板块进行评估。

对每个概念判断：
1. **policy_score** (0-10分)：与国家政策的关联度
   - 9-10分：直接对应国家重大战略（如碳中和、半导体自主可控、数字经济、军民融合）
   - 6-8分：与产业政策密切相关（如新能源、新材料、高端制造、生物医药）
   - 3-5分：有一定产业意义但政策关联较弱（如消费电子、家电、食品饮料细分）
   - 0-2分：纯交易情绪概念、无产业意义、过于泛化

2. **category**：所属产业分类（如 新能源/新材料/半导体/医药生物/消费升级/数字经济/高端制造/军工/金融/传统周期/其他）

3. **keep** (true/false)：是否值得保留
   - 保留标准：有明确产业方向、与国家政策或长期投资主题相关
   - 丢弃标准：纯短线交易情绪（如昨日涨停、近端次新）、过于泛化、无产业方向

返回 JSON 数组：
[{{"name": "概念名", "score": 数字, "category": "分类", "keep": true/false}}]

概念列表：
{concepts_json}"""


# ── LLM 智能筛选 ──────────────────────────────────────────────────────────────

def llm_filter_concepts(concept_names: list[str]) -> list[dict]:
    """
    通过 LLM 对概念进行智能筛选和排序。

    流程：
      - 优先读取 Redis 缓存（7天有效）
      - 仅对未缓存的概念调用 LLM 分批评估
      - 按 policy_score 降序排序，返回 keep=True 的概念

    返回: [{"name": "...", "score": int, "category": "..."}, ...]
    """
    from src.nodes.llm_utils import call_llm_json
    import src.infrastructure.database as db

    # 读取已缓存的评估
    cached = _load_cached_eval(db.redis_client)

    # 分离已缓存 / 待评估
    results = []
    to_eval = []
    for name in concept_names:
        if name in cached:
            entry = cached[name]
            if entry.get("keep", False):
                results.append(entry)
        else:
            to_eval.append(name)

    if to_eval:
        logger.info("[ConceptFilter] LLM 评估: {} 个新概念需评估 ({} 个已缓存)",
                     len(to_eval), len(concept_names) - len(to_eval))

        batch_size = 80
        new_eval = {}
        for i in range(0, len(to_eval), batch_size):
            batch = to_eval[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(to_eval) + batch_size - 1) // batch_size
            logger.info("[ConceptFilter] LLM 批次 {}/{} ({} 个概念)",
                         batch_num, total_batches, len(batch))

            try:
                prompt = _EVAL_PROMPT_TEMPLATE.format(
                    concepts_json=json.dumps(batch, ensure_ascii=False)
                )
                resp = call_llm_json(
                    prompt, model="flash", system=_EVAL_SYSTEM,
                    max_tokens=4096, use_cache=True,
                )
                if isinstance(resp, list):
                    for item in resp:
                        name = item.get("name", "")
                        if name:
                            entry = {
                                "name": name,
                                "score": int(item.get("score", 0)),
                                "category": item.get("category", "其他"),
                                "keep": bool(item.get("keep", False)),
                            }
                            new_eval[name] = entry
                            if entry["keep"]:
                                results.append(entry)
                else:
                    logger.warning("[ConceptFilter] LLM 批次 {} 返回非数组", batch_num)
            except Exception as e:
                logger.warning("[ConceptFilter] LLM 批次 {} 失败: {}", batch_num, e)

            # 避免 LLM 限流
            if i + batch_size < len(to_eval):
                time.sleep(1)

        # 保存新评估结果到 Redis
        if new_eval:
            _save_cached_eval(db.redis_client, cached, new_eval)
    else:
        logger.info("[ConceptFilter] 全部 {} 个概念已有 LLM 缓存评估", len(concept_names))

    # 按 policy_score 降序排序
    results.sort(key=lambda x: x.get("score", 0), reverse=True)

    kept = len(results)
    dropped = len(concept_names) - kept
    logger.info("[ConceptFilter] 筛选完成: 保留 {} 个, 丢弃 {} 个", kept, dropped)

    # 打印 Top 10 高分概念供参考
    if results:
        top10 = results[:10]
        logger.info("[ConceptFilter] Top10: {}",
                     ", ".join(f"{r['name']}({r['score']}分)" for r in top10))

    return results


# ── 缓存读写 ──────────────────────────────────────────────────────────────────

def _load_cached_eval(redis_client) -> dict:
    """从 Redis 加载已缓存的 LLM 评估结果"""
    if redis_client is None:
        return {}
    try:
        raw = redis_client.get(_CACHE_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return {}


def _save_cached_eval(redis_client, old_cache: dict, new_eval: dict) -> None:
    """合并新旧评估结果并保存到 Redis"""
    if redis_client is None:
        return
    old_cache.update(new_eval)
    try:
        redis_client.setex(_CACHE_KEY, _CACHE_TTL,
                           json.dumps(old_cache, ensure_ascii=False))
    except Exception as e:
        logger.warning("[ConceptFilter] 缓存写入失败: {}", e)


# ── 降级策略 ──────────────────────────────────────────────────────────────────

_FILTER_PREFIXES = ("昨日", "今日", "近端", "远端", "连续", "次新", "竞价")


def _fallback_filter(concept_names: list[str], max_boards: int = 150) -> list[str]:
    """LLM 不可用时的降级过滤：前缀过滤 + 截断"""
    names = [n for n in concept_names if not n.startswith(tuple(_FILTER_PREFIXES))]
    if max_boards > 0:
        names = names[:max_boards]
    return names


# ── 成分股获取 ─────────────────────────────────────────────────────────────────

def fetch_concept_stocks(board_name: str, sleep_sec: float = 0.3) -> list[str]:
    """获取单个概念板块的成分股列表（ts_code 格式），含 3 次重试"""
    import akshare as ak
    for attempt in range(3):
        try:
            df_cons = ak.stock_board_concept_cons_em(symbol=board_name)
            if df_cons is None or df_cons.empty:
                return []
            stocks = []
            for _, row in df_cons.iterrows():
                code = str(row.get("代码", "")).strip()
                if code:
                    stocks.append(_em_code_to_ts_code(code))
            return stocks
        except Exception as e:
            if attempt < 2:
                time.sleep(0.5 * (2 ** attempt))
            else:
                logger.warning("[ConceptSources] akshare 板块 '{}' 成分股失败(重试{}次): {}", board_name, attempt + 1, e)
    return []


# ── 主入口 ────────────────────────────────────────────────────────────────────

def fetch_akshare_concepts(
    max_boards: int = 0,
    sleep_sec: float = 0.3,
    use_llm: bool = True,
) -> list[dict]:
    """
    从 akshare 获取东方财富概念板块 + LLM 智能筛选 + 成分股。

    流程：
      1. 获取全部概念名称（不做前缀过滤）
      2. LLM 评估筛选（按政策关联度排序，丢弃无价值概念）
      3. 仅为保留的概念获取成分股

    参数:
      max_boards: LLM不可用时的降级截断数（默认0=不截断）
      sleep_sec: 成分股请求间隔秒数（防限流）
      use_llm: 是否使用 LLM 筛选（False 则退化为前缀过滤+截断）

    返回: [{"concept": "...", "stocks": [...], "source": "akshare_em",
            "score": 8, "category": "..."}, ...]
    """
    try:
        import akshare as ak
    except ImportError:
        logger.warning("[ConceptSources] akshare 未安装，跳过东财概念数据")
        return []

    # Step 1: 获取全部概念名称
    try:
        df_boards = ak.stock_board_concept_name_em()
        if df_boards is None or df_boards.empty:
            logger.warning("[ConceptSources] akshare 东财概念列表为空")
            return []

        all_names = df_boards["板块名称"].tolist()
        logger.info("[ConceptSources] akshare 东财概念: {} 个", len(all_names))
    except Exception as e:
        logger.warning("[ConceptSources] akshare 东财概念列表获取失败: {}", e)
        return []

    # Step 2: LLM 筛选 或 降级过滤
    selected_concepts = []  # [(name, score, category), ...]

    if use_llm:
        try:
            llm_results = llm_filter_concepts(all_names)
            selected_concepts = [
                (r["name"], r["score"], r["category"])
                for r in llm_results
            ]
        except Exception as e:
            logger.warning("[ConceptSources] LLM 筛选失败，降级为前缀过滤: {}", e)

    if not selected_concepts:
        # 降级：前缀过滤 + 截断
        fallback_names = _fallback_filter(all_names, max_boards or 150)
        selected_concepts = [(n, 0, "") for n in fallback_names]
        logger.info("[ConceptSources] 降级过滤: {} 个概念", len(selected_concepts))

    # Step 3: 仅为选中的概念获取成分股
    results = []
    total = len(selected_concepts)
    for idx, (name, score, category) in enumerate(selected_concepts, 1):
        stocks = fetch_concept_stocks(name, sleep_sec=sleep_sec)
        if stocks:
            results.append({
                "concept": name,
                "stocks": stocks,
                "source": "akshare_em",
                "score": score,
                "category": category,
            })

        if idx % 30 == 0:
            logger.info("[ConceptSources] 成分股进度 {}/{} (已获取 {} 个)",
                         idx, total, len(results))

        if sleep_sec > 0:
            time.sleep(sleep_sec)

    logger.info("[ConceptSources] 完成: {} 个概念板块（共 {} 个候选）",
                 len(results), total)
    return results


# ── tushare 数据源（需5000+积分，当前不可用）─────────────────────────────────────

def fetch_tushare_concepts(sleep_sec: float = 0.25) -> list[dict]:
    """
    从 tushare 获取概念板块 + 成分股。
    当前2000积分不可用，保留代码供积分升级后自动生效。
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
        err_msg = str(e)
        if "接口名" in err_msg or "权限" in err_msg:
            logger.warning("[ConceptSources] tushare concept 接口不可用（需5000+积分），"
                           "跳过 tushare 概念数据，仅使用 akshare 东财概念")
        else:
            logger.warning("[ConceptSources] tushare 概念获取失败: {}", err_msg)

    return results


# ── 概念股票语义增强 ────────────────────────────────────────────────────────

def enrich_concept_stocks(
    concept_name: str,
    stocks_detail: dict[str, dict],
    n_results: int = 50,
) -> dict[str, dict]:
    """
    通过 ChromaDB 语义搜索增强概念的股票列表并打分。

    流程：
      1. 用概念名查询 ChromaDB，获取语义相似的股票
      2. 将语义搜索发现的股票与已有股票合并
      3. 按语义相似度为所有股票打分（0-100）
      4. 按分数降序排列

    Args:
        concept_name: 概念名称
        stocks_detail: 已有股票详情 {ts_code: {sources: [...], name: ""}}
        n_results: ChromaDB 返回结果数

    Returns:
        增强后的 stocks_detail（按 score 降序，含 semantic_score 字段）
    """
    from src.infrastructure import database

    if database.chroma_collection is None:
        # ChromaDB 不可用，返回原始数据（无打分）
        return stocks_detail

    # Step 1: ChromaDB 语义搜索
    try:
        results = database.chroma_collection.query(
            query_texts=[concept_name],
            n_results=n_results,
        )
    except Exception as e:
        logger.debug("[ConceptSources] ChromaDB 查询 '{}' 失败: {}", concept_name, e)
        return stocks_detail

    if not results or not results.get("ids") or not results["ids"][0]:
        return stocks_detail

    # Step 2: 解析 ChromaDB 结果 → {ts_code: {name, distance}}
    chroma_stocks: dict[str, dict] = {}
    ids_list = results["ids"][0]
    meta_list = results["metadatas"][0] if results.get("metadatas") else [{}] * len(ids_list)
    dist_list = results["distances"][0] if results.get("distances") else [1.0] * len(ids_list)
    for i, doc_id in enumerate(ids_list):
        meta = meta_list[i]
        distance = dist_list[i]
        ts_code = meta.get("ts_code", doc_id)
        chroma_stocks[ts_code] = {
            "name": meta.get("name", ""),
            "distance": distance,
        }

    # Step 3: 合并已有股票 + ChromaDB 发现的股票
    merged: dict[str, dict] = {}

    # 3a: 保留已有股票及其来源
    for ts_code, detail in stocks_detail.items():
        if ts_code in chroma_stocks:
            similarity = max(0, 1.0 - chroma_stocks[ts_code]["distance"])
            score = round(similarity * 100)
        else:
            # 已在概念板块但 ChromaDB 未召回，语义距离较远，给低分
            score = 5
        merged[ts_code] = {
            **detail,
            "semantic_score": score,
            "name": detail.get("name") or chroma_stocks.get(ts_code, {}).get("name", ""),
        }

    # 3b: 补充 ChromaDB 发现但概念板块未包含的股票
    added = 0
    for ts_code, info in chroma_stocks.items():
        if ts_code not in merged:
            similarity = max(0, 1.0 - info["distance"])
            score = round(similarity * 100)
            merged[ts_code] = {
                "sources": ["semantic"],
                "name": info["name"],
                "semantic_score": score,
            }
            added += 1

    if added > 0:
        logger.info("[ConceptSources] 语义增强 '{}': 新增 {} 只股票 (共 {} 只)",
                    concept_name, added, len(merged))

    # Step 4: 按 semantic_score 降序返回
    sorted_items = sorted(merged.items(), key=lambda x: x[1].get("semantic_score", 0), reverse=True)
    return dict(sorted_items)
