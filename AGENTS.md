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
| 异步 HTTP | aiohttp (新闻源直连 API) |
| PDF 解析 | PyMuPDF (fitz) |
| 运行时 | Python 3.11+ |
| 基建部署 | Docker / docker-compose |

---

## 项目结构

```
a_stock_agent/
├── config/
│   ├── settings.py             # 全局配置（pydantic-settings，从 .env 读取）
│   └── prompts/                # Prompt 模板（10个 .txt 文件）
│       ├── policy_parser.txt   # 政策解读（两段式，--- 分隔）
│       ├── chain_splitter.txt  # 产业链拆解
│       ├── entity_mapper.txt   # 实体映射打分
│       ├── news_coarse.txt     # 新闻粗筛
│       ├── news_deep.txt       # 新闻深读
│       ├── sop_extractor.txt   # SOP 图谱提取
│       ├── concept_graph_extract.txt    # 政策文本→核心概念提取
│       ├── concept_graph_expand.txt     # BFS扩展子概念
│       ├── concept_graph_convergence.txt # 收敛判断
│       └── concept_graph_insert.txt     # 增量插入定位
├── src/
│   ├── infrastructure/         # 数据底座
│   │   ├── database.py         # PG/ChromaDB/Redis 初始化 + ChromaDB v0.x 兼容层
│   │   ├── data_fetcher.py     # 数据降级链（东财F10 → Tushare 两级降级）
│   │   ├── news_sources/       # 多源新闻采集包（直连 API，替代 RSSHub）
│   │   │   ├── base.py         # NewsItem + NewsSource ABC + NewsAggregator
│   │   │   ├── cls_telegraph.py # 财联社电报（5s 轮询）
│   │   │   ├── gov_policy.py   # 国务院政策（60s 轮询）
│   │   │   └── csrc_policy.py  # 证监会公告（60s 轮询）
│   │   ├── rss_fetcher.py      # 向后兼容层（re-export NewsItem + poll_rss）
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
│   │   ├── sop_learner.py      # SOP 自学习（pending → active, approved=FALSE）
│   │   └── concept_graph_builder.py # 政策概念图谱（BFS扩展+智能收敛+增量插入）
│   ├── graphs/
│   │   ├── static_graph.py     # 静态图谱 DAG（按需触发）
│   │   └── dynamic_graph.py    # 动态监控 DAG（APScheduler 定时轮询）
│   └── tools/
│       └── indicator_calc.py   # 技术指标硬计算（禁止 LLM 替代）
├── scripts/                    # 运维/部署/诊断脚本
│   └── view_output.py          # 系统输出查看器（交互式菜单）
├── tests/                      # 测试套件（105 个用例）
│   ├── conftest.py             # 共享 fixtures
│   ├── test_phase1.py          # Phase 1: 配置 + 指标 + 降级链 + DDL
│   ├── test_phase2.py          # Phase 2: DAG 节点 + 数据流格式
│   ├── test_phase3.py          # Phase 3: 动态监控 + 三共振
│   ├── test_phase4.py          # Phase 4: SOP + Prompt + LLM 工具
│   ├── test_e2e.py             # E2E 集成测试（25 个，含真实 LLM/API 调用）
│   ├── test_full_chain.py      # 全业务链路测试（9 个，端到端 DAG 执行）
│   └── test_concept_graph.py   # 概念图谱单元测试（15 个）
├── api.py                      # FastAPI 接口（SOP审核 + 数据浏览 + 后台任务）
├── static/
│   └── dashboard.html          # Web 数据仪表盘（SPA，7页面）
├── start.bat                   # 一键启动控制面板（双击运行）
├── main.py                     # 系统入口（5 种运行模式）
└── .env                        # 环境变量（不提交 Git）
```

---

## 一键启动

双击 `start.bat` 进入控制面板，提供交互式菜单：

```
  ╔═══════════════════════════════════════════════╗
  ║       A股宏观锚定投研智能体 - 控制面板        ║
  ╠═══════════════════════════════════════════════╣
  ║   [1] 一键启动（完整模式）                    ║
  ║       SSH隧道 + 动态监控 + API服务            ║
  ║   [2] 首次初始化                              ║
  ║       SSH隧道 + 建表 + 全A股入库              ║
  ║   [3] 语义知识库初始化                        ║
  ║   [4] 仅启动动态监控                          ║
  ║   [5] 构建静态图谱（需政策PDF）               ║
  ║   [6] 启动 SOP 审核平台（API Server）         ║
  ║   [7] 运行测试套件                            ║
  ║   [8] 查看系统输出                            ║
  ║   [9] 构建政策概念图谱                        ║
  ╚═══════════════════════════════════════════════╝
```

**首次使用流程**：选 [2] 初始化 → 选 [3] 语义初始化 → 选 [1] 一键启动

**一键启动（选项 1）会同时拉起**：
- SSH 隧道（新窗口，转发 PG/Redis/ChromaDB 端口）
- FastAPI SOP 审核平台（新窗口，`http://localhost:8088`）
- 动态监控流水线（当前窗口，Ctrl+C 停止）

---

## 常用命令

```bash
# 一键启动（推荐 Windows 用户双击 start.bat）
start.bat

# 初始化基础设施（建表 + 全 A 股入库）
python main.py --mode init

# 静态图谱构建（需提供政策 PDF）
python main.py --mode static --pdf <policy_pdf_path>

# 动态监控（APScheduler 定时轮询）
python main.py --mode dynamic

# 语义知识库初始化（主营业务向量化 → ChromaDB）
python main.py --mode semantic

# 政策概念图谱构建（自动下载十五五政策 → BFS扩展 → 智能收敛）
python main.py --mode concept_graph

# 政策概念图谱构建（指定政策文本）
python main.py --mode concept_graph --policy-text "政策文本内容或文件路径"

# 启动 SOP 审核 Web API
uvicorn api:app --host 0.0.0.0 --port 8088

# 查看系统输出数据
python scripts/view_output.py

# 运行全部测试（105 个用例）
pytest tests/ -v

# 仅运行 Phase 单元测试（56 个，~2s）
pytest tests/test_phase1.py tests/test_phase2.py tests/test_phase3.py tests/test_phase4.py -v

# 仅运行 E2E 集成测试（25 个，~35s，需真实 API）
pytest tests/test_e2e.py -v -s

# 仅运行全业务链路测试（9 个，~107s，含 LLM Pro 调用）
pytest tests/test_full_chain.py -v -s --tb=short

# 仅运行概念图谱单元测试（15 个，~2s）
pytest tests/test_concept_graph.py -v

# 建立 SSH 隧道连接远程基础设施
python scripts/start_ssh_tunnels.py
```

---

## 核心架构

### 双流水线设计

- **`static_graph`**（按需触发）：政策 PDF → policy_parser(V4-Pro) → chain_splitter(SearXNG+V4-Pro) → entity_mapper(ChromaDB+V4-Pro+PG) → tech_ranker(K线+indicator_calc)，输出 tier1/tier2 排名股池
- **`dynamic_graph`**（多源聚合 + 定时消费）：NewsAggregator(财联社5s+国务院60s+证监会60s) → Queue → news_funnel(Flash粗筛+Pro深读) → resonance_alert(三共振检查) → sop_learner(Flash提取→sop_active)，输出预警信号。消费间隔 30 秒。

### 双模型策略

| 场景 | 模型 | Model ID |
|------|------|----------|
| 高频粗筛、简单判断、SOP 提取 | DeepSeek V4-Flash | `deepseek-chat` |
| 深度推理、产业链拆解、精判打分 | DeepSeek V4-Pro | `deepseek-reasoner` |

DeepSeek API Base URL: `https://api.deepseek.com/v1`（OpenAI 兼容接口）

### 数据降级链

**主营业务文本（两级降级）**：
```
L1: 东财 F10 datacenter API（支持 SH/SZ/BJ 市场）
    ↓ 捕获异常（网络超时 / 返回 None）
L2: Tushare stock_company main_business → 兜底
```

**行情/财务数据**：
```
Tushare Pro（首选）
    ↓ 捕获异常（积分不足 / 超频）
a-stock-data 东财 API（补充，需 _EASTMONEY_HEADERS 请求头）
    ↓ 必须在日志中记录降级事件
```

### 新闻采集架构（直连 API，绕过 RSSHub）

| 数据源 | 轮询间隔 | API 端点 | 说明 |
|--------|---------|----------|------|
| 财联社电报 | **5 秒** | `cls.cn/api/cache?name=telegraph` | 无需鉴权，aiohttp 异步 |
| 国务院政策 | 60 秒 | `sousuo.www.gov.cn/search-gov/data` | JSON API |
| 证监会公告 | 60 秒 | `csrc.gov.cn/searchList/{channelId}` | JSON API |

各源独立 `asyncio.Task` 并行轮询，Redis ID 去重（前缀命名空间：`cls:`、`gov:`、`csrc:`），统一推入 `asyncio.Queue` 供消费端处理。

### ChromaDB 兼容层

ChromaDB 服务端为 v0.5.x（REST API），客户端可能为 v1.x。项目使用 `_ChromaDBCompat` + `_CollectionCompat` 兼容层直接通过 REST API 通信。

**关键约束**：所有 collection 操作（count/add/query/get）**必须使用 UUID** 而非 collection name，否则会返回 `400 InvalidUUID`。UUID 在 `get_or_create_collection()` 时从服务端响应中提取。

### Redis Key 规范

| Key 模式 | 用途 | TTL |
|----------|------|-----|
| `dedup:news:{article_id}` | 新闻去重（带源前缀：cls:/gov:/csrc:） | 7d |
| `dedup:url:{url_md5}` | URL 正文缓存 | 7d |
| `llm:{prompt_hash}` | LLM 响应缓存 | 24h |
| `rate:searxng:{minute_bucket}` | SearXNG 限流 | 2min |
| `weights:stock:{ts_code}` | 下一代权重 | 30d |
| `static:stock_pool` | 静态图谱结果 | 7d |
| `dynamic:alerts:{YYYYMMDD}` | 当日预警 | 3d |
| `dynamic:news_feed` | 最新消息面（Redis list，保留 500 条） | 永久 |
| `concept_graph:edges` | 概念图谱边（child→JSON{parents,relevance,created_at}） | 30d |
| `concept_graph:roots` | 概念图谱 Layer0 根节点集合 | 30d |
| `concept_graph:layer:{depth}` | 概念图谱每层概念名集合 | 30d |
| `concept_graph:build_progress` | 概念图谱构建进度（JSON） | 1h |
| `concept_graph:build_lock` | 概念图谱并发构建锁 | 5min |

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
获取主营业务文本时，东财 F10 为首选（支持 SH/SZ/BJ）。只有捕获异常后，才允许 fallback 到 Tushare `stock_company`，且**必须**在日志中记录降级事件（DEBUG 级别）。获取行情/财务数据时，Tushare 必须首选。只有捕获 Tushare 异常（积分不足 / 超频）之后，才允许 fallback 到 `a-stock-data`，且**必须**在日志中记录降级事件。

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
| Concept Graph | `test_concept_graph.py` | 15 | ~2s | 概念图谱单元测试 |
| **合计** | | **105** | | |

### Full Chain 测试覆盖

| 编号 | 覆盖链路 | 关键组件 |
|------|----------|----------|
| FC-1 | PDF → policy_parser → chain_splitter → entity_mapper → tech_ranker | 完整静态 DAG |
| FC-2 | 预设概念词 → SearXNG → LLM Pro → ChromaDB → PG → indicator_calc | 中间链路深度验证 |
| FC-3 | 新闻 → Flash粗筛 → Pro深读 → 三共振检查 → SOP学习 | 完整动态 DAG |
| FC-4 | sop_pending → LLM提取 → sop_active(approved=FALSE) → API查询 | SOP 全生命周期 |
| FC-5 | tech_ranker→Redis, 预警存储, LLM缓存共享 | 跨管道数据流 |

---

## 软件输出

系统运行后的输出数据分为 4 类，可通过 **SOP 审核平台 Web UI**、**输出查看器**、**命令行工具**三种方式查看。

### 查看方式

| 方式 | 入口 | 适用场景 |
|------|------|----------|
| Web 仪表盘 | `http://localhost:8088` | 系统概览/消息面/股票库/语义搜索/预警/股池/SOP管理 |
| 输出查看器 | `python scripts/view_output.py` | 交互式查看全部输出（直连 PG/Redis） |
| start.bat [8] | 双击 `start.bat` 选 8 | 菜单式快捷查看 |
| API 接口 | curl / 浏览器 | 程序化访问 |

### 1. 三共振预警信号

动态监控检测到消息面 + 资金面 + 量比三重共振时，输出预警。

| 存储 | Key / 表 | 说明 |
|------|----------|------|
| Redis | `dynamic:alerts:{YYYYMMDD}` | 当日预警列表（JSON，TTL 3天） |
| 控制台 | `🚨 [RESONANCE ALERT]` | 实时日志输出 |
| API | `GET /alerts/today` | JSON 格式返回 |

每条预警包含：`ts_code`、`news_title`、`news_score`、`capital_inflow_pct`、`volume_ratio`、`timestamp`

```bash
# 查看今日预警
python scripts/view_output.py alerts

# API 访问
curl http://localhost:8088/alerts/today
```

### 2. 静态图谱股池

静态图谱构建完成后，输出产业链相关的排名股池。

| 存储 | Key / 表 | 说明 |
|------|----------|------|
| Redis | `static:stock_pool` | tier1 + tier2 排名股池（JSON，TTL 7天） |
| 控制台 | `[Main] 静态图谱结果` | 实时日志输出 |

每只股票包含：`ts_code`、`name`、`score`、`is_ma_bullish`、`vol_ratio`、`reason`

```bash
# 查看股池
python scripts/view_output.py stockpool
```

### 3. SOP 战法（自学习输出）

动态监控从新闻中自动提取交易战法，经人工审核后生效。

| 存储 | 表 | 说明 |
|------|------|------|
| PostgreSQL | `sop_pending` | 待审核原始战法（status=pending） |
| PostgreSQL | `sop_active` | 已提取战法（approved=FALSE 待批准，TRUE 已生效） |
| API | `GET /sop/pending` | 待审核列表 |
| API | `GET /sop/active` | 已审核列表 |
| Web UI | `http://localhost:8088` | 可视化审核（通过/拒绝按钮） |

```bash
# 查看待审核
python scripts/view_output.py sop-pending

# 查看已审核
python scripts/view_output.py sop-active

# API 审核通过
curl -X POST http://localhost:8088/sop/approve/1 -H 'Content-Type: application/json' -d '{"approved_by":"admin"}'
```

### 4. 系统运行统计

查看基础设施中的数据量、缓存、状态概览。

```bash
# 查看系统统计
python scripts/view_output.py stats
```

输出示例：
```
  PostgreSQL:
    stock_basic:  5307 条 (活跃 5102, ST/退市 205)
    sop_pending:  3 条待审核
    sop_active:   5 条 (2 已批准, 3 待批准)

  ChromaDB:
    stock_business: 4823 条向量化记录

  Redis:
    LLM 缓存:      127 条
    今日预警:      2 条
    静态图谱结果:  有
```

### 控制台日志标识

| 日志前缀 | 含义 |
|----------|------|
| `[Main]` | 主进程输出 |
| `[StaticGraph]` | 静态 DAG 执行日志 |
| `[DynamicGraph]` | 动态 DAG 执行日志 |
| `[Aggregator]` | 多源新闻聚合器（启动源、新增条数） |
| `[NewsSource]` | 单个新闻源拉取失败警告 |
| `[Scheduler]` | APScheduler 定时任务触发 |
| `🚨 [RESONANCE ALERT]` | 三共振预警信号 |
| `[SOP]` | SOP 审核操作日志 |
| `[DB]` | 数据库初始化/连接日志 |
| `[Task]` | Web 仪表盘触发的后台任务 |

### Web API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Web 仪表盘首页 |
| `/api/stats` | GET | 系统统计（PG/ChromaDB/Redis） |
| `/api/stocks` | GET | 分页股票列表（支持搜索、排序） |
| `/api/semantic` | GET | ChromaDB 语义搜索 |
| `/api/stockpool` | GET | 静态图谱股池 |
| `/api/news` | GET | 最新消息面（Redis list） |
| `/api/run/{task}` | POST | 触发后台任务（init/semantic/static/dynamic/concept_graph） |
| `/api/tasks` | GET | 查看任务状态和实时日志 |
| `/api/concept-graph/build` | POST | 触发概念图谱全量构建 |
| `/api/concept-graph/progress` | GET | 查询概念图谱构建进度 |
| `/api/concept-graph/add-concept` | POST | 手动添加概念并展开子图 |
| `/api/concept-graph/tree` | GET | 获取概念图谱层级树结构 |
| `/alerts/today` | GET | 今日三共振预警 |
| `/sop/pending` | GET | 待审核 SOP |
| `/sop/active` | GET | 已审核 SOP |
| `/sop/approve/{id}` | POST | 审核通过 |
| `/sop/reject/{id}` | POST | 审核拒绝 |

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
