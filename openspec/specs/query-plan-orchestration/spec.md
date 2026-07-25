# query-plan-orchestration Specification

> **Status:** superseded by roadmap M5 — 该能力将在对应里程碑删除，不要新增 requirement 或加固；仅接受阻断性缺陷的最小修复。 分类依据见 `docs/agentic_loop_roadmap.md`。

## Purpose
TBD - created by archiving change improve-query-execution-orchestration. Update Purpose after archive.

## Requirements
### Requirement: System SHALL create a shared query analysis before evidence execution
系统 SHALL 在默认搜索路径执行来源选择或检索前创建结构化 `QueryAnalysis`，至少表达意图形态、实体、歧义、时间/新鲜度约束、声明类别、比较成员和是否允许搜索。

#### Scenario: Query contains multiple constraints
- **WHEN** 用户问题同时包含比较、实体、时间、新鲜度或数值声明等多个约束
- **THEN** 系统 SHALL 将这些约束保存在同一 `QueryAnalysis` 中
- **AND** 后续路由、检索、融合和验证 SHALL 使用该分析而不是各自重新从原始文本推断

#### Scenario: Query has no evidence requirement
- **WHEN** 分析表明问题可由 direct answer 或 local-only 路径完整处理
- **THEN** 系统 SHALL 标记不需要外部证据计划
- **AND** 系统 SHALL 保留现有快速路径语义

### Requirement: System SHALL derive a bounded query plan from the analysis
系统 SHALL 将需要证据的分析转换为 `QueryPlan`，其中包含证据策略、按顺序执行的步骤、每步目的、允许来源、查询标识以及查询和结果预算。

#### Scenario: Search-enabled query receives a plan
- **WHEN** `QueryAnalysis` 判定需要外部或领域证据
- **THEN** 系统 SHALL 创建带有至少一个明确用途的计划步骤
- **AND** 每个步骤 SHALL 受该计划的查询和结果预算约束

#### Scenario: Existing domain data is appropriate
- **WHEN** 分析和证据策略要求现有 weather、transportation、finance 或 sports 等领域数据
- **THEN** 系统 SHALL 将领域 API 调用表示为显式计划步骤
- **AND** 该步骤的结果 SHALL 进入同一证据账本

### Requirement: Critical ambiguity SHALL be resolved at the planning boundary
系统 SHALL 在无法安全形成证据计划时产生澄清状态，而不是猜测实体、来源或约束。

#### Scenario: Required entity is ambiguous
- **WHEN** 用户要求比较、核验或获取数值声明，但关键实体不能唯一解析
- **THEN** 系统 SHALL 生成 `clarification_required` 计划结果
- **AND** 系统 SHALL NOT 以猜测的实体或来源执行证据检索

#### Scenario: Non-critical ambiguity does not block a plan
- **WHEN** 歧义不影响用户请求的核心实体、声明或证据标准
- **THEN** 系统 SHALL 在计划中记录该歧义
- **AND** 系统 SHALL 继续执行满足其余约束的受限计划

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

### Requirement: Authority-required comparisons SHALL plan bounded target-domain recovery
The system SHALL create a bounded recovery step with one deterministic target record
per mapped member when an authority-required comparison contains members that map to
configured official domains.

#### Scenario: Pricing comparison has three mapped members
- **WHEN** a pricing comparison names three entities that map to configured official domains
- **THEN** the plan SHALL retain the three target entities and their allowed registrable domains
- **AND** the recovery step SHALL be bounded to the configured target limit and recovery budget

#### Scenario: Comparison member has no official-domain mapping
- **WHEN** a comparison member has no configured official-domain alias
- **THEN** the plan SHALL not invent an official domain for that member
- **AND** existing generic recovery semantics SHALL remain available

### Requirement: Plan layer SHALL use the single canonical registrable-domain helper
The system SHALL compute registrable domains in the planning layer using the canonical `evidence.source_tiering.registrable_domain` helper. The system SHALL NOT maintain a duplicate domain-normalization or public-suffix implementation in the planning module, so that a given URL resolves to the same domain in both the plan layer and the evidence-tier layer.

#### Scenario: Plan and tier layers agree on a domain
- **WHEN** the plan layer and the evidence-tier layer each normalize the same URL
- **THEN** both SHALL return the identical registrable domain
- **AND** no private/duplicate public-suffix table SHALL remain in the planning module

### Requirement: Official recovery targets SHALL be sourced through the domain resolver
The system SHALL derive authority-recovery official-domain targets for comparison members by querying the domain resolver (with `pins` precedence) rather than by reading a separate static alias map. Targets the resolver cannot confirm as official SHALL not be planned as official recovery targets.

#### Scenario: Comparison member resolves to an official domain
- **WHEN** an authority-required comparison member has no configured pin
- **AND** the resolver returns a confirmed official domain for that member
- **THEN** the plan SHALL include that domain as a target official site
- **AND** the planned recovery step SHALL record the resolver signals in the trace

#### Scenario: Comparison member resolves to nothing official
- **WHEN** the resolver returns `confidence="none"` for a comparison member
- **THEN** the plan SHALL NOT emit an official recovery target for that member
- **AND** the verification policy SHALL treat that member as lacking official coverage
