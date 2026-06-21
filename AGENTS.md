# AGENTS.md

This file provides guidance to Qoder (qoder.com) when working with code in this repository.

## 项目定位

基于固定 DAG 工作流与 LLM 智能节点的 A 股投研系统。以国家宏观政策为锚定，利用 RAG 和语义理解构建产业链知识图谱，结合高频新闻与资金面共振，输出买卖预警建议。

详细设计蓝图见：`spec_out/A股宏观锚定投研智能体 - 完整系统设计蓝图 v1.0.md`

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 工作流编排 | LangGraph |
| LLM 集成 | LangChain + DeepSeek API |
| 数值计算 | Pandas |
| 结构化存储 | PostgreSQL |
| 向量存储 | ChromaDB |
| 缓存/限流 | Redis |
| 运行时 | Python 3.11+ |
| 基建部署 | Docker / docker-compose |

---

## 项目结构（目标态）

```
a_stock_agent/
├── config/
│   ├── settings.py             # 全局配置（API Keys、DB URLs）
│   └── prompts/                # 所有 Prompt 模板
├── src/
│   ├── infrastructure/         # 数据底座
│   │   ├── database.py         # PostgreSQL 与 ChromaDB 初始化
│   │   ├── data_fetcher.py     # 数据降级链（Tushare -> a-stock-data）
│   │   ├── rss_fetcher.py      # RSSHub 财联社电报抓取
│   │   ├── searxng_search.py   # SearXNG 搜索封装
│   │   └── web_fetcher.py      # defuddle/Playwright 正文提取
│   ├── nodes/                  # DAG 智能节点
│   │   ├── policy_parser.py    # 步骤1: 政策解读与概念提取
│   │   ├── chain_splitter.py   # 步骤2: 产业链深度拆解
│   │   ├── entity_mapper.py    # 步骤3: 实体映射与去伪
│   │   ├── tech_ranker.py      # 步骤4: 技术面多因子打分
│   │   ├── news_funnel.py      # 步骤5: 新闻智能漏斗
│   │   ├── resonance_alert.py  # 步骤6-7: 三共振预警
│   │   └── sop_learner.py      # 方法论自学习抓取
│   ├── graphs/
│   │   ├── static_graph.py     # 静态图谱构建流水线（按需触发）
│   │   └── dynamic_graph.py    # 动态监控流水线（定时轮询）
│   └── tools/
│       └── indicator_calc.py   # 均线、量比等 Python 硬计算（禁止 LLM 替代）
├── data/                       # 本地持久化（政策 PDF、中间 JSON）
├── tests/
├── docker-compose.yml
└── main.py
```

---

## 常用命令

```bash
# 启动所有基础设施（PostgreSQL、ChromaDB、Redis、SearXNG、RSSHub）
docker-compose up -d

# 运行系统
python main.py

# 运行测试
pytest tests/

# 运行单个测试文件
pytest tests/<test_file>.py -v
```

---

## 核心架构

### 双流水线设计

- **`static_graph`**（按需触发）：政策 PDF 输入 → 概念提取 → 产业链拆解 → 实体映射 → 技术面排序，构建静态产业链图谱
- **`dynamic_graph`**（定时轮询）：每分钟拉取新闻 → 双模型漏斗粗筛/深读 → 每 10 分钟拉取资金面 → 三共振预警输出

### 双模型策略

| 场景 | 模型 | Model ID |
|------|------|----------|
| 高频粗筛、简单判断 | DeepSeek V4-Flash | `deepseek-chat` |
| 深度推理、产业链拆解、精判打分 | DeepSeek V4-Pro | `deepseek-reasoner` |

DeepSeek API Base URL: `https://api.deepseek.com/v1`（OpenAI 兼容接口）

### 数据降级链

```
Tushare Pro（首选）
    ↓ 捕获异常（积分不足 / 超频）
a-stock-data（补充）
    ↓ 必须在日志中记录降级事件
```

### 关键 Tushare 接口

| 接口 | 用途 |
|------|------|
| `pro.stock_basic` | 全 A 股基础信息 |
| `pro.daily` | 日线行情（计算技术指标） |
| `pro.top_list` / `pro.top_inst` | 龙虎榜数据 |
| `pro.moneyflow` | 资金流向 |
| `pro.fina_indicator` | 财务指标 |

### 外部服务接口

- **RSSHub 财联社电报**：`http://<your-server>/cls/telegraph`，每分钟拉取
- **SearXNG**：`http://<your-server>/search?q={query}&format=json`，需 Header `Authorization: Bearer <api-key>`

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
`sop_learner.py` 写库时，目标表**必须且只能**是 `sop_pending`。**严禁**自动写入 `sop_active`（生效表），所有新战法须经人工审核后才能激活。

### 4. 信源降级链顺序
获取行情 / 财务数据时，Tushare 必须首选。只有捕获 Tushare 异常（积分不足 / 超频）之后，才允许 fallback 到 `a-stock-data`，且**必须**在日志中记录降级事件。

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

推送使用 GitHub Personal Access Token（PAT）进行 HTTPS 鉴权，token 来源见下方"凭证与密钥管理"。

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
