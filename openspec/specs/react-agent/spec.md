# react-agent Specification

> **Status:** active - roadmap M5 sole-loop runtime contract.

## Purpose
Define the explicit LangGraph act/observe/evaluate loop used by the production runtime.
## Requirements
### Requirement: ReAct Agent 引擎
系统 SHALL 提供基于显式状态机（LangGraph graph）的迭代推理引擎，图结构为 `act → observe → evaluate → (continue | finish)`。该引擎接收用户查询，输出最终回答，并且是唯一的 ReAct 响应循环实现。

#### Scenario: 基础 ReAct 推理流程
- **WHEN** 用户提交查询且系统使用 ReAct Agent 模式
- **THEN** Agent 执行 act → observe → evaluate 循环，最多迭代 max_iterations 次
- **AND** 每次迭代后由 evaluate 节点判定是否满足终止条件
- **AND** 返回最终答案

#### Scenario: 多工具迭代选择
- **WHEN** 复杂查询需要多个工具
- **THEN** Agent 在每次迭代中根据当前状态选择合适的工具
- **AND** Agent 考虑工具 description 和当前上下文

#### Scenario: 达到最大迭代次数
- **WHEN** Agent 达到 max_iterations 上限仍未通过 evaluate 判定
- **THEN** Agent SHALL 返回带证据/预算不足说明的当前候选答案，或中性的不足回答
- **AND** 循环终止原因 SHALL 标记为 `exhausted`

#### Scenario: 缺失 LangGraph 依赖
- **WHEN** 运行时缺少 LangGraph 依赖
- **THEN** 系统 SHALL 明确失败
- **AND** 系统 SHALL NOT 静默切换到另一个具有独立停止逻辑的执行器

#### Scenario: 模型提议结束需经评估确认
- **WHEN** 模型在 act 阶段产出最终答案提议
- **THEN** 循环 SHALL NOT 直接终止
- **AND** evaluate 节点 SHALL 验证约束 checklist 后方决定是否接受该答案

### Requirement: Tool-aware ReAct Prompt
系统 SHALL 为显式状态机注入与实际启用工具一致的系统提示，并且不暴露未启用工具。

#### Scenario: 构建循环提示
- **WHEN** LangGraph ReAct 循环开始执行
- **THEN** 模型工具绑定或兼容提示 SHALL 只包含当前启用工具及其描述
- **AND** 非原生工具调用模型 SHALL 使用相同工具清单的结构化兼容提示

### Requirement: LangGraph ReAct SHALL emit auditable action events
When the LangGraph ReAct engine runs with a workflow tracer, it SHALL emit
ordered, additive events for each iteration start, each enabled tool request,
each tool outcome, and the iteration's evaluation. Events SHALL identify the
iteration and tool, and SHALL include only bounded public arguments and
outcome facts; they SHALL NOT contain hidden reasoning, prompts, credentials,
or complete tool responses.

#### Scenario: A tool call succeeds during an iteration
- **WHEN** an act node requests one or more enabled tools
- **THEN** the tracer SHALL emit an active and completed event for every tool
- **AND** the completed event SHALL include the tool name, safe query summary,
  elapsed duration, and bounded result summary

#### Scenario: A tool invocation fails
- **WHEN** an enabled tool raises or returns a recognized error result
- **THEN** the tracer SHALL record an error outcome with a bounded safe reason
- **AND** the failed observation SHALL participate in normal loop termination
  accounting

#### Scenario: A compatible XML function request is returned by a model
- **WHEN** a model response has no native tool call but contains
  `<function>enabled_tool</function><query>...</query>`
- **THEN** the runner SHALL normalize it into an enabled tool call and execute
  it through the ordinary observe path
- **AND** the trace SHALL identify it as a tool action rather than answer text

#### Scenario: Tool-like markup is unsupported or malformed
- **WHEN** a model response contains unrecognized function markup
- **THEN** the runner SHALL emit a traceable invalid-tool-request outcome
- **AND** the markup SHALL NOT become the final answer text

### Requirement: Process narration SHALL NOT become the final answer
The runner SHALL reject clearly first-person search planning or process
narration without an enabled structured tool call as a final-answer candidate.
It SHALL emit a bounded response-format event and either retry with corrective
feedback or use the existing neutral terminal message; it SHALL NOT return the
process narration in `answer`.

#### Scenario: A model describes a planned search instead of calling a tool
- **WHEN** a model response says it needs or will perform a search but contains
  no enabled tool call
- **THEN** the response SHALL be excluded from the final answer
- **AND** the trace SHALL identify the format outcome without including the
  raw model prose
