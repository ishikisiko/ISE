# unified-rag-execution Delta

## ADDED Requirements

### Requirement: Local index build SHALL be skipped when no indexable documents exist
统一主执行层 SHALL 在本地文档快照为空（目录不存在或无可索引文件）时跳过嵌入模型加载与索引构建，而不是为空目录付出索引初始化成本。

#### Scenario: Empty uploads directory
- **WHEN** 本地文档目录存在但没有任何可索引文件
- **THEN** 系统 SHALL 跳过嵌入模型加载与索引构建
- **AND** 工作流 trace SHALL 将本地索引步骤标记为 skipped 而非耗时构建

#### Scenario: Documents appear later
- **WHEN** 本地文档目录中新增了可索引文件
- **THEN** 系统 SHALL 基于新的快照重新构建索引
- **AND** 跳过逻辑 SHALL NOT 缓存"目录为空"的结论而忽略后续文件

## MODIFIED Requirements

### Requirement: Unified execution SHALL enforce plan budgets and replan only for declared gaps
系统 SHALL 在查询、结果、时间和恢复次数预算内执行计划，并仅在验证或证据账本声明可恢复缺口时追加步骤。可追加的恢复步骤包括时间覆盖补充检索与 `QUERY_REFORMULATION` 改写重搜；所有恢复步骤 SHALL 与首次检索共用同一证据账本与预算。

#### Scenario: Plan budget is exhausted
- **WHEN** 执行达到计划的查询、结果或恢复预算
- **THEN** 系统 SHALL 停止新增检索步骤
- **AND** 验证阶段 SHALL 返回证据不足或其他适当结果状态

#### Scenario: Recoverable evidence gap is found
- **WHEN** 证据账本显示存在受策略允许且可由一个受限步骤补齐的缺口
- **THEN** 系统 SHALL 创建并执行该恢复步骤
- **AND** 恢复步骤 SHALL 使用剩余计划预算并写入 trace

#### Scenario: Reformulation recovery shares the evidence ledger
- **WHEN** 验证对 authority、比较成员或检索词质量缺口判定可恢复
- **THEN** 统一主执行层 SHALL 以改写查询重新执行 web 检索
- **AND** 新证据 SHALL 并入首次检索的同一证据账本并重新参与融合与验证
