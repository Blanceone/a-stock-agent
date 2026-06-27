"""
policy_parser.py — 步骤1：政策解读与概念提取

输入 state:
  policy_pdf_path: str   # 政策 PDF 本地路径
  retry_count: int       # 重试计数

输出 state 增量:
  concepts: list[dict]   # [{"concept": str, "source_section": str, "confidence": float}]
"""
from __future__ import annotations

import hashlib
import json

import fitz  # PyMuPDF
from loguru import logger

from src.infrastructure.database import chroma_collection
from src.nodes.llm_utils import call_llm_json, load_prompt


def _extract_toc(pdf_path: str) -> list[dict]:
    """用 PyMuPDF 提取目录大纲"""
    doc = fitz.open(pdf_path)
    toc = doc.get_toc()  # [(level, title, page), ...]
    doc.close()
    return [{"level": lvl, "title": title, "page": page} for lvl, title, page in toc]


def _extract_section_texts(pdf_path: str, key_pages: list[int]) -> dict[int, str]:
    """提取指定页码的正文文本"""
    doc = fitz.open(pdf_path)
    texts: dict[int, str] = {}
    for page_num in key_pages:
        if 1 <= page_num <= len(doc):
            page = doc[page_num - 1]  # fitz 0-indexed
            texts[page_num] = page.get_text()
    doc.close()
    return texts


def _store_policy_chunks(section_texts: dict[int, str], pdf_path: str) -> None:
    """将重点章节向量化存入 ChromaDB（collection: policy_chunks）"""
    if chroma_collection is None:
        logger.warning("[policy_parser] ChromaDB 未初始化，跳过存储")
        return

    pdf_hash = hashlib.md5(pdf_path.encode()).hexdigest()[:12]
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    for page_num, text in section_texts.items():
        if len(text.strip()) < 50:
            continue
        doc_id = f"policy_{pdf_hash}_p{page_num}"
        ids.append(doc_id)
        documents.append(text)
        metadatas.append({"source": pdf_path, "page": page_num})

    if ids:
        chroma_collection.add(ids=ids, documents=documents, metadatas=metadatas)
        logger.info("[policy_parser] 已向 ChromaDB 写入 {} 个政策文本块", len(ids))


def run(state: dict) -> dict:
    """
    节点入口函数（LangGraph 约定）。
    """
    pdf_path = state["policy_pdf_path"]
    logger.info("[policy_parser] 开始处理 PDF: {}", pdf_path)

    # 1. 提取目录
    toc = _extract_toc(pdf_path)
    toc_json = json.dumps(toc, ensure_ascii=False, indent=2)
    logger.info("[policy_parser] 目录条目数: {}", len(toc))

    # 2. V4-Pro 圈定重点章节
    template = load_prompt("policy_parser")
    toc_prompt = template.split("---")[0].format(toc_json=toc_json)
    key_sections = call_llm_json(toc_prompt, model="pro")
    key_pages = key_sections.get("key_sections", [])
    logger.info("[policy_parser] V4-Pro 圈定重点章节: {}", key_pages)

    # 3. 提取重点章节文本 → 向量化
    section_texts = _extract_section_texts(pdf_path, key_pages)
    _store_policy_chunks(section_texts, pdf_path)

    # 4. V4-Pro 提取产业概念词
    combined_text = "\n\n".join(section_texts.values())
    concept_prompt = template.split("---")[1].format(section_texts=combined_text[:8000])
    concepts_raw = call_llm_json(concept_prompt, model="pro")

    # 直接使用 LLM 返回结果（prompt 中已包含空词过滤指令）
    concepts = [c for c in concepts_raw if c.get("concept")]
    logger.info("[policy_parser] 提取概念词: {} 个", len(concepts))
    for c in concepts:
        logger.debug("  - {} (置信度: {}, 来源: {})",
                     c["concept"], c.get("confidence"), c.get("source_section"))

    return {
        "concepts": concepts,
        "error_node": None,
        "error_msg": None,
    }
