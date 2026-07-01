"""
database.py — 统一管理 PostgreSQL / ChromaDB / Redis 的连接与初始化。
调用 init_all() 完成所有连接建立和建表 DDL。
"""
from __future__ import annotations

import psycopg2
import psycopg2.pool
import redis
import chromadb
from loguru import logger
from typing import Optional

from config.settings import settings

# ── Redis Key 前缀规范 ──────────────────────────────────────────────────────
REDIS_KEY_NEWS_DEDUP   = "dedup:news:{article_id}"
REDIS_KEY_URL_DEDUP    = "dedup:url:{url_md5}"
REDIS_KEY_LLM_CACHE    = "llm:{prompt_hash}"
REDIS_KEY_RATE_LIMIT   = "rate:searxng:{minute_bucket}"
REDIS_KEY_NEXT_WEIGHTS = "weights:stock:{ts_code}"
REDIS_KEY_NEWS_FEED    = "dynamic:news_feed"

# ── 概念图谱 Redis Keys ──────────────────────────────────────────────────────
REDIS_KEY_GRAPH_EDGES       = "concept_graph:edges"          # Hash: child→JSON{parents,relevance}
REDIS_KEY_GRAPH_ROOTS       = "concept_graph:roots"          # Set: Layer0 概念名
REDIS_KEY_GRAPH_LAYER       = "concept_graph:layer:{depth}"  # Set: 每层概念名
REDIS_KEY_GRAPH_PROGRESS    = "concept_graph:build_progress" # String: JSON 构建进度
REDIS_KEY_GRAPH_BUILD_LOCK  = "concept_graph:build_lock"     # String: 并发锁

# ── PostgreSQL 建表 DDL ───────────────────────────────────────────────────────
_DDL = """
CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code     VARCHAR(12)  PRIMARY KEY,
    name        VARCHAR(50)  NOT NULL,
    industry    VARCHAR(50),
    circ_mv     NUMERIC(18,4),
    is_st       BOOLEAN      DEFAULT FALSE,
    list_status VARCHAR(2)   DEFAULT 'L',
    biz_text_updated_at TIMESTAMP,
    updated_at  TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_stock_basic_st ON stock_basic(is_st);
CREATE INDEX IF NOT EXISTS idx_stock_basic_mv ON stock_basic(circ_mv);

CREATE TABLE IF NOT EXISTS sop_pending (
    id             SERIAL PRIMARY KEY,
    graph_json     JSONB       NOT NULL,
    source_text    TEXT        NOT NULL,
    status         VARCHAR(20) DEFAULT 'pending',
    created_at     TIMESTAMP   DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sop_active (
    id             SERIAL PRIMARY KEY,
    sop_name       VARCHAR(200) NOT NULL,
    policy_name    VARCHAR(200),
    graph_json     JSONB       NOT NULL,
    approved       BOOLEAN     DEFAULT FALSE,
    approved_by    VARCHAR(50),
    approved_at    TIMESTAMP   DEFAULT NOW(),
    created_at     TIMESTAMP   DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS news_analysis (
    article_id      VARCHAR(200) PRIMARY KEY,
    source          VARCHAR(50),
    title           TEXT,
    pub_time        TIMESTAMP,
    news_score      NUMERIC(5,4) DEFAULT 0,
    impact_type     VARCHAR(50),
    impact_concept  VARCHAR(100),
    sentiment       VARCHAR(20),
    reason          TEXT,
    related_ts_codes    JSONB DEFAULT '[]',
    new_concept_terms   JSONB DEFAULT '[]',
    mentioned_companies JSONB DEFAULT '[]',
    supply_chain_impact JSONB DEFAULT '[]',
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS concept_stocks (
    concept     VARCHAR(100) NOT NULL,
    ts_code     VARCHAR(12)  NOT NULL,
    sources     JSONB        DEFAULT '[]',
    score       NUMERIC(8,4) DEFAULT 0,
    stock_name  VARCHAR(50)  DEFAULT '',
    updated_at  TIMESTAMP    DEFAULT NOW(),
    PRIMARY KEY (concept, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_concept_stocks_concept ON concept_stocks(concept);
CREATE INDEX IF NOT EXISTS idx_concept_stocks_code ON concept_stocks(ts_code);

CREATE TABLE IF NOT EXISTS resonance_alerts (
    id SERIAL PRIMARY KEY,
    alert_time          TIMESTAMP    NOT NULL,
    ts_code             VARCHAR(10)  NOT NULL,
    name                VARCHAR(20),
    concept             VARCHAR(50),
    news_score          FLOAT,
    capital_inflow_pct  FLOAT,
    volume_ratio        FLOAT,
    confidence          FLOAT,
    reason              TEXT,
    created_at          TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_alerts_time ON resonance_alerts(alert_time DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_code ON resonance_alerts(ts_code);
"""

# ── 模块级单例 ────────────────────────────────────────────────────────────────
pg_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
chroma_client: Optional[chromadb.HttpClient] = None
chroma_collection: Optional[chromadb.Collection] = None
redis_client: Optional[redis.Redis] = None


# ── PostgreSQL ────────────────────────────────────────────────────────────────
def get_pg_conn() -> psycopg2.extensions.connection:
    """从连接池取出一条连接，使用完毕需调用 release_pg_conn 归还。
    如果连接已断开，自动重试新建连接。"""
    if pg_pool is None:
        raise RuntimeError("PostgreSQL 连接池未初始化，请先调用 init_all()")
    for attempt in range(3):
        conn = pg_pool.getconn()
        try:
            # 快速验证连接是否存活
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return conn
        except Exception:
            # 连接已断开，关闭并丢弃，下次循环会获取新连接
            try:
                conn.close()
            except Exception:
                pass
            pg_pool.putconn(conn, close=True)
            logger.warning("[DB] PG 连接已断开，重试 ({}/3)", attempt + 1)
    raise RuntimeError("PostgreSQL 连接池无法获取有效连接")


def release_pg_conn(conn: psycopg2.extensions.connection) -> None:
    """将连接归还连接池。"""
    if pg_pool is not None:
        pg_pool.putconn(conn)


def _init_postgres() -> psycopg2.pool.ThreadedConnectionPool:
    logger.info("[DB] 初始化 PostgreSQL 连接池: {}", settings.pg_dsn[:40] + "...")
    pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=1, maxconn=10, dsn=settings.pg_dsn
    )
    # 执行建表
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
        # 补丁：为已有表添加缺失列（单独事务，避免已存在时报错）
        _patch_sqls = [
            "ALTER TABLE sop_active ADD COLUMN created_at TIMESTAMP DEFAULT NOW()",
            "ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS biz_text_updated_at TIMESTAMP",
        ]
        for sql in _patch_sqls:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
            except Exception:
                conn.rollback()
        logger.info("[DB] PostgreSQL 表结构初始化完成")
    finally:
        pool.putconn(conn)
    return pool


# ── ChromaDB ──────────────────────────────────────────────────────────────────
def _init_chromadb() -> tuple:
    """初始化 ChromaDB 连接，兼容不同版本的服务端。

    检测顺序：先探测 v0.x REST API（轻量 HTTP GET），成功则使用兼容模式；
    REST 失败再尝试 v1.x HttpClient。避免不必要的 WARNING 日志。
    """
    from chromadb.utils import embedding_functions

    logger.info("[DB] 初始化 ChromaDB: {}:{}", settings.chromadb_host, settings.chromadb_port)

    client = None
    base_url = f"http://{settings.chromadb_host}:{settings.chromadb_port}"

    # ── 优先探测 v0.x REST API（轻量 GET，本项目服务端即此版本）──────
    try:
        import requests
        resp = requests.get(f"{base_url}/api/v1/heartbeat", timeout=5)
        if resp.ok:
            logger.info("[DB] ChromaDB v0.x 服务端在线，使用兼容模式")
            client = _ChromaDBCompat(base_url)
    except Exception:
        pass  # REST 不可达，继续尝试 v1.x

    # ── 降级：尝试 v1.x HttpClient ──────────────────────────────────
    if client is None:
        try:
            client = chromadb.HttpClient(
                host=settings.chromadb_host,
                port=settings.chromadb_port,
            )
            client.heartbeat()
            logger.info("[DB] ChromaDB v1.x 客户端连接成功")
        except Exception as e:
            logger.error("[DB] ChromaDB 完全不可用: {}", e)
            return None, None

    # 使用 bge-small-zh-v1.5（优先使用本地模型路径）
    model_path = settings.embedding_model_path
    try:
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=model_path,
        )
        logger.info("[DB] Embedding 模型已加载: {}", model_path)
    except Exception as ef_err:
        logger.warning("[DB] Embedding 模型加载失败: {}，使用默认", ef_err)
        ef = embedding_functions.DefaultEmbeddingFunction()

    try:
        collection = client.get_or_create_collection(
            name=settings.chroma_collection_name,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("[DB] ChromaDB collection '{}' 就绪，当前数量: {}",
                    settings.chroma_collection_name, collection.count())
    except Exception as e:
        logger.warning("[DB] ChromaDB collection 创建失败: {}，语义搜索不可用", e)
        collection = None

    return client, collection


class _ChromaDBCompat:
    """ChromaDB v0.x 兼容包装器，通过 REST API 直接通信。"""
    def __init__(self, base_url: str):
        import requests
        self._base = base_url
        self._session = requests.Session()
        self._collections: list = []  # 持有子 Collection 引用以便清理

    def close(self) -> None:
        """关闭底层 requests.Session，释放连接池资源"""
        try:
            self._session.close()
        except Exception:
            pass

    def heartbeat(self):
        resp = self._session.get(f"{self._base}/api/v1/heartbeat", timeout=5)
        return resp.json()

    def get_or_create_collection(self, name, embedding_function=None, metadata=None):
        """获取或创建 collection（返回 UUID 用于后续 API 调用）"""
        # 先尝试获取
        resp = self._session.get(f"{self._base}/api/v1/collections/{name}", timeout=5)
        if resp.ok:
            data = resp.json()
            coll_id = data.get("id", name)
            coll = _CollectionCompat(self._base, name, self._session, embedding_function, coll_id)
            self._collections.append(coll)
            return coll
        # 不存在则创建
        resp = self._session.post(
            f"{self._base}/api/v1/collections",
            json={"name": name, "metadata": metadata or {}},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        coll_id = data.get("id", name) if isinstance(data, dict) else name
        coll = _CollectionCompat(self._base, name, self._session, embedding_function, coll_id)
        self._collections.append(coll)
        return coll


class _CollectionCompat:
    """ChromaDB v0.x Collection 兼容包装器。"""
    def __init__(self, base_url: str, name: str, session, embedding_function=None, coll_id: str = ""):
        self._base = base_url
        self.name = name
        self._id = coll_id or name  # UUID for API calls, fallback to name
        self._session = session
        self._ef = embedding_function

    def count(self):
        try:
            resp = self._session.get(
                f"{self._base}/api/v1/collections/{self._id}/count", timeout=5
            )
            if resp.ok:
                return resp.json()
        except Exception:
            pass
        return 0

    def add(self, ids, documents, metadatas=None, embeddings=None):
        """添加文档（使用 embedding function 生成向量）"""
        if embeddings is None and self._ef is not None:
            try:
                embeddings = self._ef(documents)
            except Exception:
                embeddings = None
        payload = {"ids": ids, "documents": documents}
        if metadatas:
            payload["metadatas"] = metadatas
        if embeddings is not None:
            import numpy as np
            if isinstance(embeddings, np.ndarray):
                embeddings = embeddings.tolist()
            elif isinstance(embeddings, list):
                embeddings = [e.tolist() if isinstance(e, np.ndarray) else e for e in embeddings]
            payload["embeddings"] = embeddings
        resp = self._session.post(
            f"{self._base}/api/v1/collections/{self._id}/add",
            json=payload, timeout=30,
        )
        if not resp.ok:
            logger.warning("[ChromaDB] add failed: {} {}", resp.status_code, resp.text[:200])
        return resp.json() if resp.ok else None

    def upsert(self, ids, documents, metadatas=None, embeddings=None):
        """Upsert 文档（已存在则更新，不存在则插入）"""
        if embeddings is None and self._ef is not None:
            try:
                embeddings = self._ef(documents)
            except Exception:
                embeddings = None
        payload = {"ids": ids, "documents": documents}
        if metadatas:
            payload["metadatas"] = metadatas
        if embeddings is not None:
            import numpy as np
            if isinstance(embeddings, np.ndarray):
                embeddings = embeddings.tolist()
            elif isinstance(embeddings, list):
                embeddings = [e.tolist() if isinstance(e, np.ndarray) else e for e in embeddings]
            payload["embeddings"] = embeddings
        resp = self._session.post(
            f"{self._base}/api/v1/collections/{self._id}/upsert",
            json=payload, timeout=30,
        )
        if not resp.ok:
            logger.warning("[ChromaDB] upsert failed: {} {}", resp.status_code, resp.text[:200])
        return resp.json() if resp.ok else None

    def query(self, query_texts, n_results=10, where=None):
        """查询文档"""
        embeddings = None
        if self._ef is not None:
            try:
                embeddings = self._ef(query_texts)
            except Exception:
                pass
        payload = {"query_texts": query_texts, "n_results": n_results}
        if embeddings is not None:
            import numpy as np
            if isinstance(embeddings, np.ndarray):
                embeddings = embeddings.tolist()
            elif isinstance(embeddings, list):
                embeddings = [e.tolist() if isinstance(e, np.ndarray) else e for e in embeddings]
            payload["query_embeddings"] = embeddings
        if where:
            payload["where"] = where
        resp = self._session.post(
            f"{self._base}/api/v1/collections/{self._id}/query",
            json=payload, timeout=30,
        )
        if resp.ok:
            return resp.json()
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def get(self, ids=None, where=None, include=None):
        """获取文档（用于增量更新检查）"""
        payload = {}
        if ids:
            payload["ids"] = ids
        if where:
            payload["where"] = where
        if include:
            payload["include"] = include
        resp = self._session.post(
            f"{self._base}/api/v1/collections/{self._id}/get",
            json=payload, timeout=30,
        )
        if resp.ok:
            return resp.json()
        return {"ids": [], "metadatas": [], "documents": []}


# ── Redis ─────────────────────────────────────────────────────────────────────
def _init_redis() -> redis.Redis:
    logger.info("[DB] 初始化 Redis: {}", settings.redis_url)
    client = redis.from_url(settings.redis_url, decode_responses=True)
    client.ping()
    logger.info("[DB] Redis 连接成功")
    return client


def ensure_redis() -> Optional[redis.Redis]:
    """确保 Redis 可用。如果模块级 redis_client 为 None，尝试重新初始化。
    成功返回 client，失败返回 None。"""
    global redis_client
    if redis_client is not None:
        try:
            redis_client.ping()
            return redis_client
        except Exception:
            redis_client = None
    # 尝试重连
    try:
        redis_client = _init_redis()
        return redis_client
    except Exception:
        return None


# ── 统一入口 ──────────────────────────────────────────────────────────────────
def init_all() -> None:
    """
    启动时调用一次，初始化所有连接并完成建表 DDL。
    初始化结果写入模块级单例，其他模块通过 get_pg_conn / redis_client 等访问。
    ChromaDB 不可用时不会崩溃，语义搜索功能降级。
    """
    global pg_pool, chroma_client, chroma_collection, redis_client

    pg_pool = _init_postgres()

    try:
        chroma_client, chroma_collection = _init_chromadb()
    except Exception as e:
        logger.warning("[DB] ChromaDB 初始化失败: {}，语义搜索不可用", e)
        chroma_client, chroma_collection = None, None

    try:
        redis_client = _init_redis()
    except Exception as e:
        logger.warning("[DB] Redis 初始化失败: {}，消息/缓存功能不可用", e)
        redis_client = None

    logger.info("[DB] 全部基础设施初始化完成")


def close_all() -> None:
    """关闭所有连接，释放资源。在进程退出时调用。"""
    global pg_pool, chroma_client, chroma_collection, redis_client

    # 关闭 ChromaDB 兼容客户端的 requests.Session
    if chroma_client is not None and hasattr(chroma_client, "close"):
        try:
            chroma_client.close()
            logger.info("[DB] ChromaDB 兼容客户端已关闭")
        except Exception as e:
            logger.warning("[DB] ChromaDB 关闭失败: {}", e)

    # 关闭 PostgreSQL 连接池
    if pg_pool is not None:
        try:
            pg_pool.closeall()
            logger.info("[DB] PostgreSQL 连接池已关闭")
        except Exception as e:
            logger.warning("[DB] PostgreSQL 连接池关闭失败: {}", e)

    # 关闭 Redis 连接
    if redis_client is not None:
        try:
            redis_client.close()
            logger.info("[DB] Redis 连接已关闭")
        except Exception as e:
            logger.warning("[DB] Redis 关闭失败: {}", e)

    pg_pool = None
    chroma_client = None
    chroma_collection = None
    redis_client = None
