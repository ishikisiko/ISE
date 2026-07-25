# query-execution-trace Specification

> **Status:** reframing at M5 — 能力存续但立论框架会变，可修补，不要在现框架上做大投入。 分类依据见 `docs/agentic_loop_roadmap.md`。

## Purpose
TBD - created by archiving change improve-query-execution-orchestration. Update Purpose after archive.

## Requirements
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

### Requirement: ReAct fallback actions SHALL be available to backend trace consumers
The backend SHALL make LangGraph ReAct action events available through the
existing `WorkflowTracer` consumers: SSE step frames, the persisted process
audit, and a bounded additive final-response control projection.

#### Scenario: A streamed request enters the ReAct loop
- **WHEN** `/api/answer/stream` runs a LangGraph ReAct fallback or conversation
  continuation
- **THEN** each emitted ReAct iteration and tool event SHALL be sent as an SSE
  `step` frame in execution order
- **AND** the server SHALL not need a new SSE event type or frontend protocol

#### Scenario: A ReAct response finishes
- **WHEN** the ReAct runner completes
- **THEN** the response control metadata SHALL include a bounded `react_trace`
  projection of its safe action events
- **AND** existing response and execution-trace fields SHALL remain compatible

#### Scenario: Process audit is enabled
- **WHEN** a traced ReAct turn is persisted through the process audit writer
- **THEN** its safe action events SHALL be recorded with the other workflow
  steps subject to the existing size and redaction rules
- **AND** an audit write failure SHALL NOT prevent the answer response

### Requirement: Individual retrieval API calls SHALL expose bounded audit records
Every actual search-provider request SHALL produce its own additive workflow
step containing the provider outcome and the result list returned by that
specific call. The system SHALL capture those records before cross-provider
merge, reranking, or final-reference limiting.

#### Scenario: A provider search succeeds
- **WHEN** a configured provider returns one or more web search results
- **THEN** the trace SHALL contain one completed API-call step for that provider
- **AND** the step SHALL include a bounded, browser-safe list of that call's
  title, URL, and snippet records
- **AND** the displayed list SHALL NOT be reconstructed from final merged or
  reranked evidence

#### Scenario: A composite or fallback search makes multiple requests
- **WHEN** a combined client fans out or a priority client tries a fallback
  provider
- **THEN** every underlying provider HTTP attempt SHALL be represented
  independently, including empty and failed outcomes
- **AND** a failed primary attempt SHALL NOT hide a later successful fallback
  result list

#### Scenario: Selected-page extraction runs
- **WHEN** an existing selected-page extraction adapter is invoked
- **THEN** each extracted or failed URL SHALL be available as a bounded
  extraction audit record with provider, status, and content-size facts
- **AND** complete page content, opaque provider payloads, credentials, and
  URL query values SHALL NOT enter the trace

### Requirement: The workflow UI SHALL collapse audit result lists by API call
The frontend SHALL render each API call's audit records in a collapsed,
expandable group associated with that call's workflow step.

#### Scenario: A search provider returns five results
- **WHEN** a search API-call step contains five result records
- **THEN** the workflow SHALL show one collapsed result group for that call
- **AND** expanding it SHALL show all five returned links individually with
  their bounded titles and snippets

#### Scenario: A page-extraction call is recorded
- **WHEN** an extraction API-call step contains page records
- **THEN** expanding its group SHALL identify the extracted page URL and
  provider/outcome facts without rendering the page body
