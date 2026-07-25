# react-agent Specification

> **Status:** reframing at M1 — 能力存续但立论框架会变，可修补，不要在现框架上做大投入。 分类依据见 `docs/agentic_loop_roadmap.md`。

## Purpose
TBD - created by archiving change langchain-react-agent. Update Purpose after archive.
## Requirements
### Requirement: ReAct Agent 引擎
系统 SHALL 提供基于显式状态机（LangGraph graph）的迭代推理引擎，图结构为 `act → observe → evaluate → (continue | finish)`。该引擎接收用户查询，输出最终回答。系统 SHALL 保留 legacy LangChain `AgentExecutor` 引擎作为可配置回退路径。

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
- **THEN** Agent SHALL 返回当前已有的最佳答案（即使不完整）
- **AND** 在返回结果中标记 `truncated: true`
- **AND** 循环终止原因 SHALL 标记为 `exhausted`

#### Scenario: 引擎可配置切换
- **WHEN** 配置 `reactAgent.engine` 为 `legacy`
- **THEN** 系统 SHALL 使用 legacy `AgentExecutor` 路径执行
- **AND** 未配置或配置为 `langgraph` 时 SHALL 使用显式状态机引擎

#### Scenario: 模型提议结束需经评估确认
- **WHEN** 模型在 act 阶段产出最终答案提议
- **THEN** 循环 SHALL NOT 直接终止
- **AND** evaluate 节点 SHALL 验证约束 checklist 后方决定是否接受该答案

### Requirement: 自定义 ReAct Prompt
系统 SHALL 支持注入自定义 ReAct System Prompt，以支持中文推理场景。

#### Scenario: 使用默认英文 Prompt
- **WHEN** 系统未提供自定义 prompt
- **THEN** 使用 LangChain 默认 ReAct prompt

#### Scenario: 使用自定义中文 Prompt
- **WHEN** 调用 `create_react_agent` 时传入 `react_prompt` 参数
- **THEN** 使用传入的 prompt 替代默认 prompt

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

