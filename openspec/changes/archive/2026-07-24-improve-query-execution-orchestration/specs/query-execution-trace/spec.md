## ADDED Requirements

### Requirement: System SHALL record an ordered execution trace for every planned search turn
系统 SHALL 为每个需要证据的查询维护有序 `QueryExecutionTrace`，记录分析摘要、计划步骤、实际尝试、证据决策和验证结果。

#### Scenario: Plan step executes successfully
- **WHEN** 查询计划中的检索、领域 API 或本地证据步骤执行成功
- **THEN** trace SHALL 记录步骤 ID、用途、实际来源或 provider、耗时和结果数
- **AND** trace SHALL 可关联该步骤产生的证据账本条目

#### Scenario: Plan step fails or is skipped
- **WHEN** 计划步骤因错误、预算、策略拒绝或前置澄清而未完成
- **THEN** trace SHALL 记录状态和简明原因
- **AND** 后续步骤 SHALL 依据该记录决定是否允许恢复、跳过或结束

### Requirement: Execution trace SHALL distinguish capability inventory from actual work
系统 SHALL 在 trace 和响应元数据中区分已配置、用户请求、可选和实际执行的 provider 或来源。

#### Scenario: Priority provider satisfies a step
- **WHEN** 优先 provider 成功完成一个计划步骤且未使用 fallback
- **THEN** trace SHALL 仅将该 provider 标记为该步骤实际执行者
- **AND** 已配置但未调用的 provider SHALL 保留为库存信息而非执行事实

#### Scenario: Fallback executes
- **WHEN** 一个计划步骤因错误、限流或无结果使用 fallback
- **THEN** trace SHALL 记录失败尝试、fallback 原因和实际 fallback 执行者
- **AND** 后续调用 SHALL NOT 覆盖此前尝试的 timing 或状态

### Requirement: Persisted audit SHALL serialize a bounded safe projection of the execution trace
启用过程审计时，系统 SHALL 持久化 trace 的紧凑投影，包含计划和执行事实所需的 ID、计数、来源引用和结果状态。

#### Scenario: Trace is persisted under audit size limits
- **WHEN** trace 会使审计记录超过配置字节上限
- **THEN** 系统 SHALL 按确定性规则裁剪可选条目并标记截断
- **AND** 系统 SHALL 保留足以说明最终结果和主要执行路径的摘要

#### Scenario: Trace contains sensitive runtime data
- **WHEN** 执行上下文包含 API key、Authorization header、完整 prompt 或网页全文
- **THEN** trace 和审计投影 SHALL NOT 序列化这些字段
- **AND** 审计失败 SHALL NOT 影响用户回答
