## MODIFIED Requirements

### Requirement: Routing core SHALL produce deterministic routing outputs
统一路由核心 SHALL 输出结构化且可复用的 `QueryAnalysis` 和 `QueryPlan` 或等价结果，至少包括是否需要搜索、领域执行提示、实体和约束摘要、证据策略、可执行步骤以及关键词或等价查询信息。

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
- **THEN** 系统 SHALL 在分析或 trace 中记录失败
- **AND** 系统 SHALL 依据 `QueryPlan` 使用确定性后备查询而不是静默退化为不受约束的原始检索

## ADDED Requirements

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
