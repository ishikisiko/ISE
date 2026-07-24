## MODIFIED Requirements

### Requirement: Default pipeline SHALL retrieve evidence through the unified EvidenceSource layer
系统 SHALL 在默认主链路中根据 `QueryPlan` 通过统一来源层收集网页、领域和本地证据，并将每个结果归一化为可进入证据账本的 `EvidenceItem`。

#### Scenario: Planned search uses enabled evidence sources
- **WHEN** `QueryPlan` 允许联网搜索且包含可执行证据步骤
- **THEN** 默认主链路 SHALL 从计划允许的 EvidenceSource 收集证据并归一化为 `EvidenceItem`
- **AND** 每个证据项 SHALL 关联产生它的计划步骤和执行 trace

#### Scenario: Local-only query uses the same fusion pipeline with fewer source types
- **WHEN** 查询在 `allow_search=false` 条件下执行
- **THEN** 默认主链路 SHALL 继续经过统一 evidence retrieval / fusion 流程
- **AND** 唯一差异 SHALL 是 `QueryPlan` 启用的来源集合减少，而不是切换为另一条独立主执行链

### Requirement: Unified evidence fusion SHALL support shared filtering, deduplication, and ranking semantics
系统 SHALL 对归一化后的 `EvidenceItem` 集合按照计划的证据策略执行过滤、URL 去重、排序、覆盖标记和最终保留上限。

#### Scenario: Evidence is evaluated against the plan
- **WHEN** 查询返回网页、本地或领域证据
- **THEN** 系统 SHALL 依据来源层级、比较/时间覆盖和其他计划策略处理统一证据集合
- **AND** 回答上下文 SHALL 基于处理后的证据账本生成

#### Scenario: Reranker is unavailable
- **WHEN** reranker 未配置、被禁用或执行失败
- **THEN** 系统 SHALL 仍执行规范化 URL 去重和最终证据/引用上限
- **AND** reranker 状态 SHALL NOT 改变证据策略的接受标准

#### Scenario: Temporal or specialized search behavior is planned
- **WHEN** `QueryPlan` 包含时间覆盖或其他专门证据策略
- **THEN** 系统 SHALL 执行对应的受限计划步骤并进行统一后处理
- **AND** 系统 SHALL NOT 从原始关键词隐式发起该专门行为

## ADDED Requirements

### Requirement: Evidence ledger SHALL report constraint coverage and retention decisions
系统 SHALL 为每个最终或被拒绝的证据记录其覆盖的计划约束、来源层级以及保留、合并或拒绝原因。

#### Scenario: Same reference appears in multiple steps
- **WHEN** 多个计划步骤返回同一规范化 URL 或等价引用
- **THEN** 系统 SHALL 将其合并为一个证据账本项
- **AND** 系统 SHALL 保留该项覆盖的多个步骤和合并原因

#### Scenario: Evidence does not satisfy policy
- **WHEN** 证据与计划相关但不满足来源权威性、时间覆盖或其他接受策略
- **THEN** 系统 SHALL 在账本中标记其受限或被拒绝状态
- **AND** 验证阶段 SHALL 不将其计为满足该约束的证据
