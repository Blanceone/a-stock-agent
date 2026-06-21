# A股宏观锚定投研智能体 — 工程开发详细 Spec v2.0

> 本文档是在 `A股宏观锚定投研智能体 - 完整系统设计蓝图 v1.0.md` 的基础上，结合设计评审确认结论，形成的可直接指导编码的工程级规格说明。  
> **凡本文与 v1.0 蓝图有出入，以本文为准（均经用户明确确认）。**

---

## 目录

- [第一章 项目初始化与工程骨架](#ch1)
- [第二章 数据底座模块（infrastructure/）](#ch2)
- [第三章 DAG 智能节点（nodes/）](#ch3)
- [第四章 LangGraph 流程编排（graphs/）](#ch4)
- [第五章 计算工具层（tools/）](#ch5)
- [第六章 模块交互时序与数据流](#ch6)
- [第七章 验收测试矩阵](#ch7)

---

<a id="ch1"></a>
## 第一章 项目初始化与工程骨架

### 1.1 Git 初始化与 GitHub 远程仓库

```bash
# 1. 初始化本地仓库
cd d:\work\ai\project-one
git init
git add .
git commit -m "[init] 项目初始化"

# 2. 使用 PAT 创建并关联远程仓库（token 从 apis.txt 读取）
# 远程仓库命名：a-stock-agent
git remote add origin https://<github_token>@github.com/<username>/a-stock-agent.git
git branch -M main
git push -u origin main
```

> 凭证来源：`C:\Users\13979\Desktop\notes\apis.txt`，key 名称：`github token`、`github密码`

### 1.2 完整目录树

```
a_stock_agent/
├── config/
│   ├── settings.py             # 全局可配置项（阈值、开关、路径）
│   └── prompts/
│       ├── policy_parser.txt   # 政策概念提取 Prompt
│       ├── chain_splitter.txt  # 产业链拆解 Prompt
│       ├── entity_mapper.txt   # 实体相关性打分 Prompt
│       ├── news_coarse.txt     # 新闻一级粗筛 Prompt（Flash）
│       ├── news_deep.txt       # 新闻二级深读 Prompt（Pro）
│       └── sop_extractor.txt   # 战法结构化提取 Prompt
├── src/
│   ├── infrastructure/
│   │   ├── __init__.py
│   │   ├── database.py         # PostgreSQL / ChromaDB / Redis 初始化
│   │   ├── data_fetcher.py     # 结构化数据降级链
│   │   ├── rss_fetcher.py      # RSSHub 新闻轮询
│   │   ├── searxng_search.py   # SearXNG 封装（限流）
│   │   └── web_fetcher.py      # defuddle + Playwright 正文提取
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── policy_parser.py
│   │   ├── chain_splitter.py
│   │   ├── entity_mapper.py
│   │   ├── tech_ranker.py
│   │   ├── news_funnel.py
│   │   ├── resonance_alert.py
│   │   └── sop_learner.py
│   ├── graphs/
│   │   ├── __init__.py
│   │   ├── static_graph.py
│   │   └── dynamic_graph.py
│   └── tools/
│       ├── __init__.py
│       └── indicator_calc.py
├── data/
│   └── intermediate/           # 中间 JSON 缓存
├── resources/                  # 外部资源统一存放目录（.gitignore，不提交）
│   ├── models/                 # 本地 Embedding 模型（bge-small-zh-v1.5）
│   ├── policies/               # 政策 PDF 存放目录
│   └── defuddle-service/       # Node.js 正文提取微服务
│       ├── package.json
│       └── server.js
├── tests/
│   ├── test_infrastructure.py
│   ├── test_nodes.py
│   └── test_tools.py
├── scripts/
│   └── load_env.py             # 从 apis.txt 读取并生成 .env
├── .env                        # 运行时环境变量（不提交 Git）
├── .env.template               # 变量名模板（提交 Git）
├── .gitignore
├── docker-compose.yml
├── requirements.txt
└── main.py
```

### 1.3 requirements.txt（含版本锁定）

```
# LLM & 工作流
langchain==0.2.16
langchain-openai==0.1.25
langgraph==0.2.28
openai==1.45.0

# 数据接口
tushare==1.4.20
akshare==1.14.0

# 数据库
psycopg2-binary==2.9.9
chromadb==0.5.15
redis==5.0.8

# 向量嵌入（本地 bge-small-zh-v1.5）
FlagEmbedding==1.2.11
torch>=2.0.0

# 数值计算
pandas==2.2.2
numpy==1.26.4

# Web 抓取
playwright==1.46.0
requests==2.32.3
feedparser==6.0.11

# PDF 解析
PyMuPDF==1.24.9

# 调度
APScheduler==3.10.4

# 工具
python-dotenv==1.0.1
loguru==0.7.2
pydantic==2.8.2
```

### 1.4 docker-compose.yml（混合拓扑）

> **部署说明**：PostgreSQL / ChromaDB / Redis 运行在**本地** Docker；SearXNG / RSSHub 运行在**阿里云**服务器（8.137.174.58）。  
> 本文件只包含本地服务，阿里云侧服务通过独立 compose 文件管理。

```yaml
version: "3.9"

services:
  postgres:
    image: postgres:16-alpine
    container_name: astock_postgres
    environment:
      POSTGRES_USER: ${PG_USER}
      POSTGRES_PASSWORD: ${PG_PASSWORD}
      POSTGRES_DB: astock
    ports:
      - "5432:5432"
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    restart: unless-stopped

  chromadb:
    image: chromadb/chroma:0.5.15
    container_name: astock_chromadb
    ports:
      - "8000:8000"
    volumes:
      - ./data/chromadb:/chroma/chroma
    environment:
      IS_PERSISTENT: "TRUE"
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: astock_redis
    ports:
      - "6379:6379"
    command: redis-server --save 60 1 --loglevel warning
    volumes:
      - ./data/redis:/data
    restart: unless-stopped
```

**阿里云侧 compose（`docker-compose.cloud.yml`，部署于 8.137.174.58）：**

```yaml
version: "3.9"

services:
  searxng:
    image: searxng/searxng:latest
    container_name: astock_searxng
    ports:
      - "8080:8080"
    volumes:
      - ./searxng:/etc/searxng
    environment:
      SEARXNG_SECRET_KEY: ${SEARXNG_SECRET}
    restart: unless-stopped

  rsshub:
    image: diygod/rsshub:latest
    container_name: astock_rsshub
    ports:
      - "1200:1200"
    environment:
      NODE_ENV: production
    restart: unless-stopped
```

### 1.5 config/settings.py 完整定义

```python
"""
全局可配置项。
所有敏感值（API Key、密码）通过环境变量注入，禁止在此文件硬编码。
"""
import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # ── 数据库 ────────────────────────────────────────────────
    pg_dsn: str = os.getenv("PG_DSN", "postgresql://astock:astock@localhost:5432/astock")
    chromadb_host: str = os.getenv("CHROMADB_HOST", "localhost")
    chromadb_port: int = int(os.getenv("CHROMADB_PORT", "8000"))
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # ── 外部服务 ──────────────────────────────────────────────
    tushare_token: str = os.getenv("TUSHARE_TOKEN", "")
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    searxng_url: str = os.getenv("SEARXNG_URL", "http://8.137.174.58:8080")
    searxng_api_key: str = os.getenv("SEARXNG_API_KEY", "")
    rsshub_url: str = os.getenv("RSSHUB_URL", "http://8.137.174.58:1200")
    defuddle_service_url: str = os.getenv("DEFUDDLE_URL", "http://localhost:3000/extract")

    # ── 模型 ID ───────────────────────────────────────────────
    model_flash: str = "deepseek-chat"       # V4-Flash，高频/粗筛
    model_pro: str = "deepseek-reasoner"     # V4-Pro，深度推理

    # ── 资源根目录 ───────────────────────────────────────────
    resources_base: str = os.getenv("RESOURCES_BASE", r"D:\work\ai\project-one\resources")

    # ── 嵌入模型（本地 bge-small-zh-v1.5）──────────────────────
    embedding_model_path: str = os.path.join(resources_base, "models", "bge-small-zh-v1.5")
    chroma_collection_name: str = "stock_business"

    # ── 三共振阈值（可调）────────────────────────────────────
    resonance_news_score_threshold: float = 0.7    # 新闻利好评分 ≥ 此值
    resonance_capital_inflow_pct: float = 0.02     # 主力净流入占比 ≥ 2%
    resonance_volume_ratio: float = 2.0            # 量比 ≥ 2.0

    # ── 实体映射过滤条件 ─────────────────────────────────────
    entity_chroma_top_k: int = 30                  # ChromaDB 召回候选数
    entity_llm_score_threshold: float = 0.6        # LLM 相关性打分下限
    entity_max_circ_mv: float = 2_000_000.0        # 流通市值上限（万元）= 200亿

    # ── 技术面多因子权重 ─────────────────────────────────────
    tech_weight_ma_alignment: float = 0.40
    tech_weight_volume_ratio: float = 0.25
    tech_weight_recent_gain: float = 0.20
    tech_weight_small_cap: float = 0.15

    # ── 新闻漏斗 ─────────────────────────────────────────────
    news_coarse_batch_size: int = 20               # Flash 粗筛每批数量

    # ── Redis TTL ─────────────────────────────────────────────
    redis_llm_cache_ttl: int = 86400               # LLM 缓存 24h
    redis_url_dedup_ttl: int = 604800              # URL 去重 7天

    # ── SearXNG 限流 ─────────────────────────────────────────
    searxng_rate_limit_per_minute: int = 10

settings = Settings()
```

### 1.6 凭证加载机制（scripts/load_env.py）

```python
"""
从 C:/Users/13979/Desktop/notes/apis.txt 读取 key:value 对，
写入 .env 文件供 python-dotenv 加载。
每次启动前执行一次即可。
"""
import re
from pathlib import Path

APIS_FILE = Path(r"C:/Users/13979/Desktop/notes/apis.txt")
ENV_FILE = Path(".env")

KEY_MAP = {
    "tushare token":    "TUSHARE_TOKEN",
    "deepseek api":     "DEEPSEEK_API_KEY",
    "github token":     "GITHUB_TOKEN",
    "阿里云服务器公网IP": "ALIYUN_IP",
}

def load_env():
    text = APIS_FILE.read_text(encoding="utf-8")
    env_lines = []
    for raw_key, env_key in KEY_MAP.items():
        pattern = rf"{re.escape(raw_key)}[：:]\s*(\S+)"
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            env_lines.append(f'{env_key}="{m.group(1)}"')
    ENV_FILE.write_text("\n".join(env_lines), encoding="utf-8")
    print(f"[load_env] 已写入 {len(env_lines)} 个环境变量到 .env")

if __name__ == "__main__":
    load_env()
```

---

<a id="ch2"></a>
## 第二章 数据底座模块（infrastructure/）

### 2.1 database.py — 数据库初始化

#### 职责
统一管理 PostgreSQL 连接池、ChromaDB 客户端、Redis 客户端及建表 DDL。

#### 接口定义

```python
# 模块级单例
pg_pool: psycopg2.pool.SimpleConnectionPool   # PostgreSQL 连接池（max 10）
chroma_client: chromadb.HttpClient            # ChromaDB HTTP 客户端
chroma_collection: chromadb.Collection        # stock_business collection
redis_client: redis.Redis                     # Redis 客户端

def get_pg_conn() -> psycopg2.connection: ...  # 从池取连接
def release_pg_conn(conn) -> None: ...         # 归还连接
def init_all() -> None: ...                    # 启动时调用，初始化全部连接+建表
```

#### PostgreSQL 表结构 DDL

```sql
-- 股票基础信息表
CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code     VARCHAR(12)  PRIMARY KEY,   -- 如 000001.SZ
    name        VARCHAR(50)  NOT NULL,
    industry    VARCHAR(50),                -- 申万一级行业
    circ_mv     NUMERIC(18,4),              -- 流通市值（万元）
    is_st       BOOLEAN      DEFAULT FALSE,
    list_status VARCHAR(2)   DEFAULT 'L',   -- L=上市 D=退市 P=暂停
    updated_at  TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_stock_basic_st  ON stock_basic(is_st);
CREATE INDEX IF NOT EXISTS idx_stock_basic_mv  ON stock_basic(circ_mv);

-- SOP 待审战法表（人工审核入口）
CREATE TABLE IF NOT EXISTS sop_pending (
    id           SERIAL PRIMARY KEY,
    concept      VARCHAR(100),               -- 关联概念
    viewpoint    TEXT         NOT NULL,      -- 核心观点
    conditions   TEXT         NOT NULL,      -- 触发条件
    scenarios    TEXT,                       -- 适用场景
    source_url   TEXT,                       -- 来源 URL
    conflicts_with INTEGER[],               -- 冲突的 sop_active.id 数组
    created_at   TIMESTAMP    DEFAULT NOW()
);

-- SOP 生效战法表（仅人工审核后写入）
CREATE TABLE IF NOT EXISTS sop_active (
    id           SERIAL PRIMARY KEY,
    concept      VARCHAR(100),
    viewpoint    TEXT         NOT NULL,
    conditions   TEXT         NOT NULL,
    scenarios    TEXT,
    source_url   TEXT,
    approved_by  VARCHAR(50),
    approved_at  TIMESTAMP    DEFAULT NOW()
);
```

#### ChromaDB 初始化（bge-small-zh-v1.5）

```python
from FlagEmbedding import FlagModel
import chromadb
from chromadb import EmbeddingFunction

class BGESmallZHEmbedding(EmbeddingFunction):
    """本地 bge-small-zh-v1.5，无需 API 调用"""
    def __init__(self, model_path: str):
        self.model = FlagModel(model_path, use_fp16=True)

    def __call__(self, input: list[str]) -> list[list[float]]:
        embeddings = self.model.encode(input, normalize_embeddings=True)
        return embeddings.tolist()

def init_chromadb(host, port, model_path, collection_name) -> tuple:
    client = chromadb.HttpClient(host=host, port=port)
    ef = BGESmallZHEmbedding(model_path)
    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"}
    )
    return client, collection
```

#### Redis 初始化与 Key 命名规范

```python
# Key 前缀规范
REDIS_KEY_NEWS_DEDUP   = "dedup:news:{article_id}"      # TTL: 7d
REDIS_KEY_URL_DEDUP    = "dedup:url:{url_md5}"          # TTL: 7d
REDIS_KEY_LLM_CACHE    = "llm:{prompt_hash}"            # TTL: 24h
REDIS_KEY_RATE_LIMIT   = "rate:searxng:{minute_bucket}" # TTL: 120s
REDIS_KEY_NEXT_WEIGHTS = "weights:stock:{ts_code}"      # 次日游资权重
```

---

### 2.2 data_fetcher.py — 结构化数据降级链

#### 职责
封装所有结构化数据获取逻辑，实现 Tushare 优先、异常时自动降级，并记录降级事件。

#### 异常类定义

```python
class TushareQuotaError(Exception): ...    # 积分不足
class TushareRateLimitError(Exception): ... # 超频
class DataFetchError(Exception): ...        # 所有来源均失败
```

#### 行情/财务数据降级链

```python
def fetch_daily(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    获取日线行情，降级顺序：Tushare pro.daily → a-stock-data
    返回列：ts_code, trade_date, open, high, low, close, vol, amount
    """

def fetch_stock_basic() -> pd.DataFrame:
    """
    获取全A股基础信息，纯 Tushare pro.stock_basic，无降级（数据唯一权威源）
    返回列：ts_code, name, industry, circ_mv, list_status
    """

def fetch_moneyflow_intraday(ts_code: str) -> dict:
    """
    盘中主力净流入（a-stock-data push2 接口）
    返回：{ts_code, net_inflow, net_inflow_pct, timestamp}
    注意：此接口不走 Tushare，直接调 a-stock-data
    """

def fetch_top_list(trade_date: str) -> pd.DataFrame:
    """
    T+1 龙虎榜数据，仅 Tushare pro.top_list
    返回列：ts_code, trade_date, name, reason, buy_amount, sell_amount
    """
```

#### 主营业务文本四级降级链

```python
def fetch_business_description(ts_code: str, company_name: str) -> str:
    """
    主营业务文本，四级降级：
    L1: 东方财富 F10 核心题材+主营介绍
    L2: a-stock-data 年报经营分析
    L3: AKShare stock_zygc_em 结构化拼接
    L4: Tushare pro.stock_company main_business 字段（兜底）
    降级事件写日志：logger.warning(f"[降级] {ts_code} 主营业务 L{level}→L{level+1}")
    """
```

#### 降级日志规范

```python
from loguru import logger

# 降级事件统一格式
logger.warning(
    "[DataFallback] ts_code={ts_code} api={api_name} "
    "reason={reason} fallback_to={fallback_target}"
)
# 示例：
# [DataFallback] ts_code=000001.SZ api=tushare.daily
#   reason=积分不足(2020) fallback_to=a-stock-data
```

---

### 2.3 rss_fetcher.py — RSSHub 新闻抓取

#### 职责
每分钟轮询 RSSHub 财联社电报，去重后将新增条目推入内存队列供 `news_funnel.py` 消费。

#### 输出数据结构

```python
@dataclass
class NewsItem:
    article_id: str         # RSS <guid> 作为去重 key
    title: str
    summary: str            # RSS <description>
    pub_time: datetime
    source: str = "财联社"
```

#### 核心流程

```python
async def poll_rss(queue: asyncio.Queue, interval_sec: int = 60):
    """
    无限轮询，每 interval_sec 秒拉一次
    1. feedparser 解析 {RSSHUB_URL}/cls/telegraph
    2. 遍历 entries，检查 Redis Key dedup:news:{article_id}
    3. 未见过的条目写入 queue，并在 Redis 中标记（TTL 7d）
    """
```

---

### 2.4 searxng_search.py — SearXNG 封装

#### 职责
封装 SearXNG 搜索 API，实现 Redis 令牌桶限流（≤10次/分钟）。  
**仅供 `static_graph` 产业链拆解和 `sop_learner` 低频调用，严禁 dynamic_graph 调用。**

#### 接口

```python
@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str

def search(query: str, num_results: int = 10) -> list[SearchResult]:
    """
    同步搜索接口。
    限流：使用 Redis INCR + EXPIRE 实现滑动窗口计数器，超限抛 RateLimitError。
    """
```

#### Redis 限流实现

```python
def _check_rate_limit(redis_client, limit: int = 10):
    bucket_key = f"rate:searxng:{int(time.time() // 60)}"
    count = redis_client.incr(bucket_key)
    if count == 1:
        redis_client.expire(bucket_key, 120)
    if count > limit:
        raise RateLimitError(f"SearXNG 已达 {limit} 次/分钟限制")
```

---

### 2.5 web_fetcher.py — 网页正文提取链

#### 职责
对给定 URL 提取干净的 Markdown 正文，三层降级：

```
L1: defuddle（Node.js 微服务，处理静态 HTML）
  ↓ 失败（超时/返回空）
L2: Playwright（无头浏览器，处理 JS 渲染页面）
  ↓ 失败
L3: requests + BeautifulSoup（最后兜底）
```

#### defuddle 微服务（resources/defuddle-service/server.js）

```javascript
// 接受 POST /extract { url: "..." }
// 返回 { markdown: "...", title: "..." }
const { Defuddle } = require("@kepano/defuddle");
const express = require("express");
const app = express();
app.use(express.json());

app.post("/extract", async (req, res) => {
  const { url } = req.body;
  const response = await fetch(url);
  const html = await response.text();
  const result = new Defuddle(html, { url }).parse();
  res.json({ markdown: result.content, title: result.title });
});
app.listen(3000);
```

#### Python 调用接口

```python
def fetch_article(url: str) -> str:
    """
    返回正文 Markdown 字符串。
    Redis URL 去重：若已抓取，直接从 Redis 缓存返回（TTL 7d）。
    """
```

---

<a id="ch3"></a>
## 第三章 DAG 智能节点（nodes/）

> 所有节点函数接受 `state: dict` 并返回 `dict`（LangGraph 约定），  
> state 的完整 Schema 见[第四章 4.1](#ch4)。

---

### 3.1 policy_parser.py — 政策解读与概念提取

#### 输入
```python
state["policy_pdf_path"]: str   # 政策 PDF 本地路径
```

#### 处理流程

```
1. PyMuPDF 解析 PDF
   └─ 提取目录大纲（TOC）→ [(level, title, page), ...]
   └─ 按章节切片正文文本

2. V4-Pro：读取大纲 → 圈定重点章节编号
   Prompt 模板：config/prompts/policy_parser.txt
   输入：TOC JSON
   输出：{"key_sections": [2, 3, 5], "reason": "..."}

3. 提取重点章节正文 → 向量化 → 存入 ChromaDB（collection: policy_chunks）
   doc_id 格式：{pdf_md5}_{section_idx}

4. V4-Pro：从重点章节文本提取产业概念词
   Prompt 约束：
     - 必须是具体可炒作的产业方向（如"低空经济""固态电池"）
     - 过滤宏观空词：["高质量发展","改革","创新","绿色","智慧城市"]
   输出 JSON：见下方
```

#### 输出

```python
state["concepts"]: list[dict]
# 示例：
[
  {"concept": "低空经济", "source_section": "第三章第二节", "confidence": 0.92},
  {"concept": "具身智能", "source_section": "第四章第一节", "confidence": 0.88}
]
```

---

### 3.2 chain_splitter.py — 产业链深度拆解

#### 输入
```python
state["concepts"]: list[dict]   # 来自 policy_parser
```

#### 处理流程（每个 concept 独立处理后合并）

```
1. SearXNG 搜索研报
   query = f"{concept} 产业链 研报"
   返回前 5 条 URL

2. web_fetcher 批量抓取正文（L1→L2→L3 降级）
   过滤：正文长度 < 500 字的跳过

3. V4-Pro 产业链拆解
   Prompt 模板：config/prompts/chain_splitter.txt
   输入：研报正文拼接（最多 8000 tokens）+ concept
   输出：树状 JSON（见下方 Schema）
```

#### 输出 JSON Schema

```python
state["industry_chains"]: list[dict]
# Schema：
{
  "concept": "具身智能",
  "layers": [
    {
      "layer_name": "基础底座层",
      "nodes": [
        {
          "node_name": "AI芯片设计",
          "description": "用于具身智能感知与决策的专用芯片",
          "keywords": ["GPU", "NPU", "算力"]
        }
      ]
    },
    {
      "layer_name": "应用层",
      "nodes": [
        {
          "node_name": "人形机器人",
          "description": "...",
          "keywords": ["双足机器人", "灵巧手"]
        }
      ]
    }
  ],
  "source_urls": ["https://..."]
}
```

---

### 3.3 entity_mapper.py — 实体映射与去伪存真

#### 输入
```python
state["industry_chains"]: list[dict]  # 来自 chain_splitter
```

#### 处理流程（每个产业链 node 独立处理）

```
1. ChromaDB 语义召回
   query = f"{node_name} {description} {' '.join(keywords)}"
   top_k = settings.entity_chroma_top_k (= 30)
   返回候选股列表：[(ts_code, name, business_text, distance), ...]

2. V4-Pro 精判打分
   Prompt 模板：config/prompts/entity_mapper.txt
   输入：节点描述 + 候选股的 business_text（每次最多 15 只，分批）
   输出：[{"ts_code": "...", "score": 0.85, "reason": "主营占比70%以上"}]
   过滤：score < settings.entity_llm_score_threshold 的丢弃

3. SQL 硬过滤（两道红线）
   SELECT ts_code FROM stock_basic
   WHERE ts_code = ANY(%(codes)s)
     AND is_st = FALSE
     AND list_status = 'L'
     AND circ_mv < %(max_mv)s   -- 默认 2000000 万元 = 200亿
```

#### 输出

```python
state["stock_pool"]: list[dict]
# Schema：
[
  {
    "ts_code": "688018.SH",
    "name": "乐鑫科技",
    "concept": "具身智能",
    "layer": "基础底座层",
    "node": "AI芯片设计",
    "llm_score": 0.85,
    "circ_mv": 180000.0
  }
]
```

---

### 3.4 tech_ranker.py — 技术面多因子排序

#### 输入
```python
state["stock_pool"]: list[dict]  # 来自 entity_mapper
```

#### 处理流程

```
1. Tushare pro.daily 批量拉取（最多 50 只/批）
   参数：ts_code_list, start_date=近60个交易日, fields=...
   → 调用 indicator_calc 模块（禁止在本节点做任何计算）

2. 调用 indicator_calc.calc_all(df) → 返回各股得分

3. 多因子加权排序（权重见 settings）
   final_score = (
     ma_align_score  * 0.40 +
     vol_ratio_score * 0.25 +
     gain_score      * 0.20 +
     small_cap_score * 0.15
   )

4. 分梯队：
   第一梯队：final_score ≥ 0.7 且均线多头排列
   第二梯队：0.5 ≤ final_score < 0.7
```

#### 输出

```python
state["ranked_stocks"]: dict
# Schema：
{
  "tier1": [{"ts_code": "...", "name": "...", "score": 0.82, "reason": "均线多头+量比3.2"}, ...],
  "tier2": [{"ts_code": "...", "name": "...", "score": 0.61, "reason": "..."}, ...]
}
```

---

### 3.5 news_funnel.py — 新闻智能漏斗

#### 输入
```python
news_items: list[NewsItem]      # 来自 rss_fetcher 内存队列
state["industry_chains"]: list  # 用于比对概念命中
```

#### 处理流程

```
一级粗筛（V4-Flash，批量）
  每批 ≤ 20 条，Prompt 见 config/prompts/news_coarse.txt
  输入：[{"id":"...", "title":"...", "summary":"..."}]
  输出：[{"id":"...", "is_relevant": true/false}]
  → 过滤掉 is_relevant=false 的条目（目标：过滤60%以上）

二级深读（V4-Pro，逐条）
  对通过粗筛的条目，Prompt 见 config/prompts/news_deep.txt
  输出：
  {
    "impact_concept": "固态电池",
    "impact_node": "电解质材料",
    "sentiment": "positive",       # positive / negative / neutral
    "score": 0.82,                 # 0~1，影响强度
    "reason": "国家队首次明确补贴固态电解质生产线"
  }

Redis 去重与 LLM 缓存：
  去重 key：dedup:news:{article_id}
  缓存 key：llm:{sha256(title+summary)}，TTL 24h
```

#### 输出

```python
state["flagged_news"]: list[dict]
# Schema（通过二级深读的条目）：
[
  {
    "article_id": "cls_12345",
    "title": "...",
    "pub_time": "2024-01-15T10:30:00",
    "impact_concept": "固态电池",
    "impact_node": "电解质材料",
    "sentiment": "positive",
    "score": 0.82
  }
]
```

---

### 3.6 resonance_alert.py — 三共振预警

#### 盘中监控（9:30-15:00，每10分钟触发一次）

```
数据拉取（三路并行）：
  ① a-stock-data push2 → 个股主力净流入（stock_pool 内所有标的）
     字段：ts_code, net_inflow, net_inflow_pct
  ② AKShare 概念板块资金流排行
     函数：ak.stock_fund_flow_concept()
     字段：板块名称, 主力净流入, 涨跌幅
  ③ 同花顺北向实时接口
     字段：north_net_inflow（亿元）

三共振判断（对 stock_pool 中每只股票）：
  condition_A = flagged_news 中存在 impact_concept 与该股 concept 匹配
                且 score ≥ settings.resonance_news_score_threshold (0.7)
  condition_B = net_inflow_pct ≥ settings.resonance_capital_inflow_pct (0.02)
  condition_C = vol_ratio ≥ settings.resonance_volume_ratio (2.0)  # 由 indicator_calc 提供

  if condition_A and condition_B and condition_C:
      → 输出红色预警
```

#### 盘后复盘（15:30后，T+1前）

```
Tushare pro.top_list(trade_date=T) + pro.top_inst(trade_date=T)
→ 识别知名游资席位（预设席位白名单配置于 settings）
→ UPDATE Redis Key weights:stock:{ts_code} 
  {"hot_money_score": <出现次数加权>, "expire": 次日9:25}
→ 次日 tech_ranker 优先展示有游资关注度权重的标的
```

#### 预警输出格式

```
【三共振预警🔴】2024-01-15 10:40
━━━━━━━━━━━━━━━━━━━━━━━━
概念：固态电池
核心标的：
  ▶ 宁德时代(300750.SZ) | 分值 0.91
    → 消息：国家补贴固态电解质（评分 0.85）
    → 资金：主力净流入 3.2%
    → 技术：量比 2.8，均线多头
━━━━━━━━━━━━━━━━━━━━━━━━
```

#### 输出

```python
state["alerts"]: list[dict]
# Schema：
[
  {
    "trigger_time": "2024-01-15T10:40:00",
    "concept": "固态电池",
    "ts_code": "300750.SZ",
    "name": "宁德时代",
    "composite_score": 0.91,
    "news_score": 0.85,
    "capital_inflow_pct": 0.032,
    "vol_ratio": 2.8,
    "alert_text": "【三共振预警🔴】..."
  }
]
```

---

### 3.7 sop_learner.py — 游资 SOP 自学习

#### ⚠️ 红线：只能写入 `sop_pending`，严禁写 `sop_active`

#### 处理流程

```
1. SearXNG 搜索（低频，每次学习任务调用）
   queries = ["游资复盘 打板战法", "龙头股战法 条件", "涨停板 操盘逻辑"]
   每个 query 取前 5 URL

2. web_fetcher 抓取正文（URL 去重，避免重复抓）

3. V4-Pro 结构化提取
   Prompt：config/prompts/sop_extractor.txt
   输出 JSON：
   {
     "concept": "打板战法",
     "viewpoint": "首板低位龙头，必须是本轮最强概念首只涨停",
     "conditions": "1.概念近期未启动 2.流通市值<50亿 3.换手率<5%",
     "scenarios": "市场热度中性偏强，有明确主线",
     "source_url": "https://..."
   }

4. 冲突检测
   L1: ChromaDB 向量相似度检索 sop_active，top_3
       若 cosine_distance < 0.15（极相似），认为冲突
   L2: 规则检测 —— 若 viewpoint 中包含明确反义词
       如"不追高" vs"可以追高"

5. 写入 sop_pending（含 conflicts_with 字段）
```

---

<a id="ch4"></a>
## 第四章 LangGraph 流程编排（graphs/）

### 4.1 static_graph.py — 静态产业链图谱构建流水线

#### 触发方式
按需手动触发，入口：`main.py --mode static --pdf <path>`

#### LangGraph State Schema（完整定义）

```python
from typing import TypedDict, Optional

class StaticGraphState(TypedDict):
    policy_pdf_path: str
    concepts: Optional[list[dict]]
    # [{"concept": str, "source_section": str, "confidence": float}]
    industry_chains: Optional[list[dict]]
    stock_pool: Optional[list[dict]]
    ranked_stocks: Optional[dict]
    # {"tier1": [...], "tier2": [...]}
    error_node: Optional[str]
    error_msg: Optional[str]
    retry_count: int
```

#### 节点注册与边定义

```python
from langgraph.graph import StateGraph, END

def build_static_graph():
    graph = StateGraph(StaticGraphState)
    graph.add_node("policy_parser",  policy_parser.run)
    graph.add_node("chain_splitter", chain_splitter.run)
    graph.add_node("entity_mapper",  entity_mapper.run)
    graph.add_node("tech_ranker",    tech_ranker.run)
    graph.add_node("error_handler",  _error_handler)
    graph.set_entry_point("policy_parser")
    graph.add_edge("policy_parser",  "chain_splitter")
    graph.add_edge("chain_splitter", "entity_mapper")
    graph.add_edge("entity_mapper",  "tech_ranker")
    graph.add_edge("tech_ranker",    END)
    return graph.compile()
```

#### 错误处理规范

每个节点统一捕获异常并返回：
```python
# 正常
return {**result, "error_node": None, "error_msg": None}
# 异常（最多重试2次，超限进 error_handler）
return {"error_node": node_name, "error_msg": str(e),
        "retry_count": state.get("retry_count", 0) + 1}
```

---

### 4.2 dynamic_graph.py — 动态监控流水线

#### 调度任务表

| 任务 | 触发时间 | 函数 |
|------|---------|------|
| 新闻轮询粗筛 | 每分钟 | `rss_and_funnel_job` |
| 三共振盘中检测 | 工作日 9:30-15:00，每10分钟 | `resonance_check_job` |
| 盘后龙虎榜更新 | 工作日 15:30 | `top_list_update_job` |
| SOP 自学习 | 每周日 02:00 | `sop_learn_job` |

#### DynamicGraphState Schema

```python
class DynamicGraphState(TypedDict):
    stock_pool: list[dict]       # Redis 读入，来自 static_graph
    industry_chains: list[dict]  # Redis 读入，来自 static_graph
    news_queue_snapshot: list
    flagged_news: list[dict]
    capital_data: dict
    alerts: list[dict]
```

#### 状态隔离说明

- `static_graph` 完成后将结果序列化至 Redis Key `static:stock_pool` / `static:industry_chains`
- `dynamic_graph` 从 Redis 读入，**只读不改**静态数据
- 每轮预警写入 Redis Key `dynamic:alerts:{yyyymmdd}`（LPUSH）

#### APScheduler 配置

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
scheduler.add_job(rss_and_funnel_job,  IntervalTrigger(minutes=1))
scheduler.add_job(resonance_check_job, CronTrigger(
    day_of_week="mon-fri", hour="9-14", minute="*/10"))
scheduler.add_job(top_list_update_job, CronTrigger(
    day_of_week="mon-fri", hour=15, minute=30))
scheduler.add_job(sop_learn_job,       CronTrigger(day_of_week="sun", hour=2))
```

---

<a id="ch5"></a>
## 第五章 计算工具层（tools/indicator_calc.py）

> ⚠️ **红线**：所有技术指标计算必须在此模块完成，**禁止** LLM 生成计算代码或推理数值。

### 函数签名

```python
import pandas as pd

def calc_ma(df: pd.DataFrame, windows: list[int] = [5, 10, 20]) -> pd.DataFrame:
    """输入：df 含 trade_date(str 升序), close(float)；追加 ma5/ma10/ma20 列"""

def calc_volume_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """量比 = 当日vol / 近5日均vol；追加 vol_ratio 列"""

def calc_recent_gain(df: pd.DataFrame, days: int = 10) -> float:
    """近 N 日涨幅 = (最新close - N日前close) / N日前close"""

def is_ma_bullish(df: pd.DataFrame) -> bool:
    """ma5 > ma10 > ma20"""

def score_ma_alignment(df: pd.DataFrame) -> float:
    """多头=1.0 / ma5>ma10=0.6 / 其余=0.2"""

def score_volume_ratio(vol_ratio: float) -> float:
    """≥3→1.0 / 2~3→0.8 / 1.5~2→0.6 / 1~1.5→0.4 / <1→0.1"""

def score_recent_gain(gain: float) -> float:
    """5~15%→1.0 / 15~25%→0.7 / >25%→0.3 / 0~5%→0.5 / 负→0.2"""

def score_small_cap(circ_mv: float) -> float:
    """<20亿→1.0 / <50亿→0.8 / <100亿→0.5 / ≥100亿→0.2（单位：万元）"""

def calc_all(df: pd.DataFrame, circ_mv: float) -> dict:
    """
    返回：{ma_align_score, vol_ratio_score, gain_score, small_cap_score,
           is_ma_bullish, latest_vol_ratio, recent_gain_10d}
    """
```

---

<a id="ch6"></a>
## 第六章 模块交互时序与数据流

### 6.1 static_graph 完整调用链

```
main.py --mode static --pdf <path>
 └─ build_static_graph().invoke(state)
     ├─ [policy_parser]
     │   ├─ PyMuPDF → TOC + 章节文本
     │   ├─ LLM(V4-Pro, policy_parser.txt) → key_sections
     │   ├─ ChromaDB.add(policy_chunks)
     │   └─ LLM(V4-Pro) → state["concepts"]
     ├─ [chain_splitter]
     │   └─ for concept: SearXNG → web_fetcher → LLM(V4-Pro) → state["industry_chains"]
     ├─ [entity_mapper]
     │   └─ for node: ChromaDB(top30) → LLM(V4-Pro,batch15) → SQL过滤 → state["stock_pool"]
     └─ [tech_ranker]
         └─ Tushare → indicator_calc.calc_all × each → state["ranked_stocks"]
             → Redis.set("static:stock_pool", ...)
```

### 6.2 dynamic_graph 完整调用链

```
APScheduler
 ├─ [每分钟] rss_fetcher → news_funnel
 │   ├─ Redis 去重
 │   ├─ LLM(V4-Flash, batch≤20) → 粗筛(过滤≥60%)
 │   └─ LLM(V4-Pro, 逐条) → state["flagged_news"]
 ├─ [每10分钟] resonance_check_job
 │   ├─ a-stock-data push2 → net_inflow_pct
 │   ├─ AKShare → sector_fund_flow
 │   ├─ 同花顺 → north_flow
 │   └─ 三共振判断 → state["alerts"] → Redis.lpush
 ├─ [15:30] Tushare top_list → Redis weights:stock:{ts_code}
 └─ [周日02:00] SearXNG → web_fetcher → LLM(V4-Pro) → pg INSERT sop_pending
```

### 6.3 节点间数据流转格式汇总

| 来源 | 目标 | State Key | 类型 |
|------|------|-----------|------|
| main.py | policy_parser | `policy_pdf_path` | `str` |
| policy_parser | chain_splitter | `concepts` | `list[ConceptItem]` |
| chain_splitter | entity_mapper | `industry_chains` | `list[ChainItem]` |
| entity_mapper | tech_ranker | `stock_pool` | `list[StockItem]` |
| tech_ranker | Redis | `static:stock_pool` | JSON |
| Redis | dynamic_graph | `stock_pool` | `list[StockItem]` |
| rss_fetcher | news_funnel | asyncio.Queue | `list[NewsItem]` |
| news_funnel | resonance_alert | `flagged_news` | `list[FlaggedNews]` |
| resonance_alert | Redis | `dynamic:alerts:{date}` | JSON List |

---

<a id="ch7"></a>
## 第七章 验收测试矩阵

### Phase 1 验收

```bash
# 容器健康
docker ps --format "table {{.Names}}\t{{.Status}}"
# 期望：astock_postgres / astock_chromadb / astock_redis 均 Up

# 阿里云 SearXNG
curl -H "Authorization: Bearer <SEARXNG_API_KEY>" \
  "http://8.137.174.58:8080/search?q=商业航天&format=json"
# 期望：results 数组非空

# RSSHub
curl "http://8.137.174.58:1200/cls/telegraph"
# 期望：RSS XML 含最新电报
```

```sql
-- stock_basic 入库
SELECT COUNT(*) FROM stock_basic WHERE list_status='L' AND is_st=FALSE;
-- 期望：> 5000

-- 性能
EXPLAIN ANALYZE SELECT ts_code FROM stock_basic
WHERE name NOT LIKE '%ST%' AND circ_mv < 2000000;
-- 期望：< 50ms
```

```python
# 向量语义检索
results = chroma_collection.query(query_texts=["AI芯片设计 GPU算力"], n_results=5)
names = [r["name"] for r in results["metadatas"][0]]
assert "海光信息" in names or "寒武纪" in names

# 业务描述质量（抽检10只，均 >150 字）
for ts_code in SAMPLE_10:
    text = fetch_business_description(ts_code, "")
    assert len(text) > 150
```

### Phase 2 验收

```python
# 空词过滤
concepts = policy_parser.run(state)["concepts"]
assert all(c["concept"] not in ["高质量发展","改革","创新"] for c in concepts)

# 产业链节点覆盖
node_names = [n["node_name"] for l in chains[0]["layers"] for n in l["nodes"]]
assert any("芯片" in n for n in node_names)
assert any("机器人" in n for n in node_names)

# 无ST
for s in stock_pool:
    assert "ST" not in s["name"]

# 梯队分值
assert all(s["score"] >= 0.7 for s in ranked["tier1"])
assert all(0.5 <= s["score"] < 0.7 for s in ranked["tier2"])
```

### Phase 3 验收

```python
# 粗筛过滤率
passed = coarse_filter(load_100_news())
assert 1 - len(passed)/100 >= 0.6

# 固态电池新闻识别
result = deep_read(SOLID_BATTERY_NEWS, [])
assert result["impact_concept"] == "固态电池"
assert result["sentiment"] == "positive"

# 三共振预警模拟
alerts = check_resonance(MOCK_STATE)["alerts"]
assert "三共振预警" in alerts[0]["alert_text"]
assert "宁德时代" in alerts[0]["alert_text"]
```

### Phase 4 验收

```sql
SELECT COUNT(*) FROM sop_pending;    -- 期望：≥ 10
SELECT COUNT(*) FROM sop_active;     -- 期望：= 0（自动写入为0，仅人工数据）
SELECT id, conflicts_with FROM sop_pending
WHERE array_length(conflicts_with, 1) > 0;  -- 验证冲突检测生效
```

---

## 附录：每轮提交规范

```bash
pytest tests/ -v                            # 确保测试通过
git add .
git commit -m "[<module>] <简短描述>"
git push
```

> ⚠️ `.env` 必须在 `.gitignore` 中，绝不提交密钥文件。

