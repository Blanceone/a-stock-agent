这份开发 Spec 是我们系统落地的第一版工程蓝图。考虑到你新增了 **Tushare Token**，这极大地增强了我们获取高质量结构化财务数据和行情数据的稳定性。我已将 Tushare 无缝融入数据获取降级链中。
以下是完整的详细开发文档。
---
# A股宏观锚定投研智能体 - 开发详细 Spec 文档 v1.0
## 一、 项目总览
### 1.1 项目定位
基于固定 DAG 工作流与 LLM 智能节点的双引擎投研系统。以国家宏观政策为锚定，利用 RAG 和语义理解构建产业链知识图谱，结合高频新闻与资金面共振，输出高胜率的买卖预警建议。
### 1.2 核心技术栈
- **大模型**：DeepSeek V4-Flash (高频/简单任务)、DeepSeek V4-Pro (深度推理/拆解任务)
- **数据源**：Tushare Pro（主结构化数据）、a-stock-data（冗余/补充）、自建 RSSHub（财联社电报）、自建 SearXNG（通用语义搜索）
- **基础设施**：PostgreSQL（结构化库）、ChromaDB（向量库）、Redis（缓存/限流）、Docker（基建部署）
- **开发框架**：Python 3.11+、LangGraph（工作流编排）、LangChain（LLM与RAG集成）、Pandas（数值计算）
---
## 二、 项目结构
```text
a_stock_agent/
├── config/                     # 配置文件
│   ├── settings.py             # 全局配置 (API Keys, DB URLs)
│   └── prompts/                # 所有 Prompt 模板
├── src/
│   ├── infrastructure/         # 基建与数据底座
│   │   ├── database.py         # PostgreSQL 与 ChromaDB 初始化
│   │   ├── data_fetcher.py     # 数据降级链 (Tushare -> a-stock-data)
│   │   ├── rss_fetcher.py      # RSSHub 财联社电报抓取
│   │   ├── searxng_search.py   # SearXNG 搜索工具封装
│   │   └── web_fetcher.py      # defuddle/Playwright 正文提取
│   ├── nodes/                  # DAG 工作流中的智能节点
│   │   ├── policy_parser.py    # 步骤1: 政策解读与概念提取
│   │   ├── chain_splitter.py   # 步骤2: 产业链深度拆解
│   │   ├── entity_mapper.py    # 步骤3: 实体映射与去伪
│   │   ├── tech_ranker.py      # 步骤4: 技术面多因子打分
│   │   ├── news_funnel.py      # 步骤5: 新闻智能漏斗分析
│   │   ├── resonance_alert.py  # 步骤6-7: 三共振预警
│   │   └── sop_learner.py      # 方法论自学习抓取
│   ├── graphs/                 # DAG 流程编排
│   │   ├── static_graph.py     # 静态图谱构建流水线
│   │   └── dynamic_graph.py    # 动态监控流水线
│   └── tools/                  # LLM 可调用的计算工具
│       └── indicator_calc.py   # 均线、量比等 Python 硬计算
├── data/                       # 本地持久化数据 (政策PDF, 中间JSON)
├── tests/                      # 单元测试与集成测试
├── docker-compose.yml          # 部署 SearXNG, RSSHub, Redis, DB
└── main.py                     # 系统入口
```
---
## 三、 数据接口设计
### 3.1 结构化数据层
- **Tushare Pro API** (主力)：
  - `pro.stock_basic`: 获取全A股基础信息。
  - `pro.daily`: 获取日线行情（计算技术指标）。
  - `pro.top_list` / `top_inst`: 获取龙虎榜数据。
  - `pro.moneyflow`: 获取资金流向。
  - `pro.fina_indicator`: 获取财务指标。
- **a-stock-data** (补充)：用于 Tushare 积分不够或需要东财特定资讯的接口。
### 3.2 新闻与资讯层
- **自建 RSSHub**：
  - 接口：`http://<your-server>/cls/telegraph`
  - 频率：每 1 分钟拉取一次。
- **自建 SearXNG**：
  - 接口：`http://<your-server>/search?q={query}&format=json`
  - 鉴权：HTTP Header `Authorization: Bearer <api-key>`
### 3.3 大模型接口
- **DeepSeek API** (OpenAI 兼容接口)：
  - Base URL: `https://api.deepseek.com/v1`
  - 模型 ID: `deepseek-chat` (V4-Flash), `deepseek-reasoner` (V4-Pro 或其最新映射)
---
## 四、 详细开发阶段与验收标准
### Phase 1: 基础设施搭建与数据底座 (预计 3-4 天)
#### Step 1.1: 部署与基建
- **该做什么**：使用 `docker-compose.yml` 在公网服务器部署 SearXNG（配置 Redis 缓存、Nginx 反代、API Key、国内搜索引擎）、RSSHub。本地部署 PostgreSQL 和 ChromaDB。
- **做到什么程度**：容器全部健康运行，SearXNG 返回 JSON 格式且必须带鉴权，RSSHub 能正常返回财联社电报。
- **验收标准**：
  1. `curl -H "Authorization: Bearer xxx" http://server/search?q=商业航天&format=json` 返回百度/必应结果。
  2. 请求 RSSHub 财联社路由返回最新 10 条电报 JSON。
#### Step 1.2: 数据库初始化与全 A 股结构化库录入
- **该做什么**：在 PostgreSQL 中创建表 `stock_basic` (代码, 名称, 申万行业, 流通市值, 是否ST)。通过 Tushare 拉取全量数据入库。
- **做到什么程度**：能通过 SQL 高效过滤 ST、退市股。
- **验收标准**：
  1. 数据库中包含 5000+ 条有效记录。
  2. 测试查询：`SELECT * FROM stock_basic WHERE name NOT LIKE '%ST%' AND circ_mv < 2000000` 在 50ms 内返回。
#### Step 1.3: 全 A 股语义知识库初始化 (系统护城河)
- **该做什么**：遍历 PostgreSQL 中的所有股票，通过 Tushare 获取其最新年报的“主营业务构成”和“核心竞争力”。使用 DeepSeek V4-Flash 提取摘要，向量化存入 ChromaDB。
- **做到什么程度**：构建完整的本地向量检索库，支持基于语义召回相关公司。
- **验收标准**：
  1. 完成 5000 家公司向量数据写入 ChromaDB。
  2. 测试检索：用 "AI芯片设计、GPU算力" 作为 Query，召回 Top 5 结果中必须包含“海光信息”、“寒武纪”。
---
### Phase 2: 静态产业链图谱构建引擎 (预计 4-5 天)
#### Step 2.1: 政策解读与基准概念提取
- **该做什么**：输入政策 PDF -> 解析目录大纲 -> V4-Pro 读取大纲圈定重点章节 -> 重点章节向量化 -> 提取产业名词输出 JSON。
- **做到什么程度**：能过滤掉“高质量发展”等宏观词汇，保留“低空经济”等可炒作产业名词。
- **验收标准**：
  1. 输入一份发改委文件，成功输出 `[{concept: "低空经济", source_section: "第三章第二节"}]` 格式数据。
#### Step 2.2: 产业链深度拆解
- **该做什么**：对于提取的基准概念，调用 SearXNG 搜索“{概念} 产业链 研报” -> defuddle 抓取正文 -> V4-Pro 结合研报内容，按照层级拆解节点。
- **做到什么程度**：必须基于真实研报拆解，输出树状 JSON。
- **验收标准**：
  1. 输入 "具身智能"，输出包含 "基础底座层 -> AI芯片设计"、"应用层 -> 人形机器人" 的多层 JSON 结构。
#### Step 2.3: 实体映射与去伪存真
- **该做什么**：拿产业链节点描述去 ChromaDB 语义召回 Top 30 候选股 -> V4-Pro 精判业务相关性打分 -> Python 查 SQL 库剔除 ST 和超大盘股。
- **做到什么程度**：剔除蹭概念的公司，保留业务纯正标的。
- **验收标准**：
  1. 输出列表中不再包含主营业务完全无关的公司。
  2. 剔除带有 ST 标记的股票。
#### Step 2.4: 技术面分析与多因子排序
- **该做什么**：调用 Tushare `pro.daily` 获取标的近期 K 线 -> Pandas 计算 5/10/20 日均线、量比、近期涨幅 -> 结合流通市值打分排序。
- **做到什么程度**：严禁 LLM 算数学题，全部由 Pandas 完成，输出梯队。
- **验收标准**：
  1. 输出 `第一梯队 (形态多头+盘子小+涨幅居前)` 和 `第二梯队` 列表。
---
### Phase 3: 动态市场监控与预警引擎 (预计 4-5 天)
#### Step 3.1: 高频新闻智能漏斗
- **该做什么**：建立定时任务（每分钟拉取 RSSHub）。将新闻 Title+Summary 批量送 V4-Flash 做一级粗筛（判断是否涉及产业投资） -> 若涉及，送 V4-Pro 做二级深读（判断影响概念、利好/利空）。
- **做到什么程度**：双模型漏斗机制，大幅降低成本，精准定位新闻影响节点。
- **验收标准**：
  1. 财联社推送 100 条电报，一级粗筛干掉至少 60% 无关内容。
  2. 二级深读能准确识别某条固态电池新闻利好“固态电池-电解质”节点。
#### Step 3.2: 资金面监控与三共振预警 (核心输出)
- **该做什么**：盘中每 10 分钟拉取 Tushare 龙虎榜和板块资金流。结合步骤 3.1 的新闻面影响，以及股票池的技术面状态。
- **做到什么程度**：实现“消息突发利好 + 游资介入 + 放量突破”的共振判断，输出预警。
- **验收标准**：
  1. 模拟注入一条“利好消息”和一条“游资买入”数据，系统在下一个盯盘周期输出红色预警文本：`【概念X】消息、资金、技术三重共振，重点关注【股票A】`。
---
### Phase 4: 自学习与方法论知识库 (预计 3 天)
#### Step 4.1: 游资 SOP 自动抓取与沉淀
- **该做什么**：编写脚本，利用 SearXNG 搜索“游资复盘 打板战法”等关键词 -> defuddle 抓正文 -> V4-Pro 提取结构化方法论 `{观点, 条件, 适用场景, 信源, 冲突检测}` -> 写入 PostgreSQL 的 `sop_pending` 表。
- **做到什么程度**：系统具备自学习雏形，但所有数据进入待审区。
- **验收标准**：
  1. 脚本运行后，`sop_pending` 表新增 10+ 条结构化战法。
  2. 如果新战法与库中已有战法冲突，`conflicts_with` 字段必须标出对应的战法 ID。
---
## 五、 开发纪律与红线 (Code Review 必查项)
1. **数值计算隔离红线**：任何技术指标（均线、量比等）的计算，必须写在 `src/tools/indicator_calc.py` 中，禁止让 LLM 生成计算代码或直接推理数值。
2. **SearXNG 频率红线**：禁止在 `dynamic_graph` 的高频新闻轮询节点调用 SearXNG。SearXNG 仅限在 `static_graph` 的步骤 2.2（产业链拆解）和 Phase 4（SOP 学习）中低频使用。
3. **SOP 人工闸门红线**：`sop_learner.py` 写库时，目标表必须且只能是 `sop_pending`。绝不允许自动写入 `sop_active`（生效表）。
4. **信源降级链红线**：获取行情/财务数据，首选必须是 Tushare，捕获 Tushare 异常（积分不足/超频）后，才 fallback 到 `a-stock-data` 或其他源，必须在日志中记录降级事件。
准备好进入代码编写了吗？我们可以先从 `docker-compose.yml` 和 Phase 1 的 Step 1.2（Tushare 数据库初始化）开始。
