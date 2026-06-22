# AGENTS.md

This file provides guidance to Qoder (qoder.com) when working with code in this repository.

## 项目定位

基于固定 DAG 工作流与 LLM 智能节点的 A 股投研系统。以国家宏观政策为锚定，利用 RAG 和语义理解构建产业链知识图谱，结合高频新闻与资金面共振，输出买卖预警建议。

详细设计蓝图见：`spec_out/A股宏观锚定投研智能体 - 完整系统设计蓝图 v1.0.md`

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 工作流编排 | LangGraph (StateGraph) |
| LLM 集成 | OpenAI SDK (DeepSeek 兼容) + 双模型策略 |
| 数值计算 | Pandas + NumPy |
| 结构化存储 | PostgreSQL (psycopg2) |
| 向量存储 | ChromaDB (REST API, bge-small-zh-v1.5 embedding) |
| 缓存/限流 | Redis (pyredis) |
| Web API | FastAPI + Uvicorn |
| PDF 解析 | PyMuPDF (fitz) |
| 运行时 | Python 3.11+ |
| 基建部署 | Docker / docker-compose |

---

## 项目结构

```
a_stock_agent/
├── config/
│   ├── settings.py             # 全局配置（pydantic-settings，从 .env 读取）
│   └── prompts/                # Prompt 模板（6个 .txt 文件）
│       ├── policy_parser.txt   # 政策解读（两段式，--- 分隔）
│       ├── chain_splitter.txt  # 产业链拆解
│       ├── entity_mapper.txt   # 实体映射打分
│       ├── news_coarse.txt     # 新闻粗筛
│       ├── news_deep.txt       # 新闻深读
│       └── sop_extractor.txt   # SOP 图谱提取
├── src/
│   ├── infrastructure/         # 数据底座
│   │   ├── database.py         # PG/ChromaDB/Redis 初始化 + ChromaDB v0.x 兼容层
│   │   ├── data_fetcher.py     # 数据降级链（Tushare → a-stock-data 东财API）
│   │   ├── rss_fetcher.py      # RSSHub 财联社电报抓取（NewsItem dataclass）
│   │   ├── searxng_search.py   # SearXNG 搜索封装（Redis 限流）
│   │   ├── semantic_init.py    # 语义知识库初始化（主营业务 → ChromaDB）
│   │   └── web_fetcher.py      # 三层降级链（defuddle → Playwright → BS4）
│   ├── nodes/                  # DAG 智能节点
│   │   ├── llm_utils.py        # LLM 调用封装（双模型 + 缓存 + JSON 鲁棒解析）
│   │   ├── policy_parser.py    # 步骤1: 政策解读与概念提取（V4-Pro）
│   │   ├── chain_splitter.py   # 步骤2: 产业链深度拆解（SearXNG + V4-Pro）
│   │   ├── entity_mapper.py    # 步骤3: ChromaDB 召回 + LLM 打分 + SQL 过滤
│   │   ├── tech_ranker.py      # 步骤4: 技术面多因子打分（indicator_calc）
│   │   ├── news_funnel.py      # 步骤5: 新闻漏斗（粗筛 Flash → 深读 Pro）
│   │   ├── resonance_alert.py  # 步骤6: 三共振预警（消息+资金+量比）
│   │   └── sop_learner.py      # SOP 自学习（pending → active, approved=FALSE）
│   ├── graphs/
│   │   ├── static_graph.py     # 静态图谱 DAG（按需触发）
│   │   └── dynamic_graph.py    # 动态监控 DAG（APScheduler 定时轮询）
│   └── tools/
│       └── indicator_calc.py   # 技术指标硬计算（禁止 LLM 替代）
├── scripts/                    # 运维/部署/诊断脚本
├── tests/                      # 测试套件（90 个用例）
│   ├── conftest.py             # 共享 fixtures
│   ├── test_phase1.py          # Phase 1: 配置 + 指标 + 降级链 + DDL
│   ├── test_phase2.py          # Phase 2: DAG 节点 + 数据流格式
│   ├── test_phase3.py          # Phase 3: 动态监控 + 三共振
│   ├── test_phase4.py          # Phase 4: SOP + Prompt + LLM 工具
│   ├── test_e2e.py             # E2E 集成测试（25 个，含真实 LLM/API 调用）
│   └── test_full_chain.py      # 全业务链路测试（9 个，端到端 DAG 执行）
├── api.py                      # FastAPI SOP 审核接口
├── main.py                     # 系统入口（4 种运行模式）
└── .env                        # 环境变量（不提交 Git）
```

---

## 常用命令

```bash
# 初始化基础设施（建表 + 全 A 股入库）
python main.py --mode init

# 静态图谱构建（需提供政策 PDF）
python main.py --mode static --pdf <policy_pdf_path>

# 动态监控（APScheduler 定时轮询）
python main.py --mode dynamic

# 语义知识库初始化（主营业务向量化 → ChromaDB）
python main.py --mode semantic

# 启动 SOP 审核 Web API
uvicorn api:app --host 0.0.0.0 --port 8088

# 运行全部测试（90 个用例）
pytest tests/ -v

# 仅运行 Phase 单元测试（56 个，~2s）
pytest tests/test_phase1.py tests/test_phase2.py tests/test_phase3.py tests/test_phase4.py -v

# 仅运行 E2E 集成测试（25 个，~35s，需真实 API）
pytest tests/test_e2e.py -v -s

# 仅运行全业务链路测试（9 个，~107s，含 LLM Pro 调用）
pytest tests/test_full_chain.py -v -s --tb=short

# 建立 SSH 隧道连接远程基础设施
python scripts/start_ssh_tunnels.py
```

---

## 核心架构

### 双流水线设计

- **`static_graph`**（按需触发）：政策 PDF → policy_parser(V4-Pro) → chain_splitter(SearXNG+V4-Pro) → entity_mapper(ChromaDB+V4-Pro+PG) → tech_ranker(K线+indicator_calc)，输出 tier1/tier2 排名股池
- **`dynamic_graph`**（定时轮询）：新闻 → news_funnel(Flash粗筛+Pro深读) → resonance_alert(三共振检查) → sop_learner(Flash提取→sop_active)，输出预警信号

### 双模型策略

| 场景 | 模型 | Model ID |
|------|------|----------|
| 高频粗筛、简单判断、SOP 提取 | DeepSeek V4-Flash | `deepseek-chat` |
| 深度推理、产业链拆解、精判打分 | DeepSeek V4-Pro | `deepseek-reasoner` |

DeepSeek API Base URL: `https://api.deepseek.com/v1`（OpenAI 兼容接口）

### 数据降级链

```
Tushare Pro（首选）
    ↓ 捕获异常（积分不足 / 超频）
a-stock-data 东财 API（补充，需 _EASTMONEY_HEADERS 请求头）
    ↓ 必须在日志中记录降级事件
```

### ChromaDB 兼容层

ChromaDB 服务端为 v0.5.x（REST API），客户端可能为 v1.x。项目使用 `_ChromaDBCompat` + `_CollectionCompat` 兼容层直接通过 REST API 通信。

**关键约束**：所有 collection 操作（count/add/query/get）**必须使用 UUID** 而非 collection name，否则会返回 `400 InvalidUUID`。UUID 在 `get_or_create_collection()` 时从服务端响应中提取。

### Redis Key 规范

| Key 模式 | 用途 | TTL |
|----------|------|-----|
| `dedup:news:{article_id}` | 新闻去重 | 24h |
| `dedup:url:{url_md5}` | URL 正文缓存 | 7d |
| `llm:{prompt_hash}` | LLM 响应缓存 | 24h |
| `rate:searxng:{minute_bucket}` | SearXNG 限流 | 2min |
| `weights:stock:{ts_code}` | 下一代权重 | 30d |
| `static:stock_pool` | 静态图谱结果 | 7d |
| `dynamic:alerts:{YYYYMMDD}` | 当日预警 | 3d |

### Prompt 模板规范

- 模板使用 Python `.format()` 渲染，**JSON 示例中的花括号必须转义**为 `{{` `}}`
- `policy_parser.txt` 两段式结构，用 `---` 分隔
- 所有模板位于 `config/prompts/` 目录

---

## 开发红线（Code Review 必查）

### 1. 数值计算隔离
所有技术指标（均线、量比、涨幅等）的计算**必须**写在 `src/tools/indicator_calc.py` 中，使用 Pandas 完成。**禁止** LLM 生成计算代码或直接推理数值。

### 2. SearXNG 使用范围
`SearXNG` **仅限**在以下两处低频调用：
- `static_graph` 的产业链拆解步骤（`chain_splitter.py`）
- Phase 4 的 SOP 自学习（`sop_learner.py`）

**禁止**在 `dynamic_graph` 的高频新闻轮询节点中调用 SearXNG。

### 3. SOP 人工闸门
`sop_learner.py` 从 `sop_pending` 读取并提取后，写入 `sop_active` 时 **`approved` 必须为 `FALSE`**。所有新战法须经人工在 Web UI 审核批准后才能生效。**严禁**自动将 `approved` 设为 `TRUE`。

### 4. 信源降级链顺序
获取行情 / 财务数据时，Tushare 必须首选。只有捕获 Tushare 异常（积分不足 / 超频）之后，才允许 fallback 到 `a-stock-data`，且**必须**在日志中记录降级事件。

### 5. JSON 模板转义
Prompt 模板中使用 `.format()` 时，JSON 示例的花括号 `{}` 必须转义为 `{{}}`，否则会被 format 解析器误认为占位符导致 `KeyError`。

---

## 开发工作流规范

每完成一个功能点、节点或 Bug 修复后，必须立即将代码推送到 GitHub，不得积压：

```bash
git add .
git commit -m "[模块名] 简短描述"
git push
```

**commit message 格式**：`[模块名] 简短描述`

| 示例 | 说明 |
|------|------|
| `[infrastructure] 初始化 PostgreSQL stock_basic 表` | 数据底座相关 |
| `[nodes/policy_parser] 完成政策 PDF 概念提取节点` | DAG 节点相关 |
| `[graphs/static_graph] 接入实体映射流程` | 流水线编排相关 |
| `[tools] indicator_calc 增加量比计算` | 计算工具相关 |
| `[E2E] 全业务链路测试(9/9) + DDL补丁` | 测试相关 |

推送使用 GitHub Personal Access Token（PAT）进行 HTTPS 鉴权，token 来源见下方"凭证与密钥管理"。

---

## 测试体系

| 层级 | 文件 | 用例数 | 耗时 | 特点 |
|------|------|--------|------|------|
| Phase 1-4 | `test_phase{1-4}.py` | 56 | ~2s | 单元测试，大量 Mock |
| E2E | `test_e2e.py` | 25 | ~35s | 集成测试，真实 API 调用 |
| Full Chain | `test_full_chain.py` | 9 | ~107s | 端到端 DAG 全链路执行 |
| **合计** | | **90** | | |

### Full Chain 测试覆盖

| 编号 | 覆盖链路 | 关键组件 |
|------|----------|----------|
| FC-1 | PDF → policy_parser → chain_splitter → entity_mapper → tech_ranker | 完整静态 DAG |
| FC-2 | 预设概念词 → SearXNG → LLM Pro → ChromaDB → PG → indicator_calc | 中间链路深度验证 |
| FC-3 | 新闻 → Flash粗筛 → Pro深读 → 三共振检查 → SOP学习 | 完整动态 DAG |
| FC-4 | sop_pending → LLM提取 → sop_active(approved=FALSE) → API查询 | SOP 全生命周期 |
| FC-5 | tech_ranker→Redis, 预警存储, LLM缓存共享 | 跨管道数据流 |

---

## 凭证与密钥管理

**唯一可信来源**：所有密钥、服务器信息、API Token，必须且只能从本机以下路径读取：

```
C:\Users\13979\Desktop\notes\apis.txt
```

该文件中包含（按 key 名称查找对应值）：

| 用途 | 文件中的 key 名称 |
|------|------------------|
| Tushare 数据接口 | `tushare token` |
| DeepSeek 大模型 | `deepseek api` |
| GitHub 推送鉴权 | `github token` |
| 阿里云服务器登录 | `阿里云服务器公网IP` / 用户名 / 密码 |
| 其他第三方接口 | 参见文件对应行 |

**禁止硬编码**：任何密钥不得直接写入代码文件或 `config/settings.py`。正确做法是运行时读取 `apis.txt` 后注入为环境变量，或通过 `python-dotenv` 加载 `.env` 文件（`.env` 由启动脚本从 `apis.txt` 自动生成，不提交到 Git）。

---

## 资源存放规则

**唯一存放路径**：所有项目所需的外部依赖和资源，下载后**必须且只能**存放在以下路径：

```
D:\work\ai\project-one\resources\
```

| 资源类型 | 存放路径 |
|---------|----------|
| 本地 Embedding 模型（bge-small-zh-v1.5） | `resources/models/` |
| 政策 PDF 文件 | `resources/policies/` |
| defuddle 微服务及 node_modules | `resources/defuddle-service/` |
| 其他第三方资源或本地缓存 | `resources/` 相应子目录 |

**路径引用规则**：代码中所有资源路径必须通过 `config/settings.py` 中的 `resources_base` 配置项读取，禁止硬编码绝对路径。`resources/` 目录加入 `.gitignore`，不提交到 Git。
