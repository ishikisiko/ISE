# evidence-policy-routing Specification

## Purpose
TBD - created by archiving change improve-query-execution-orchestration. Update Purpose after archive.

## Requirements
### Requirement: System SHALL derive evidence policies from query constraints and claim classes
系统 SHALL 根据 `QueryAnalysis` 选择可组合的证据策略，而不是仅根据单一领域标签或关键词决定检索行为。

#### Scenario: Numeric or current claim requires authority
- **WHEN** 计划包含当前、数值、价格、合规或其他需要权威性的声明
- **THEN** 系统 SHALL 应用来源权威性策略
- **AND** 该策略 SHALL 定义可接受的来源层级和未满足时的结果状态

#### Scenario: Comparison requires membership coverage
- **WHEN** 计划包含两个或更多比较成员
- **THEN** 系统 SHALL 应用比较覆盖策略
- **AND** 验证阶段 SHALL 能识别每个成员是否被证据支持、证据不足或需要澄清

#### Scenario: Historical coverage is explicitly requested
- **WHEN** 计划包含明确时间范围、历史或趋势约束
- **THEN** 系统 SHALL 应用时间覆盖策略并定义所需时间范围
- **AND** 该策略 SHALL 可以请求受预算限制的补充检索

### Requirement: Generic lexical cues SHALL NOT select specialized evidence policies alone
系统 SHALL 要求与策略相匹配的结构化约束，且不得仅凭通用词语触发专门的时间、权威或比较检索行为。

#### Scenario: Bare comparison text has no temporal constraint
- **WHEN** 查询只包含 `对比`、`比较`、`compare` 或 `comparison` 等比较词
- **THEN** 系统 SHALL 创建比较覆盖约束而不创建时间覆盖约束
- **AND** 系统 SHALL NOT 触发按年份的补充检索

#### Scenario: Explicit temporal comparison selects both policies
- **WHEN** 查询同时包含比较成员和明确的跨年份、历史或趋势约束
- **THEN** 系统 SHALL 同时应用比较覆盖和时间覆盖策略
- **AND** 计划 SHALL 记录它们各自的证据目标与预算

### Requirement: Evidence policy SHALL classify source acceptance independently of provider availability
系统 SHALL 将“可调用 provider”与“可接受为某项声明证据的来源层级”分开判断。

#### Scenario: Available source does not meet claim authority
- **WHEN** provider 返回可用网页结果但其来源层级不满足计划的权威策略
- **THEN** 系统 SHALL 将该结果作为受限上下文或拒绝证据处理
- **AND** 系统 SHALL NOT 将其单独作为已验证声明的支持依据