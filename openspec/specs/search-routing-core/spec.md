# search-routing-core Specification

> **Status:** superseded by roadmap M3 — 该能力将在对应里程碑删除，不要新增 requirement 或加固；仅接受阻断性缺陷的最小修复。 分类依据见 `docs/agentic_loop_roadmap.md`。

## Purpose
Define the shared routing core used by the default search pipeline across CLI and API entrypoints.

## Requirements
### Requirement: System SHALL use a unified routing core for default search handling
系统 SHALL 为默认搜索主链路提供统一的查询路由核心，用于处理 small talk 判定、时间约束解析、领域分类、搜索决策和关键词生成。

#### Scenario: Default CLI query uses shared routing core
- **WHEN** CLI 通过默认主编排器处理查询
- **THEN** 系统 SHALL 使用统一路由核心完成查询判定与搜索前决策
- **AND** 系统 SHALL NOT 依赖一套与 Web 端分离的 legacy 路由实现

#### Scenario: Default API query uses shared routing core
- **WHEN** Web API 通过默认主编排器处理查询
- **THEN** 系统 SHALL 使用与 CLI 默认主链路相同的统一路由核心
- **AND** small talk、direct answer、domain routing 和 search routing 的语义 SHALL 保持一致

### Requirement: Routing core SHALL produce deterministic routing outputs
统一路由核心 SHALL 输出结构化且可复用的 `QueryAnalysis` 和 `QueryPlan` 或等价结果，至少包括是否需要搜索、领域执行提示、实体和约束摘要、证据策略、可执行步骤以及关键词或等价查询信息。关键词生成失败与后备使用情况 SHALL 在工作流 trace 的步骤详情中可见，且确定性后备查询 SHALL 保留查询的意图线索而非仅拼接实体。

#### Scenario: Query does not require search
- **WHEN** 路由核心判定查询无需外部搜索
- **THEN** 系统 SHALL 返回结构化决策结果，明确 `needs_search=false`
- **AND** 该结果 SHALL 可直接驱动 direct answer 或 local-only 路径

#### Scenario: Query requires search
- **WHEN** 路由核心判定查询需要外部搜索
- **THEN** 系统 SHALL 返回结构化决策结果，明确 `needs_search=true`
- **AND** 系统 SHALL 提供可供计划执行器使用的关键词、查询步骤或等价搜索计划

#### Scenario: Keyword generation cannot produce usable terms
- **WHEN** 关键词生成发生模板、调用或解析错误，或返回空结果
- **THEN** 系统 SHALL 在分析或 trace 中记录失败，且工作流 trace 的关键词步骤 SHALL 显示使用了后备及错误摘要
- **AND** 系统 SHALL 依据 `QueryPlan` 使用确定性后备查询而不是静默退化为不受约束的原始检索

#### Scenario: Deterministic fallback query preserves intent cues
- **WHEN** 关键词生成失败后使用确定性后备查询
- **THEN** 后备查询 SHALL 在实体之外保留分析阶段识别的意图线索（如价格类查询附加 pricing/价格 词、最新类查询附加 latest 词）
- **AND** 系统 SHALL NOT 仅以实体名称拼接作为后备查询

### Requirement: Routing core SHALL preserve constraints across downstream stages
系统 SHALL 让后续来源选择、检索、融合和 post-check 消费同一分析约束，而不是在各阶段重复使用独立关键词逻辑。

#### Scenario: Comparison constraint reaches verification
- **WHEN** 路由核心识别到比较成员
- **THEN** 计划 SHALL 将比较覆盖要求传递给证据融合和验证阶段
- **AND** 最终结果 SHALL 能说明成员覆盖或缺失状态

#### Scenario: Temporal constraint reaches retrieval
- **WHEN** 路由核心识别到明确的时间约束
- **THEN** 计划 SHALL 将该约束传递给检索和时间覆盖策略
- **AND** 非时间查询 SHALL NOT 获得时间扩展步骤
