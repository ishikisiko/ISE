# unified-rag-execution Specification

> **Status:** reframing at M5 — 能力存续但立论框架会变，可修补，不要在现框架上做大投入。 分类依据见 `docs/agentic_loop_roadmap.md`。

## Purpose
Define the single primary RAG execution layer used by the default orchestrator for local, search, and hybrid execution.

## Requirements
### Requirement: Default pipeline SHALL use a single primary RAG execution layer
系统 SHALL 为默认主编排器提供单一主 RAG 执行层，用于处理 local RAG、search RAG 以及 search+local 的混合执行，而不是长期并行维护两套主执行实现。

#### Scenario: Default search path uses unified execution layer
- **WHEN** 默认主编排器判定需要执行搜索增强回答
- **THEN** 系统 SHALL 使用统一主 RAG 执行层完成搜索结果整合、可选本地文档检索和答案生成
- **AND** 系统 SHALL NOT 在默认路径上并行依赖一套独立的 legacy SearchRAG 主实现

#### Scenario: Search disabled path uses unified local execution semantics
- **WHEN** 查询在搜索关闭的条件下执行
- **THEN** 系统 SHALL 使用统一主执行层中的 local-only 语义处理本地文档检索或 direct fallback
- **AND** CLI 与 API 的该路径行为 SHALL 保持一致

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
