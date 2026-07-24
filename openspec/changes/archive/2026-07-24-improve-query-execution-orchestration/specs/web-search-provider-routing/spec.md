## ADDED Requirements

### Requirement: Web provider routing SHALL execute only plan-authorized provider attempts
系统 SHALL 根据 `QueryPlan` 的来源约束和用户显式 source 选择执行网页 provider，而不是让隐式特殊分支绕过这些约束。

#### Scenario: Plan authorizes priority search
- **WHEN** 计划允许默认通用网页搜索且用户未限制来源
- **THEN** 系统 SHALL 按既有确定性优先顺序执行 provider
- **AND** 每个实际尝试 SHALL 写入 `QueryExecutionTrace`

#### Scenario: Caller limits providers
- **WHEN** 调用方显式提供受支持的 `search_sources` 子集
- **THEN** 计划 SHALL 将该子集作为 provider 约束
- **AND** 未请求 provider SHALL NOT 被作为隐式 fallback 执行

### Requirement: Provider routing metadata SHALL report actual execution and fallback decisions
系统 SHALL 将 provider 库存和每个计划步骤的实际尝试分开暴露。

#### Scenario: Priority provider completes a step
- **WHEN** 优先 provider 返回满足步骤最低结果要求的结果
- **THEN** 响应和 trace SHALL 将其标记为该步骤实际执行者
- **AND** 未调用的 fallback provider SHALL 不被描述为本轮已执行

#### Scenario: Provider fallback is required
- **WHEN** provider 失败、限流或未满足步骤最低结果要求并触发 fallback
- **THEN** 系统 SHALL 记录失败原因和 fallback 决策
- **AND** 系统 SHALL 以实际执行顺序记录 fallback provider
