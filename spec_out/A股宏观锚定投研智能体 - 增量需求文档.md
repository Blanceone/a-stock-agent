# A股宏观锚定投研智能体 — 增量需求文档 v2.4
| 文档属性 | 内容 |
|---------|------|
| **项目名称** | A股宏观锚定投研智能体 |
| **文档版本** | v2.4 (Incremental) |
| **基准版本** | 详细设计文档 v2.3 |
| **文档状态** | 待评审 |
| **更新日期** | 2026-07-02 |
---
## 1. 概述
本增量需求文档基于对 v2.3 版本系统架构的深度复盘，旨在解决系统在长期无人值守运行中可能面临的**磁盘资源管理**、**高频场景下的 Token 成本控制**以及**核心数据资产沉淀**三大问题。
本次更新不涉及业务逻辑的重大变更，侧重于基础设施的加固与运维能力的提升。
---
## 2. 需求模块详细说明
### 2.1 模块一：日志自动化运维
#### 2.1.1 需求背景
v2.3 版本引入了结构化 JSON 日志（`logs/app_{date}.log`），解决了监控数据的查询问题。然而，当前未配置日志文件的保留策略和压缩机制。在长期运行或调试模式下，日志文件可能无限增长，导致服务器磁盘空间耗尽，进而引发系统崩溃。
#### 2.1.2 需求目标
配置日志的生命周期管理策略，实现日志的自动切割、压缩与清理，确保磁盘空间占用处于可控范围。
#### 2.1.3 详细设计
*   **修改文件**：`main.py`
*   **修改函数**：`_setup_obs_logger()`
*   **配置参数**：
    *   **Rotation（切割）**：当单文件大小超过 `100 MB` 时自动切割。
    *   **Retention（保留）**：仅保留最近 `30 days` 的日志文件。
    *   **Compression（压缩）**：对过期的日志文件自动进行 `zip` 压缩，节省空间。
*   **实现逻辑**：
    利用 `loguru` 的内置参数进行配置，无需编写额外的清理脚本。
```python
# 示例配置
logger.add(
    "logs/app_{time:YYYY-MM-DD}.log",
    format="{message}",
    filter=lambda r: r["extra"].get("event_type"),
    level="INFO",
    rotation="100 MB",
    retention="30 days",
    compression="zip",
    enqueue=True,
    serialize=True
)
```
#### 2.1.4 验收标准
1.  系统运行后，当日志文件大小达到 100MB 时，自动创建新文件，旧文件自动压缩为 `.zip`。
2.  系统能自动识别并删除 30 天前的日志文件。
3.  `--mode stats` 命令仍能正确读取并统计未压缩日志中的数据。
---
### 2.2 模块二：概念图谱增量插入防抖
#### 2.2.1 需求背景
v2.3 版本中，新闻分析模块若发现新概念词（如“低空经济”），会自动触发概念图谱的增量插入流程。
**风险点**：若热门政策被多源（财联社、国务院、证监会）在短时间内高频重复报道，系统将针对同一个概念词重复触发 LLM 调用。这不仅浪费大量 Token 成本，还可能导致重复写入数据库。
#### 2.2.2 需求目标
引入“短期去重锁”机制，确保同一概念在指定时间窗口内仅被处理一次。
#### 2.2.3 详细设计
*   **修改文件**：`nodes/concept_graph_builder.py`
*   **修改函数**：`incremental_insert(self, concept: str)`
*   **Redis 锁设计**：
    *   **Key 格式**：`concept_graph:insert_lock:{concept_md5}`
    *   **Value**：`1`
    *   **TTL**：`3600` 秒（1小时）
*   **逻辑流程**：
    1.  计算概念词的 MD5 值。
    2.  尝试使用 `SET key value NX EX 3600` 命令设置 Redis 锁。
    3.  **若设置失败**（锁已存在），说明该概念正在处理或1小时内已处理，直接 `return` 跳过。
    4.  **若设置成功**，执行原有的 LLM 插入逻辑。
    5.  执行结束无需手动释放锁，依赖 TTL 自动过期（防止死锁）。
#### 2.2.4 验收标准
1.  单元测试模拟同一概念在 1 分钟内连续触发 5 次插入请求。
2.  检查日志和 LLM 调用记录，确认仅有第 1 次请求执行了完整流程，后续 4 次均命中 Redis 锁跳过。
3.  锁在 1 小时后自动失效，允许再次处理该概念（适用于该概念有新进展的场景）。
---
### 2.3 模块三：预警信号持久化
#### 2.3.1 需求背景
当前系统的三共振预警信号仅存储在 Redis 键 `dynamic:alerts:{date}` 中，TTL 设置为 7 天。
**局限性**：用户无法查询 7 天前的历史预警记录，无法进行长期的策略回测、胜率统计或复盘分析。Redis 仅适合热数据，不适合存储长期的历史档案。
#### 2.3.2 需求目标
将预警信号同步持久化至 PostgreSQL，构建可追溯的历史预警数据库，并提供查询接口。
#### 2.3.3 详细设计
*   **数据库层变更**：
    *   新建表 `resonance_alerts`：
        ```sql
        CREATE TABLE resonance_alerts (
            id SERIAL PRIMARY KEY,
            alert_time TIMESTAMP NOT NULL,
            ts_code VARCHAR(10) NOT NULL,
            name VARCHAR(20),
            concept VARCHAR(50),
            news_score FLOAT,
            capital_inflow_pct FLOAT,
            volume_ratio FLOAT,
            confidence FLOAT,
            reason TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX idx_alerts_time ON resonance_alerts(alert_time DESC);
        CREATE INDEX idx_alerts_code ON resonance_alerts(ts_code);
        ```
*   **业务逻辑变更**：
    *   修改文件：`nodes/resonance_alert.py` 或 `graphs/dynamic_graph.py`
    *   触发时机：在预警信号写入 Redis 成功后。
    *   操作：异步调用 `_pg_persist_alert()` 函数，将预警数据插入 `resonance_alerts` 表。
*   **API 层变更**：
    *   修改文件：`api.py`
    *   新增接口：`GET /api/alerts/history`
    *   参数：`start_date` (YYYY-MM-DD), `end_date` (YYYY-MM-DD), `page`, `size`
    *   返回：指定时间范围内的历史预警列表。
#### 2.3.4 验收标准
1.  触发一次三共振预警，PostgreSQL 数据库中能立即查询到对应记录。
2.  7 天后 Redis 数据过期，PostgreSQL 数据依然完整保留。
3.  调用 `/api/alerts/history` 接口，能正确分页返回历史数据，且响应时间 < 500ms。
---
## 3. 实施计划与优先级
| 模块 | 优先级 | 预估工时 | 前置依赖 | 风险评估 |
|------|--------|---------|---------|---------|
| **日志自动化运维** | P0 | 0.5 人天 | 无 | 低 |
| **概念图谱防抖** | P1 | 0.5 人天 | 无 | 低 |
| **预警信号持久化** | P1 | 1.0 人天 | PG DDL 变更 | 中（需处理数据一致性） |
**建议实施顺序**：
1.  **日志自动化运维**：立即执行，消除磁盘爆满隐患。
2.  **概念图谱防抖**：尽早实施，降低日常运行成本。
3.  **预警信号持久化**：最后实施，完善数据资产管理闭环。
