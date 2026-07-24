# search-routing-core Delta

## MODIFIED Requirements

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
