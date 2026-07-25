# query-plan-orchestration Delta

## ADDED Requirements

### Requirement: System SHALL support query reformulation recovery steps
计划词汇表 SHALL 包含 `QUERY_REFORMULATION`（recovery_only）步骤类型。当验证识别出可由改写查询补齐的缺口（如 authority 未满足、比较成员缺失、首次检索词质量不足）且恢复预算允许时，计划 SHALL 追加该步骤；步骤的改写查询 SHALL 由缺失约束驱动生成，而不是简单重复首次检索词。

#### Scenario: Authority gap adds a reformulation step
- **WHEN** 验证产出缺失约束包含 `authority` 的可恢复缺口
- **THEN** 计划 SHALL 追加 `QUERY_REFORMULATION` 恢复步骤
- **AND** 该步骤的查询 SHALL 偏向比较成员或查询实体的官方来源表达（如 `<entity> official pricing`）

#### Scenario: Comparison member gap adds per-member queries
- **WHEN** 验证产出缺失约束包含未被证据覆盖的比较成员
- **THEN** 改写查询 SHALL 针对缺失成员单独构造
- **AND** 系统 SHALL NOT 用首次检索词原样重试

#### Scenario: Recovery steps remain budget-bounded
- **WHEN** 恢复预算或查询预算已用尽
- **THEN** 系统 SHALL NOT 追加或执行新的改写恢复步骤
- **AND** 该决策 SHALL 写入执行 trace

### Requirement: Verification SHALL mark non-temporal evidence gaps recoverable when recovery is possible
验证 SHALL 将可恢复性判定推广到 temporal 之外的缺口类型：只要计划允许追加恢复步骤且恢复预算未用尽，`no_evidence`、authority 未满足、比较成员缺失等缺口 SHALL 产出 `RECOVERABLE_GAP` 与 `next_action="recover"`，而不是仅 temporal 缺口才可恢复。

#### Scenario: No retained evidence on a non-temporal query
- **WHEN** 非时间查询的首轮检索后账本无任何 retained 证据
- **AND** 恢复预算未用尽
- **THEN** 验证 SHALL 返回 `RECOVERABLE_GAP` 与 `next_action="recover"`
- **AND** 系统 SHALL NOT 因计划缺少 temporal 恢复步骤而直接判定不可恢复

#### Scenario: Unrecoverable gaps stay final
- **WHEN** 缺口由关键歧义、搜索不可用或预算耗尽造成
- **THEN** 验证 SHALL 返回不可恢复的终态（澄清或证据不足）
- **AND** 系统 SHALL NOT 启动改写恢复循环

### Requirement: System SHALL consume recover actions with a bounded recovery loop
默认编排器 SHALL 提供消费 `next_action="recover"` 的执行路径：依据验证缺失约束生成改写查询，在查询、恢复与时间预算内重新执行 web 检索，将新证据并入同一证据账本并重新验证；循环 SHALL 以恢复预算为上界，且每次迭代 SHALL 写入执行 trace。

#### Scenario: Recovery loop re-searches with a reformulated query
- **WHEN** 验证返回 `next_action="recover"` 且恢复预算允许
- **THEN** 编排器 SHALL 生成改写查询并重新执行受预算约束的 web 检索
- **AND** 新证据 SHALL 并入原证据账本后重新验证

#### Scenario: Recovery loop terminates deterministically
- **WHEN** 恢复预算耗尽、时间预算耗尽或重新验证不再产出可恢复缺口
- **THEN** 循环 SHALL 终止并进入正常终态（回答、带限定回答、澄清或证据不足）
- **AND** 最终结果 SHALL 记录恢复执行事实（尝试次数、改写查询、状态）
