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

# ── PostgreSQL 建表 DDL ───────────────────────────────────────────────────────
_DDL = """
CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code     VARCHAR(12)  PRIMARY KEY,
    name        VARCHAR(50)  NOT NULL,
    industry    VARCHAR(50),
    circ_mv     NUMERIC(18,4),
    is_st       BOOLEAN      DEFAULT FALSE,
    list_status VARCHAR(2)   DEFAULT 'L',
    updated_at  TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_stock_basic_st ON stock_basic(is_st);
CREATE INDEX IF NOT EXISTS idx_stock_basic_mv ON stock_basic(circ_mv);

CREATE TABLE IF NOT EXISTS sop_pending (
    id             SERIAL PRIMARY KEY,
    graph_json     JSONB       NOT NULL,          -- V4-Flash 提取的 SOP 操作图谱
    source_text    TEXT        NOT NULL,          -- 原始政策文本片段
    status         VARCHAR(20) DEFAULT 'pending', -- pending/processed
    created_at     TIMESTAMP   DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sop_active (
    id             SERIAL PRIMARY KEY,
    sop_name       VARCHAR(200) NOT NULL,         -- SOP 名称
    policy_name    VARCHAR(200),                  -- 关联政策名称
    graph_json     JSONB       NOT NULL,          -- 审批通过的 SOP 操作图谱
    approved       BOOLEAN     DEFAULT FALSE,     -- 人工审核状态
    approved_by    VARCHAR(50),
    approved_at    TIMESTAMP   DEFAULT NOW()
);
"""

# ── 模块级单例 ────────────────────────────────────────────────────────────────
pg_pool: Optional[psycopg2.pool.SimpleConnectionPool] = None
chroma_client: Optional[chromadb.HttpClient] = None
chroma_collection: Optional[chromadb.Collection] = None
redis_client: Optional[redis.Redis] = None


# ── PostgreSQL ────────────────────────────────────────────────────────────────
def get_pg_conn() -> psycopg2.extensions.connection:
    """从连接池取出一条连接，使用完毕需调用 release_pg_conn 归还。"""
    if pg_pool is None:
        raise RuntimeError("PostgreSQL 连接池未初始化，请先调用 init_all()")
    return pg_pool.getconn()


def release_pg_conn(conn: psycopg2.extensions.connection) -> None:
    """将连接归还连接池。"""
    if pg_pool is not None:
        pg_pool.putconn(conn)


def _init_postgres() -> psycopg2.pool.SimpleConnectionPool:
    logger.info("[DB] 初始化 PostgreSQL 连接池: {}", settings.pg_dsn[:40] + "...")
    pool = psycopg2.pool.SimpleConnectionPool(
        minconn=2, maxconn=10, dsn=settings.pg_dsn
    )
    # 执行建表
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
        logger.info("[DB] PostgreSQL 表结构初始化完成")
    finally:
        pool.putconn(conn)
    return pool


# ── ChromaDB ──────────────────────────────────────────────────────────────────
def _init_chromadb() -> tuple:
    """初始化 ChromaDB 连接，兼容不同版本的服务端。"""
    from chromadb.utils import embedding_functions

    logger.info("[DB] 初始化 ChromaDB: {}:{}", settings.chromadb_host, settings.chromadb_port)

    client = None
    # 尝试新版 HttpClient (1.x 客户端)
    try:
        client = chromadb.HttpClient(
            host=settings.chromadb_host,
            port=settings.chromadb_port,
        )
        # 验证连接
        client.heartbeat()
    except Exception as e1:
        logger.warning("[DB] ChromaDB HttpClient 连接失败: {}，尝试兼容模式", e1)
        # 降级：使用 REST API 直接通信
        try:
            import requests
            base_url = f"http://{settings.chromadb_host}:{settings.chromadb_port}"
            resp = requests.get(f"{base_url}/api/v1/heartbeat", timeout=5)
            if resp.ok:
                logger.info("[DB] ChromaDB v0.x 服务端在线，使用兼容模式")
                # 创建简化包装器
                client = _ChromaDBCompat(base_url)
            else:
                raise Exception(f"heartbeat 返回 {resp.status_code}")
        except Exception as e2:
            logger.error("[DB] ChromaDB 完全不可用: {}", e2)
            return None, None

    # 使用 bge-small-zh-v1.5
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="BAAI/bge-small-zh-v1.5",
        cache_folder=settings.embedding_model_path,
    )

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

    def heartbeat(self):
        resp = self._session.get(f"{self._base}/api/v1/heartbeat", timeout=5)
        return resp.json()

    def get_or_create_collection(self, name, embedding_function=None, metadata=None):
        """获取或创建 collection（简化实现）"""
        import requests
        # 先尝试获取
        resp = self._session.get(f"{self._base}/api/v1/collections/{name}", timeout=5)
        if resp.ok:
            return _CollectionCompat(self._base, name, self._session)
        # 不存在则创建
        resp = self._session.post(
            f"{self._base}/api/v1/collections",
            json={"name": name, "metadata": metadata or {}},
            timeout=5,
        )
        resp.raise_for_status()
        return _CollectionCompat(self._base, name, self._session)


class _CollectionCompat:
    """ChromaDB v0.x Collection 兼容包装器。"""
    def __init__(self, base_url: str, name: str, session):
        self._base = base_url
        self.name = name
        self._session = session

    def count(self):
        try:
            resp = self._session.get(
                f"{self._base}/api/v1/collections/{self.name}/count", timeout=5
            )
            if resp.ok:
                return resp.json()
        except Exception:
            pass
        return 0


# ── Redis ─────────────────────────────────────────────────────────────────────
def _init_redis() -> redis.Redis:
    logger.info("[DB] 初始化 Redis: {}", settings.redis_url)
    client = redis.from_url(settings.redis_url, decode_responses=True)
    client.ping()
    logger.info("[DB] Redis 连接成功")
    return client


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

    redis_client = _init_redis()

    logger.info("[DB] 全部基础设施初始化完成")
