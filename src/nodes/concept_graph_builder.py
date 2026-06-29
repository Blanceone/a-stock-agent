"""
concept_graph_builder.py — 政策驱动概念图谱构建引擎

三种触发模式：
  1. build_full()          — 全量构建（CLI / Web 按钮）
  2. expand_from_concept() — 手动输入单概念展开
  3. incremental_insert()  — 新闻发现新概念时增量插入

核心算法：BFS 迭代扩展 + LLM 从已有概念池中选取子概念
"""
from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable

from loguru import logger

from config.settings import settings
from src.infrastructure.database import (
    REDIS_KEY_GRAPH_BUILD_LOCK,
    REDIS_KEY_GRAPH_EDGES,
    REDIS_KEY_GRAPH_LAYER,
    REDIS_KEY_GRAPH_PROGRESS,
    REDIS_KEY_GRAPH_ROOTS,
    redis_client,
)
from src.nodes.llm_utils import call_llm_json, call_llm_with_tools, load_prompt


# ── 进度管理 ──────────────────────────────────────────────────────────────────

def _update_progress(
    status: str,
    current_depth: int = 0,
    discovered_count: int = 0,
    total_layer0: int = 0,
    current_concept: str = "",
    errors: list[str] | None = None,
    started_at: str = "",
) -> None:
    """写入构建进度到 Redis，供前端轮询"""
    if redis_client is None:
        return
    data = {
        "status": status,
        "current_depth": current_depth,
        "discovered_count": discovered_count,
        "total_layer0": total_layer0,
        "current_concept": current_concept,
        "started_at": started_at,
        "errors": errors or [],
        "updated_at": datetime.now().isoformat(),
    }
    try:
        redis_client.setex(REDIS_KEY_GRAPH_PROGRESS, 3600, json.dumps(data, ensure_ascii=False))
    except Exception as e:
        logger.warning("[ConceptGraph] 进度写入失败: {}", e)


def get_progress() -> dict:
    """读取构建进度"""
    if redis_client is None:
        return {"status": "idle"}
    try:
        raw = redis_client.get(REDIS_KEY_GRAPH_PROGRESS)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return {"status": "idle"}


# ── 政策文本获取 ──────────────────────────────────────────────────────────────

def fetch_policy_text(policy_text_path_or_content: str | None = None) -> str:
    """
    获取政策全文文本。
    优先使用用户提供的路径/内容，否则自动联网搜索下载。
    """
    # 1. 用户直接提供文本内容（较长）
    if policy_text_path_or_content and len(policy_text_path_or_content) > 200:
        return policy_text_path_or_content[:15000]

    # 2. 用户提供文件路径
    if policy_text_path_or_content:
        from pathlib import Path
        p = Path(policy_text_path_or_content)
        if p.exists():
            return p.read_text(encoding="utf-8")[:15000]

    # 3. 自动联网搜索
    queries = [q.strip() for q in settings.concept_graph_policy_search_queries.split(",") if q.strip()]
    logger.info("[ConceptGraph] 自动搜索政策文本: {} 条查询", len(queries))

    prompt = f"""请搜索以下关键词，找到国家重要政策文件（如十五五规划纲要）的全文或要点：
搜索查询：{json.dumps(queries, ensure_ascii=False)}

使用 web_search 工具逐一搜索，从结果中选取最权威的全文链接（优先 gov.cn 域名）。
最终输出 JSON：
{{"urls": ["url1", "url2"], "policy_name": "文件名称", "summary": "如果无法获取全文，请提供政策要点概述（500字以内）"}}"""

    try:
        result = call_llm_with_tools(
            prompt, model="flash",
            system="你是政策文件搜索助手。使用 web_search 工具搜索，最终仅输出 JSON。",
            max_rounds=4, max_tokens=2048,
        )
        if not isinstance(result, dict):
            result = {}

        urls = result.get("urls", [])
        # 尝试从 URL 抓取全文
        for url in urls[:3]:
            try:
                from src.infrastructure.web_fetcher import fetch_article
                text = fetch_article(url)
                if len(text) > 500:
                    logger.info("[ConceptGraph] 政策文本抓取成功: {} ({} 字)", url, len(text))
                    return text[:15000]
            except Exception as e:
                logger.debug("[ConceptGraph] 抓取失败 url={}: {}", url, e)

        # 降级：使用 LLM 生成的摘要
        summary = result.get("summary", "")
        if summary:
            logger.info("[ConceptGraph] 使用 LLM 政策摘要 ({} 字)", len(summary))
            return summary

    except Exception as e:
        logger.warning("[ConceptGraph] 政策搜索失败: {}", e)

    return ""


# ── Layer 0 提取 ──────────────────────────────────────────────────────────────

def extract_layer0(policy_text: str) -> list[dict]:
    """
    从政策文本中提取 Layer 0 核心概念。
    返回: [{"concept": "...", "policy_basis": "...", "importance": int, "category": "..."}, ...]
    """
    template = load_prompt("concept_graph_extract")
    prompt = template.format(policy_text=policy_text[:12000])

    result = call_llm_json(prompt, model="pro", max_tokens=4096, use_cache=False)
    if not isinstance(result, list):
        logger.warning("[ConceptGraph] Layer0 提取返回非数组: {}", type(result).__name__)
        return []

    concepts = [
        c for c in result
        if isinstance(c, dict) and c.get("concept") and len(c["concept"]) >= 2
    ]

    # 按 importance 降序，截取 max_layer0
    concepts.sort(key=lambda x: x.get("importance", 0), reverse=True)
    concepts = concepts[:settings.concept_graph_max_layer0]

    # 后置匹配：将 LLM 提取的概念名对齐到已有概念池
    for c in concepts:
        matched = _match_existing_concept(c["concept"])
        if matched and matched != c["concept"]:
            logger.debug("[ConceptGraph] Layer0 '{}' 匹配到已有概念 '{}'", c["concept"], matched)
            c["original_concept"] = c["concept"]
            c["concept"] = matched

    logger.info("[ConceptGraph] Layer0 提取: {} 个核心概念", len(concepts))
    for c in concepts:
        logger.debug("  - {} (重要度: {}, 分类: {})", c["concept"], c.get("importance"), c.get("category"))

    return concepts


# ── BFS 迭代扩展 ──────────────────────────────────────────────────────────────

def _get_graph_concepts_summary() -> str:
    """获取图谱中已有概念的摘要文本，供 LLM prompt 使用"""
    if redis_client is None:
        return "(空)"
    try:
        roots = redis_client.smembers(REDIS_KEY_GRAPH_ROOTS)
        if not roots:
            return "(空)"
        root_names = [r.decode("utf-8") if isinstance(r, bytes) else r for r in roots]
        lines = [f"Layer 0 (政策核心): {', '.join(root_names)}"]

        for depth in range(1, settings.concept_graph_max_depth + 1):
            layer_key = REDIS_KEY_GRAPH_LAYER.format(depth=depth)
            members = redis_client.smembers(layer_key)
            if members:
                names = [m.decode("utf-8") if isinstance(m, bytes) else m for m in members]
                lines.append(f"Layer {depth}: {', '.join(names)}")

        return "\n".join(lines)
    except Exception:
        return "(读取失败)"


def _get_visited_set() -> set[str]:
    """从 Redis 获取所有已访问概念名"""
    visited: set[str] = set()
    if redis_client is None:
        return visited
    try:
        # 从所有 layer 集合中读取
        for depth in range(settings.concept_graph_max_depth + 2):
            layer_key = REDIS_KEY_GRAPH_LAYER.format(depth=depth)
            members = redis_client.smembers(layer_key)
            for m in members:
                visited.add(m.decode("utf-8") if isinstance(m, bytes) else m)
        # roots 也加入
        roots = redis_client.smembers(REDIS_KEY_GRAPH_ROOTS)
        for r in roots:
            visited.add(r.decode("utf-8") if isinstance(r, bytes) else r)
    except Exception:
        pass
    return visited


def _load_concept_pool(exclude: set[str] | None = None) -> list[dict]:
    """
    从 Redis dynamic:concepts 加载候选概念池。
    返回: [{"name", "category", "policy_score", "stock_count"}, ...]
    按 policy_score 降序，排除已加入图谱的概念。
    """
    if redis_client is None:
        return []
    try:
        all_data = redis_client.hgetall("dynamic:concepts")
    except Exception:
        return []

    exclude = exclude or set()
    pool = []
    for name_raw, val_raw in all_data.items():
        name = name_raw.decode("utf-8") if isinstance(name_raw, bytes) else name_raw
        if name in exclude:
            continue
        try:
            data = json.loads(val_raw)
        except (json.JSONDecodeError, TypeError):
            continue
        stock_count = len(data.get("stocks", []))
        if stock_count == 0:
            continue  # 无成分股的概念不入图谱
        pool.append({
            "name": name,
            "category": data.get("category", ""),
            "policy_score": data.get("policy_score", 0),
            "stock_count": stock_count,
        })

    pool.sort(key=lambda x: x.get("policy_score", 0), reverse=True)
    return pool


def _filter_candidates(
    pool: list[dict],
    current_category: str,
    visited: set[str],
    max_candidates: int = 80,
) -> list[dict]:
    """
    根据当前概念分类粗筛候选池，减少 LLM token 消耗。
    策略：同类别概念优先 + 高分概念兜底，总数不超过 max_candidates。
    """
    same_cat = []
    other_cat = []
    for item in pool:
        if item["name"] in visited:
            continue
        if current_category and item.get("category") == current_category:
            same_cat.append(item)
        else:
            other_cat.append(item)

    candidates = same_cat[:]
    remaining = max_candidates - len(candidates)
    if remaining > 0:
        candidates.extend(other_cat[:remaining])

    return candidates


def _write_concept_to_redis(
    concept_name: str,
    depth: int,
    parent_concepts: list[str],
    policy_anchor: str = "",
    expansion_status: str = "pending",
) -> None:
    """将概念写入 dynamic:concepts Hash（兼容现有结构）"""
    if redis_client is None:
        return
    try:
        raw = redis_client.hget("dynamic:concepts", concept_name)
        if raw:
            data = json.loads(raw)
        else:
            data = {
                "stocks": [],
                "stocks_detail": {},
                "sources": ["concept_graph"],
                "confidence": 0.8,
                "last_seen": datetime.now().isoformat(),
            }

        data["graph_depth"] = depth
        data["parent_concepts"] = parent_concepts
        data["policy_anchor"] = policy_anchor
        data["expansion_status"] = expansion_status

        redis_client.hset("dynamic:concepts", concept_name, json.dumps(data, ensure_ascii=False))

        # 写入 layer Set（仅当概念有成分股时）
        if data.get("stocks"):
            layer_key = REDIS_KEY_GRAPH_LAYER.format(depth=depth)
            redis_client.sadd(layer_key, concept_name)
            redis_client.expire(layer_key, 30 * 86400)

    except Exception as e:
        logger.debug("[ConceptGraph] 写入概念 {} 失败: {}", concept_name, e)


def _write_edge(child: str, parent: str, relevance: float, relation: str = "") -> None:
    """写入概念边到 concept_graph:edges"""
    if redis_client is None:
        return
    try:
        raw = redis_client.hget(REDIS_KEY_GRAPH_EDGES, child)
        if raw:
            data = json.loads(raw)
        else:
            data = {"parents": [], "created_at": datetime.now().isoformat()}

        # 避免重复添加同一 parent
        existing_parents = {p["name"] for p in data.get("parents", [])}
        if parent not in existing_parents:
            data["parents"].append({
                "name": parent,
                "relevance": relevance,
                "relation": relation,
            })

        redis_client.hset(REDIS_KEY_GRAPH_EDGES, child, json.dumps(data, ensure_ascii=False))
        redis_client.expire(REDIS_KEY_GRAPH_EDGES, 30 * 86400)
    except Exception as e:
        logger.debug("[ConceptGraph] 写入边 {}→{} 失败: {}", parent, child, e)


def _match_existing_concept(name: str) -> str | None:
    """
    模糊匹配已有概念池（dynamic:concepts），返回匹配到的概念名或 None。
    策略：精确匹配 > 子串包含 > 编辑距离
    """
    if redis_client is None:
        return None
    try:
        all_names = redis_client.hkeys("dynamic:concepts")
        all_names_str = [
            n.decode("utf-8") if isinstance(n, bytes) else n
            for n in all_names
        ]
    except Exception:
        return None

    # 精确匹配
    if name in all_names_str:
        return name

    # 子串包含（双向）
    best_match = None
    for existing in all_names_str:
        if name in existing or existing in name:
            if best_match is None or len(existing) > len(best_match):
                best_match = existing
    if best_match:
        return best_match

    # 编辑距离 <= 2（简单实现：汉明距离 for 等长字符串）
    for existing in all_names_str:
        if abs(len(existing) - len(name)) <= 2:
            diff = sum(1 for a, b in zip(existing, name) if a != b)
            if diff <= 2:
                return existing

    return None


def bfs_expand(
    layer0_concepts: list[dict],
    progress_callback: Callable | None = None,
    started_at: str = "",
) -> dict:
    """
    BFS 迭代扩展核心算法。

    Args:
        layer0_concepts: Layer0 概念列表
        progress_callback: 可选回调 (depth, count, concept_name)
        started_at: 开始时间

    Returns:
        {"discovered": int, "layers": {depth: [names]}}
    """
    max_depth = settings.concept_graph_max_depth
    max_children = settings.concept_graph_max_children
    threshold = settings.concept_graph_convergence_threshold

    # 初始化
    queue: deque[tuple[str, int, str, str]] = deque()  # (concept, depth, category, policy_anchor)
    visited = _get_visited_set()
    discovered = len(visited)
    layer_counts: dict[int, int] = {}
    errors: list[str] = []

    # Layer0 入队 + 写入 Redis
    for c in layer0_concepts:
        name = c["concept"]
        if name not in visited:
            _write_concept_to_redis(
                name, depth=0, parent_concepts=[],
                policy_anchor=c.get("policy_basis", ""),
                expansion_status="pending",
            )
            redis_client.sadd(REDIS_KEY_GRAPH_ROOTS, name)
            redis_client.expire(REDIS_KEY_GRAPH_ROOTS, 30 * 86400)

        queue.append((name, 0, c.get("category", ""), c.get("policy_basis", "")))
        visited.add(name)

    layer_counts[0] = len(layer0_concepts)
    total_layer0 = len(layer0_concepts)

    # 加载候选概念池（一次性）
    concept_pool = _load_concept_pool(exclude=visited)
    logger.info("[ConceptGraph] 候选概念池加载: {} 个概念", len(concept_pool))

    _update_progress("running", 0, discovered, total_layer0, "", errors, started_at)

    # BFS 主循环
    while queue:
        current, depth, category, policy_anchor = queue.popleft()

        if depth >= max_depth:
            _write_concept_to_redis(
                current, depth, [], policy_anchor, expansion_status="converged",
            )
            continue

        # 更新进度
        _update_progress("running", depth, discovered, total_layer0, current, errors, started_at)
        if progress_callback:
            progress_callback(depth, discovered, current)

        logger.info("[ConceptGraph] BFS Layer{} 扩展: {}", depth, current)

        # 限流等待
        time.sleep(1)

        # LLM 从候选池中选取子概念
        try:
            expand_template = load_prompt("concept_graph_expand")
            candidates = _filter_candidates(concept_pool, category, visited, max_candidates=80)
            candidate_text = "\n".join(
                f"- {c['name']}（{c.get('category', '其他')}，{c.get('stock_count', 0)}只成分股）"
                for c in candidates
            )
            excluded = ", ".join(list(visited)[:30])
            prompt = expand_template.format(
                concept_name=current,
                category=category or "综合",
                policy_anchor=policy_anchor or "国家产业政策",
                candidate_pool=candidate_text or "（候选池为空）",
                excluded_concepts=excluded,
            )

            result = call_llm_json(
                prompt, model="pro",
                max_tokens=2048, use_cache=False,
            )

            if not isinstance(result, dict):
                errors.append(f"{current}: LLM返回非dict")
                continue

            children = result.get("children", [])
            if not isinstance(children, list):
                children = []

            children = children[:max_children]
            logger.info("[ConceptGraph] {} 发现 {} 个子概念", current, len(children))

            # 构建候选名称集合（用于硬校验）
            pool_names = {c["name"] for c in candidates}

            # 处理每个子概念
            for child in children:
                if not isinstance(child, dict):
                    continue
                child_name = child.get("concept", "").strip()
                if not child_name or child_name in visited:
                    continue

                # 硬校验：确保概念在候选池中
                if child_name not in pool_names:
                    fuzzy = _match_existing_concept(child_name)
                    if fuzzy and fuzzy in pool_names:
                        child_name = fuzzy
                    else:
                        logger.debug("[ConceptGraph] LLM 返回池外概念 '{}'，跳过", child_name)
                        continue

                relevance = float(child.get("relevance", 0))
                relation = child.get("relation", "")
                reason = child.get("reason", "")

                # 收敛判断：relevance < 阈值则不扩展
                should_expand = relevance >= threshold

                if should_expand:
                    # 写入概念 + 边
                    _write_concept_to_redis(
                        child_name, depth=depth + 1,
                        parent_concepts=[current],
                        policy_anchor=policy_anchor,
                        expansion_status="pending",
                    )
                    _write_edge(child_name, current, relevance, relation)

                    # 从候选池移除（避免重复分配）
                    concept_pool = [c for c in concept_pool if c["name"] != child_name]

                    # 入队继续扩展
                    visited.add(child_name)
                    discovered += 1
                    layer_counts[depth + 1] = layer_counts.get(depth + 1, 0) + 1
                    queue.append((child_name, depth + 1, category, policy_anchor))
                else:
                    # 低关联度：记录但不扩展
                    _write_concept_to_redis(
                        child_name, depth=depth + 1,
                        parent_concepts=[current],
                        policy_anchor=policy_anchor,
                        expansion_status="converged",
                    )
                    _write_edge(child_name, current, relevance, relation)
                    visited.add(child_name)
                    discovered += 1
                    concept_pool = [c for c in concept_pool if c["name"] != child_name]

        except Exception as e:
            err_msg = f"{current}: {e}"
            logger.warning("[ConceptGraph] 扩展失败: {}", err_msg)
            errors.append(err_msg)

        # 标记当前概念已扩展（保留原有 parent_concepts）
        _existing_raw = redis_client.hget("dynamic:concepts", current) if redis_client else None
        _existing_data = json.loads(_existing_raw) if _existing_raw else {}
        _write_concept_to_redis(
            current, depth,
            _existing_data.get("parent_concepts", []),
            policy_anchor, expansion_status="expanded",
        )

    return {"discovered": discovered, "layers": layer_counts}


# ── 全量构建入口 ──────────────────────────────────────────────────────────────

def _clear_old_graph() -> None:
    """清除旧图谱数据，供全量构建前调用"""
    if redis_client is None:
        return
    try:
        redis_client.delete(REDIS_KEY_GRAPH_ROOTS)
        redis_client.delete(REDIS_KEY_GRAPH_EDGES)
        for depth in range(settings.concept_graph_max_depth + 2):
            redis_client.delete(REDIS_KEY_GRAPH_LAYER.format(depth=depth))
        logger.info("[ConceptGraph] 旧图谱数据已清除")
    except Exception as e:
        logger.warning("[ConceptGraph] 清除旧图谱失败: {}", e)


def build_full(
    policy_text_path_or_content: str | None = None,
    progress_callback: Callable | None = None,
) -> dict:
    """
    全量构建政策概念图谱。

    流程：获取政策文本 → 提取Layer0 → BFS展开 → 返回统计
    """
    # 并发锁检查
    if redis_client is not None:
        lock_val = redis_client.get(REDIS_KEY_GRAPH_BUILD_LOCK)
        if lock_val:
            logger.warning("[ConceptGraph] 已有构建任务在运行中")
            return {"status": "already_running", "message": "概念图谱构建已在运行中"}
        redis_client.setex(REDIS_KEY_GRAPH_BUILD_LOCK, 3600, "1")  # 1小时锁

    started_at = datetime.now().isoformat()
    try:
        # Step 0: 清除旧图谱数据
        _clear_old_graph()

        # Step 1: 获取政策文本
        _update_progress("fetching_policy", 0, 0, 0, "", [], started_at)
        policy_text = fetch_policy_text(policy_text_path_or_content)
        if not policy_text:
            _update_progress("failed", errors=["无法获取政策文本"], started_at=started_at)
            return {"status": "failed", "error": "无法获取政策文本"}

        logger.info("[ConceptGraph] 政策文本获取成功: {} 字", len(policy_text))

        # Step 2: 提取 Layer0
        _update_progress("extracting_layer0", 0, 0, 0, "", [], started_at)
        layer0 = extract_layer0(policy_text)
        if not layer0:
            _update_progress("failed", errors=["Layer0 提取为空"], started_at=started_at)
            return {"status": "failed", "error": "Layer0 提取为空"}

        # Step 3: BFS 展开
        stats = bfs_expand(layer0, progress_callback, started_at)

        _update_progress("completed", 0, stats["discovered"], len(layer0), "", [], started_at)
        logger.info("[ConceptGraph] 全量构建完成: 发现 {} 个概念, 层级: {}",
                     stats["discovered"], stats["layers"])
        return {
            "status": "completed",
            "discovered": stats["discovered"],
            "layers": stats["layers"],
            "layer0_count": len(layer0),
        }

    except Exception as e:
        logger.error("[ConceptGraph] 全量构建异常: {}", e, exc_info=True)
        _update_progress("failed", errors=[str(e)], started_at=started_at)
        return {"status": "failed", "error": str(e)}

    finally:
        # 释放锁
        if redis_client is not None:
            try:
                redis_client.delete(REDIS_KEY_GRAPH_BUILD_LOCK)
            except Exception:
                pass


# ── 单概念展开 ────────────────────────────────────────────────────────────────

def expand_from_concept(concept_name: str, max_depth: int = 2) -> dict:
    """
    以单个概念为中心进行展开。用于手动输入场景。

    Returns: {"status": "...", "discovered": int, "matched": str|None}
    """
    # 模糊匹配已有概念
    matched = _match_existing_concept(concept_name)
    if matched and matched != concept_name:
        logger.info("[ConceptGraph] 手动输入 '{}' 匹配到已有概念 '{}'", concept_name, matched)
        concept_name = matched

    # 检查是否已在图谱中
    if redis_client is not None:
        raw = redis_client.hget("dynamic:concepts", concept_name)
        if raw:
            data = json.loads(raw)
            depth = data.get("graph_depth", -1)
            if depth >= 0:
                return {
                    "status": "found",
                    "matched": matched,
                    "graph_position": {
                        "depth": depth,
                        "parents": data.get("parent_concepts", []),
                    },
                }

    # 新概念：写入并展开
    _write_concept_to_redis(
        concept_name, depth=0, parent_concepts=[],
        policy_anchor="用户手动输入",
        expansion_status="pending",
    )

    # 构建 Layer0 并 BFS
    layer0 = [{
        "concept": concept_name,
        "category": "",
        "policy_basis": "用户手动输入",
        "importance": 8,
    }]

    # 临时调低 max_depth
    original_max_depth = settings.concept_graph_max_depth
    try:
        settings.concept_graph_max_depth = max_depth
        stats = bfs_expand(layer0, started_at=datetime.now().isoformat())
    finally:
        settings.concept_graph_max_depth = original_max_depth

    return {
        "status": "expanded",
        "matched": matched,
        "discovered": stats["discovered"],
    }


# ── 增量插入 ──────────────────────────────────────────────────────────────────

def incremental_insert(new_terms: list[str], news_result: dict | None = None) -> dict:
    """
    将新闻发现的新概念增量插入到已有图谱中。

    流程：检查图谱是否存在 → LLM 评估定位 → 写入边 → 有限展开
    """
    if not new_terms:
        return {"status": "skipped", "reason": "无新概念"}

    # 检查图谱是否已构建
    if redis_client is None:
        return {"status": "skipped", "reason": "Redis 未连接"}

    try:
        roots_count = redis_client.scard(REDIS_KEY_GRAPH_ROOTS)
    except Exception:
        roots_count = 0

    if roots_count == 0:
        return {"status": "skipped", "reason": "图谱尚未构建"}

    # 过滤已存在的概念
    terms_to_insert = []
    for term in new_terms:
        if not term or not isinstance(term, str):
            continue
        match = _match_existing_concept(term)
        if match:
            # 已存在，检查是否已在图谱中
            raw = redis_client.hget("dynamic:concepts", match)
            if raw:
                data = json.loads(raw)
                if data.get("graph_depth", -1) >= 0:
                    continue  # 已在图谱中，跳过
        terms_to_insert.append(term)

    if not terms_to_insert:
        return {"status": "skipped", "reason": "所有概念已在图谱中"}

    # LLM 批量评估定位
    graph_summary = _get_graph_concepts_summary()
    try:
        insert_template = load_prompt("concept_graph_insert")
        prompt = insert_template.format(
            graph_concepts_json=graph_summary[:3000],
            new_terms_json=json.dumps(terms_to_insert, ensure_ascii=False),
        )
        result = call_llm_json(prompt, model="flash", max_tokens=2048, use_cache=False)
    except Exception as e:
        logger.warning("[ConceptGraph] 增量插入 LLM 评估失败: {}", e)
        return {"status": "error", "error": str(e)}

    if not isinstance(result, list):
        return {"status": "error", "error": "LLM 返回非数组"}

    inserted = 0
    expanded = 0

    for item in result:
        if not isinstance(item, dict):
            continue
        term = item.get("term", "").strip()
        parent = item.get("parent", "").strip()
        relevance = float(item.get("relevance", 0))
        relation = item.get("relation", "")

        if not term or relevance < 0.4:
            continue

        if parent:
            # 获取父概念的 depth
            parent_depth = 0
            raw = redis_client.hget("dynamic:concepts", parent)
            if raw:
                parent_data = json.loads(raw)
                parent_depth = parent_data.get("graph_depth", 0)

            child_depth = parent_depth + 1
            if child_depth > settings.concept_graph_max_depth:
                continue

            # 写入概念 + 边
            _write_concept_to_redis(
                term, depth=child_depth,
                parent_concepts=[parent],
                policy_anchor="",
                expansion_status="expanded",  # 增量插入不展开
            )
            _write_edge(term, parent, relevance, relation)
            inserted += 1
            logger.info("[ConceptGraph] 增量插入: {} → {} (relevance={:.2f})", term, parent, relevance)
        else:
            # 无合适父节点：作为独立概念记录（不加入图谱层级）
            logger.debug("[ConceptGraph] 新概念 {} 无合适父节点，仅记录", term)

    return {"status": "ok", "inserted": inserted, "expanded": expanded}


# ── 读取图谱树结构 ────────────────────────────────────────────────────────────

def get_graph_tree(max_depth: int | None = None) -> dict:
    """
    读取图谱层级数据，返回树结构供前端渲染。
    """
    if redis_client is None:
        return {"roots": [], "nodes": [], "total_nodes": 0, "max_depth": 0}

    if max_depth is None:
        max_depth = settings.concept_graph_max_depth

    try:
        # 获取所有 roots
        roots_raw = redis_client.smembers(REDIS_KEY_GRAPH_ROOTS)
        roots = sorted([r.decode("utf-8") if isinstance(r, bytes) else r for r in roots_raw])

        if not roots:
            return {"roots": [], "nodes": [], "total_nodes": 0, "max_depth": 0}

        # 获取所有边
        edges_raw = redis_client.hgetall(REDIS_KEY_GRAPH_EDGES)
        children_map: dict[str, list[dict]] = {}  # parent → [children]
        for child_key, val in edges_raw.items():
            child_name = child_key.decode("utf-8") if isinstance(child_key, bytes) else child_key
            try:
                edge_data = json.loads(val)
                for parent_info in edge_data.get("parents", []):
                    parent_name = parent_info["name"]
                    if parent_name not in children_map:
                        children_map[parent_name] = []
                    children_map[parent_name].append({
                        "concept": child_name,
                        "relevance": parent_info.get("relevance", 0),
                        "relation": parent_info.get("relation", ""),
                    })
            except (json.JSONDecodeError, KeyError):
                continue

        # 获取所有概念的详细信息
        def _build_node(name: str, depth: int) -> dict | None:
            if depth > max_depth:
                return None
            raw = redis_client.hget("dynamic:concepts", name)
            data = json.loads(raw) if raw else {}

            stock_count = len(data.get("stocks", []))
            policy_score = data.get("policy_score", 0)

            node = {
                "concept": name,
                "depth": depth,
                "policy_anchor": data.get("policy_anchor", ""),
                "policy_score": policy_score,
                "stock_count": stock_count,
                "expansion_status": data.get("expansion_status", ""),
                "children": [],
            }

            # 递归构建子节点
            for child_info in children_map.get(name, []):
                child_node = _build_node(child_info["concept"], depth + 1)
                if child_node:
                    child_node["relevance"] = child_info["relevance"]
                    child_node["relation"] = child_info["relation"]
                    node["children"].append(child_node)

            # 子节点按 relevance 降序
            node["children"].sort(key=lambda x: x.get("relevance", 0), reverse=True)
            return node

        nodes = []
        total = 0
        actual_max_depth = 0

        def _count_nodes(node: dict) -> None:
            nonlocal total, actual_max_depth
            total += 1
            actual_max_depth = max(actual_max_depth, node["depth"])
            for child in node.get("children", []):
                _count_nodes(child)

        for root_name in roots:
            node = _build_node(root_name, 0)
            if node:
                nodes.append(node)
                _count_nodes(node)

        # 根节点按 policy_score 降序
        nodes.sort(key=lambda x: x.get("policy_score", 0), reverse=True)

        return {
            "roots": roots,
            "nodes": nodes,
            "total_nodes": total,
            "max_depth": actual_max_depth,
        }

    except Exception as e:
        logger.error("[ConceptGraph] 读取图谱树失败: {}", e, exc_info=True)
        return {"roots": [], "nodes": [], "total_nodes": 0, "max_depth": 0, "error": str(e)}
