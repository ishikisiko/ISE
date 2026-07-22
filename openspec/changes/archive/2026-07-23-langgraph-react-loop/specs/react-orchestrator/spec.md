# react-orchestrator Specification (delta)

## MODIFIED Requirements

### Requirement: ReactAgentOrchestrator SHALL support fallback execution context
系统 SHALL 支持将 ReactAgentOrchestrator 作为默认 pipeline 的补救执行器使用，并接收 fallback 上下文。

#### Scenario: Fallback invocation carries prior answer context
- **WHEN** 默认 pipeline 的 post-check 决定触发 ReAct fallback
- **THEN** ReactAgentOrchestrator SHALL 能接收原始 query、首答摘要和 post-check failure types
- **AND** Agent SHALL 使用这些上下文作为补救执行的起点

#### Scenario: Fallback invocation carries available evidence summary
- **WHEN** 默认 pipeline 已经获取了搜索结果、领域数据或本地文档
- **THEN** ReactAgentOrchestrator SHALL 支持接收这些证据的摘要或引用
- **AND** Agent SHALL 可在不完全从零开始的前提下继续补救

#### Scenario: Fallback invocation carries unified evidence metadata
- **WHEN** 默认 pipeline 已经生成 unified evidence metadata
- **THEN** ReactAgentOrchestrator SHALL 支持接收 `evidence_items`、`evidence_sources_active`、`evidence_sources_used` 或对应来源类型信息
- **AND** fallback 响应 SHALL 保留这些元数据语义

#### Scenario: Fallback invocation carries explicit success criteria
- **WHEN** post-check verdict 包含 `failure_types`、`missing_constraints` 或 `recovery_goal`
- **THEN** ReactAgentOrchestrator SHALL 将其作为显式成功标准注入循环初始状态
- **AND** 循环终止判定 SHALL 对照该成功标准执行

### Requirement: ReactAgentOrchestrator SHALL return fallback-compatible metadata
系统 SHALL 在 ReAct 作为 fallback 返回结果时输出与默认主链路兼容的元数据。

#### Scenario: Successful fallback response
- **WHEN** ReactAgentOrchestrator 产出用于替代首答的最终结果
- **THEN** 返回结构 SHALL 包含 `answer`、`control` 和 `search_hits`
- **AND** `control` SHALL 标明执行来源为 fallback ReAct
- **AND** `control` SHALL 能暴露 fallback 过程中沿用的 evidence source metadata

#### Scenario: Fallback execution ends without full resolution
- **WHEN** ReAct fallback 达到迭代上限或未能完全补救首答问题
- **THEN** 返回结构 SHALL 保留失败或截断信息
- **AND** `control` SHALL 包含与 fallback 执行相关的原因说明

#### Scenario: Fallback response exposes loop termination status
- **WHEN** ReAct fallback 以任意终止原因结束
- **THEN** `control["loop_status"]` SHALL 标记终止语义（`succeeded` / `exhausted` / `stagnated` / `unrecoverable` 之一）
- **AND** `control` SHALL 包含每轮迭代的 LoopVerdict 摘要或其可追溯引用

#### Scenario: Fallback response metadata remains backward compatible
- **WHEN** 调用方读取 fallback 返回的 `control` 元数据
- **THEN** 既有字段（`search_performed`、`decision`、`search_mode`、`final_executor`、`fallback_triggered` 等）SHALL 保持原有语义不变
- **AND** 循环状态相关字段 SHALL 仅以新增字段形式出现
