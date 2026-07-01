# A股宏观锚定投研智能体 - 增量需求文档
> **版本**：v2.3 | **状态**：待评审 | **基于版本**：详细设计文档 v2.2
>
> **概述**：本文档基于对 v2.2 版本系统的深度评审，针对前端可视化交互瓶颈、系统运行可观测性缺失以及核心知识库维护成本问题，提出三个关键模块的增量优化需求。
---
## 1. 前端可视化增强：概念图谱交互升级
### 1.1 背景与问题
当前 `dashboard.html` 采用原生 DOM 渲染概念图谱树结构。随着 BFS 扩展深度的增加，节点数量可能突破 200+，原生渲染在处理大量 DOM 节点时存在性能卡顿，且缺乏缩放、拖拽、点击展开等必备的交互体验，难以支撑用户对复杂产业链关系的探索。
### 1.2 目标
引入成熟的可视化库，构建高性能、交互流畅的概念图谱视图，支持用户直观地浏览产业层级及节点关联。
### 1.3 详细需求
| 需求项 | 详细描述 |
|--------|---------|
| **技术选型** | 引入 **Apache ECharts** (v5.x) 通过 CDN 方式集成。放弃原生 JS 渲染，优先使用 ECharts 的 `Tree` 图表组件。 |
| **数据适配** | 修改后端 `/api/concept-graph/tree` 接口返回格式，将当前的 Redis 层级数据结构转换为 ECharts Tree 标准数据格式（`name`, `children`, `value`, `symbolSize` 等）。 |
| **交互功能** | 1. **缩放与平移**：支持鼠标滚轮缩放、拖拽平移画布。<br>2. **折叠/展开**：点击节点可折叠/展开其子节点。<br>3. **悬停提示**：鼠标悬停显示该概念的 `category`（分类）、`stock_count`（成分股数量）、`policy_basis`（政策依据）。<br>4. **高亮关联**：点击某个概念节点时，右侧“概念列表”自动滚动并高亮该概念及其成分股。 |
| **视觉样式** | 区分层级颜色（Layer0 根节点为深红，Layer1 为橙色，Layer2 为蓝色），节点大小根据成分股数量或 `relevance` 动态调整。 |
### 1.4 验收标准
1. 能够流畅渲染包含 500+ 节点的图谱，无明显卡顿。
2. 支持上述所有交互功能，操作响应时间 < 200ms。
3. 页面无 JS 报错，适配 Chrome/Edge 最新版。
---
## 2. 系统可观测性建设：统一日志与监控模块
### 2.1 背景与问题
当前系统缺乏结构化的运行日志记录。对于 LLM 的 Token 消耗成本、LangGraph 节点的执行耗时、降级链的触发频率等关键指标，仅能依赖控制台输出查看，无法进行历史回溯和性能分析，不利于 Prompt 优化和成本控制。
### 2.2 目标
构建统一的结构化日志模块，对系统运行过程进行全链路记录，并支持关键指标的统计分析。
### 2.3 详细需求
#### 2.3.1 新增基础设施模块：`src/infrastructure/logger.py`
- **日志格式**：采用 JSON 格式，包含以下标准字段：
  ```json
  {
    "timestamp": "2026-06-30T10:00:00Z",
    "level": "INFO",
    "module": "news_funnel",
    "node": "coarse_filter",
    "trace_id": "uuid",
    "latency_ms": 150,
    "tokens_input": 200,
    "tokens_output": 50,
    "model": "deepseek-chat",
    "status": "success",
    "extra": {"fallback_used": false}
  }
  ```
#### 2.3.2 关键埋点要求
| 埋点位置 | 必须记录的字段 | 说明 |
|---------|---------------|------|
| **LLM 调用** (`llm_utils.py`) | `model`, `tokens_input`, `tokens_output`, `latency_ms`, `prompt_template_name` | 用于成本核算和 Prompt 效果分析 |
| **降级链触发** (`data_fetcher.py`, `web_fetcher.py`) | `data_type`, `failed_source`, `fallback_source`, `reason` | 记录何时发生降级，评估数据源稳定性 |
| **节点执行** (`graphs/*.py`) | `node_name`, `status` (success/failure), `error_msg` | 监控 DAG 流水线健康度 |
| **SearXNG 调用** | `query`, `result_count`, `latency_ms` | 监控外部搜索服务可用性 |
#### 2.3.3 日志存储与查询
- **存储**：日志文件按日期滚动存储在 `logs/` 目录下（如 `astock_2026-06-30.log`）。
- **CLI 工具**：在 `main.py` 新增命令 `python main.py --mode stats`，简单解析日志文件，输出昨日 Token 总消耗、各节点平均耗时、降级触发 TOP 3 数据源。
### 2.4 验收标准
1. 所有 LLM 调用均有结构化日志记录。
2. 能够通过 `--mode stats` 命令看到过去 24 小时的 Token 费用估算和降级统计。
3. 日志文件不包含敏感信息（如 API Key、完整 Prompt 内容若敏感可截断）。
---
## 3. 知识库增量更新机制：年报与向量维护
### 3.1 背景与问题
当前 `semantic_init.py` 为全量初始化模式。随着年报季更新，上市公司主营业务描述会发生变化。若每次数据更新都全量跑 5000 家公司，耗时约 3-4 小时且成本较高。系统缺乏高效的增量更新手段。
### 3.2 目标
实现基于时间戳的增量更新能力，仅对业务描述可能发生变更的股票进行重新向量化，大幅降低维护成本。
### 3.3 详细需求
#### 3.3.1 数据库扩展
- **PostgreSQL**：在 `stock_basic` 表中新增字段 `biz_text_updated_at` (TIMESTAMP)，记录最后一次主营业务文本的更新时间。
- **ChromaDB**：在 Metadata 中存储 `ts_code` 和 `updated_at`，支持查询。
#### 3.3.2 CLI 命令扩展
修改 `main.py --mode semantic` 行为：
- **全量模式（默认）**：`python main.py --mode semantic`（保持现有逻辑，全量覆盖）。
- **增量模式（新增）**：`python main.py --mode semantic --incremental`。
#### 3.3.3 增量更新逻辑
1. 获取当前时间 `T_now`。
2. 遍历 `stock_basic` 表，筛选条件：
   - `biz_text_updated_at` 为 NULL（从未初始化）
   - 或 `T_now - biz_text_updated_at > 90 days`（超过 90 天未更新，覆盖年报周期）
3. 对筛选出的股票列表执行“获取主营文本 -> 向量化 -> ChromaDB UPSERT”流程。
4. 更新 PostgreSQL 中的 `biz_text_updated_at = T_now`。
5. 输出增量更新日志（更新数量、耗时）。
#### 3.3.4 年报强更机制（可选）
在 `config/settings.py` 增加配置 `FORCE_UPDATE_STOCKS`（列表格式）。如果用户明确知道某只股票刚发年报，可将其代码加入此列表，下次 `--incremental` 运行时强制更新，无视 90 天限制。
### 3.4 验收标准
1. 执行 `--incremental` 后，仅更新超过 90 天或新增的股票。
2. 更新完成后，ChromaDB 中对应股票的向量数据和 Metadata 中的 `updated_at` 均为最新。
3. 增量更新耗时相比全量更新减少 90% 以上。
---
## 4. 优先级与排期建议
| 模块 | 优先级 | 预估工时 | 依赖 |
|------|--------|---------|------|
| **1. 前端可视化增强** | P0 (高) | 1.5 人天 | 无（可并行） |
| **2. 系统可观测性建设** | P1 (中) | 2 人天 | 无（可并行） |
| **3. 知识库增量更新** | P1 (中) | 1 人天 | 需先完成 DB Schema 扩展 |
**建议开发顺序**：
1. 先完成 **模块 1**，提升用户体验和系统直观性。
2. 其次完成 **模块 2**，为后续的 Prompt 调优和系统排错提供数据支持。
3. 最后完成 **模块 3**，作为长期运维的保障手段。
