## MODIFIED Requirements

### Requirement: Unified execution layer SHALL preserve required specialized search behavior
统一主 RAG 执行层 SHALL 通过 `QueryPlan` 和证据策略保留所需的专门检索能力，并 SHALL 将专门行为作为受预算限制、可追溯的计划步骤执行。

#### Scenario: Valid temporal coverage requires extra evidence gathering
- **WHEN** 计划的时间覆盖策略识别出明确时间范围内的证据缺口
- **THEN** 统一主执行层 SHALL 执行受预算限制的补充历史检索步骤
- **AND** 每个步骤 SHALL 写入执行 trace 和证据账本

#### Scenario: Generic comparison has no temporal coverage constraint
- **WHEN** 计划仅包含比较覆盖而不包含时间覆盖
- **THEN** 统一主执行层 SHALL NOT 执行按年份或历史颗粒化检索
- **AND** 系统 SHALL 仅执行计划中声明的步骤

#### Scenario: Unified execution fails to search
- **WHEN** 外部搜索不可用但本地文档或 direct answer 仍可用
- **THEN** 系统 SHALL 使用统一主执行层定义的回退语义生成结果
- **AND** 返回结构 SHALL 明确记录搜索不可用而非 silently dropping the failure

## ADDED Requirements

### Requirement: Unified execution SHALL enforce plan budgets and replan only for declared gaps
系统 SHALL 在查询、结果、时间和恢复次数预算内执行计划，并仅在验证或证据账本声明可恢复缺口时追加步骤。

#### Scenario: Plan budget is exhausted
- **WHEN** 执行达到计划的查询、结果或恢复预算
- **THEN** 系统 SHALL 停止新增检索步骤
- **AND** 验证阶段 SHALL 返回证据不足或其他适当结果状态

#### Scenario: Recoverable evidence gap is found
- **WHEN** 证据账本显示存在受策略允许且可由一个受限步骤补齐的缺口
- **THEN** 系统 SHALL 创建并执行该恢复步骤
- **AND** 恢复步骤 SHALL 使用剩余计划预算并写入 trace
