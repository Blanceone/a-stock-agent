"""
全局可配置项。
所有敏感值（API Key、密码）通过环境变量注入，禁止在此文件硬编码。
"""
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── 数据库 ────────────────────────────────────────────────
    pg_dsn: str = os.getenv(
        "PG_DSN",
        "postgresql://astock:astock@localhost:5432/astock",
    )
    chromadb_host: str = os.getenv("CHROMADB_HOST", "localhost")
    chromadb_port: int = int(os.getenv("CHROMADB_PORT", "8000"))
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # ── 外部服务 ──────────────────────────────────────────────
    tushare_token: str = os.getenv("TUSHARE_TOKEN", "")
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    searxng_url: str = os.getenv("SEARXNG_URL", "http://8.137.174.58:8888")
    searxng_api_key: str = os.getenv("SEARXNG_API_KEY", "")
    rsshub_url: str = os.getenv("RSSHUB_URL", "http://8.137.174.58:1200")
    defuddle_service_url: str = os.getenv(
        "DEFUDDLE_URL", "http://localhost:3000/extract"
    )

    # ── 新闻源直连 API ──────────────────────────────────────────────
    cls_api_url: str = os.getenv("CLS_API_URL", "https://www.cls.cn/api/cache")
    cls_poll_interval: float = float(os.getenv("CLS_POLL_INTERVAL", "5"))
    gov_api_url: str = os.getenv("GOV_API_URL", "https://sousuo.www.gov.cn/search-gov/data")
    gov_poll_interval: float = float(os.getenv("GOV_POLL_INTERVAL", "60"))
    csrc_api_url: str = os.getenv("CSRC_API_URL", "https://www.csrc.gov.cn/searchList")
    csrc_poll_interval: float = float(os.getenv("CSRC_POLL_INTERVAL", "60"))
    news_consumer_interval: int = int(os.getenv("NEWS_CONSUMER_INTERVAL", "30"))

    # ── 模型 ID ───────────────────────────────────────────────
    model_flash: str = "deepseek-chat"    # V4-Flash，高频/粗筛
    model_pro: str = "deepseek-reasoner"  # V4-Pro，深度推理

    # ── 资源根目录 ───────────────────────────────────────────
    resources_base: str = os.getenv(
        "RESOURCES_BASE", r"D:\work\ai\project-one\resources"
    )

    # ── 嵌入模型（本地 bge-small-zh-v1.5）──────────────────────
    embedding_model_path: str = os.path.join(
        resources_base, "models", "bge-small-zh-v1.5"
    )
    chroma_collection_name: str = "stock_business"

    # ── 三共振阈值（可调）────────────────────────────────────
    resonance_news_score_threshold: float = 0.7   # 新闻利好评分 ≥ 此值
    resonance_capital_inflow_pct: float = 0.02    # 主力净流入占比 ≥ 2%
    resonance_volume_ratio: float = 2.0           # 量比 ≥ 2.0

    # ── 实体映射过滤条件 ─────────────────────────────────────
    entity_chroma_top_k: int = 30                 # ChromaDB 召回候选数
    entity_llm_score_threshold: float = 0.6       # LLM 相关性打分下限
    entity_max_circ_mv: float = 2_000_000.0       # 流通市值上限（万元）= 200亿

    # ── 技术面多因子权重 ─────────────────────────────────────
    tech_weight_ma_alignment: float = 0.40
    tech_weight_volume_ratio: float = 0.25
    tech_weight_recent_gain: float = 0.20
    tech_weight_small_cap: float = 0.15

    # ── 新闻漏斗 ─────────────────────────────────────────────
    news_coarse_batch_size: int = 20              # Flash 粗筛每批数量

    # ── Redis TTL ─────────────────────────────────────────────
    redis_llm_cache_ttl: int = 86400              # LLM 缓存 24h
    redis_url_dedup_ttl: int = 604800             # URL 去重 7天

    # ── SearXNG 限流 ─────────────────────────────────────────
    searxng_rate_limit_per_minute: int = 10


settings = Settings()
