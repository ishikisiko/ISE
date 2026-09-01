## MODIFIED Requirements

### Requirement: Tool-aware ReAct Prompt
系统 SHALL 为显式状态机注入与实际启用工具一致的系统提示，并且不暴露未启用工具。系统提示 SHALL 按生效的 `AutonomyPolicy` 组装：`checklist_injection=enforce` 时成功标准 SHALL 以必须满足的措辞注入；`checklist_injection=hint` 时同一标准 SHALL 以参考信息的措辞注入且 SHALL NOT 表述为终止条件。提示 SHALL 包含当前剩余的迭代与每工具调用预算。

#### Scenario: 构建循环提示
- **WHEN** LangGraph ReAct 循环开始执行
- **THEN** 模型工具绑定或兼容提示 SHALL 只包含当前启用工具及其描述
- **AND** 非原生工具调用模型 SHALL 使用相同工具清单的结构化兼容提示

#### Scenario: 成功标准按策略措辞注入
- **WHEN** `checklist_injection=hint`
- **THEN** 由 `QueryAnalysis` 派生的成功标准 SHALL 作为参考信息出现在提示中
- **AND** 提示 SHALL NOT 声称这些标准是回答被接受的必要条件

#### Scenario: 预算自述
- **WHEN** 循环开始一轮 act
- **THEN** 提示 SHALL 包含剩余迭代数与各工具的剩余调用额度
- **AND** 该信息 SHALL 与实际执行的预算约束一致

### Requirement: Process narration SHALL NOT become the final answer
When the effective `AutonomyPolicy` sets `narration_guard=on`, the runner SHALL
reject clearly first-person search planning or process narration without an
enabled structured tool call as a final-answer candidate. It SHALL emit a
bounded response-format event and either retry with corrective feedback or use
the existing neutral terminal message; it SHALL NOT return the process
narration in `answer`. When `narration_guard=off`, the runner SHALL NOT apply
this rejection, and a text-only response without a tool call SHALL be treated
as a candidate final answer under the ordinary evaluation path.

#### Scenario: A model describes a planned search instead of calling a tool
- **WHEN** `narration_guard=on` and a model response says it needs or will
  perform a search but contains no enabled tool call
- **THEN** the response SHALL be excluded from the final answer
- **AND** the trace SHALL identify the format outcome without including the
  raw model prose

#### Scenario: The guard is disabled
- **WHEN** `narration_guard=off` and a model response contains planning prose
  without a tool call
- **THEN** the runner SHALL NOT emit an invalid-final-response outcome
- **AND** the response SHALL enter the ordinary evaluation path as a candidate
  final answer

## ADDED Requirements

### Requirement: 规划文本与工具调用共存
当 `narration_guard=off` 时，若模型的一条响应同时携带文本与结构化工具调用，系统 SHALL 保留该文本进入消息序列与执行轨迹，并 SHALL 照常执行其工具调用。该文本 SHALL NOT 被当作候选终答，也 SHALL NOT 因存在工具调用而被丢弃。

#### Scenario: 同一响应携带计划与工具调用
- **WHEN** `narration_guard=off` 且模型响应同时包含文本与 `tool_calls`
- **THEN** 工具调用 SHALL 照常执行
- **AND** 文本 SHALL 保留在消息序列中供后续轮次可见
- **AND** 该轮 SHALL NOT 被判定为模型提议终答

#### Scenario: 守卫开启时保持现状
- **WHEN** `narration_guard=on` 且模型响应同时包含文本与 `tool_calls`
- **THEN** 处理方式 SHALL 与引入本能力之前一致

#### Scenario: 共存文本进入留痕
- **WHEN** 一条携带文本的工具调用响应被处理
- **THEN** 执行轨迹 SHALL 以有界形式记录该轮存在模型规划文本
- **AND** 记录 SHALL NOT 包含隐藏推理内容或完整提示词
