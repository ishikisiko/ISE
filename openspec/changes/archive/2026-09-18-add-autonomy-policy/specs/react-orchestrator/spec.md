## MODIFIED Requirements

### Requirement: Response control SHALL report actual loop execution
The adapter SHALL expose the terminal status, iterations, verdicts, actual evidence sources, per-tool budgets, and the effective autonomy policy without a fallback marker or engine-mode field. The reported autonomy facts SHALL include the effective mode name, its resolution source, and the advisory gap count actually produced during the run.

#### Scenario: The loop completes normally
- **WHEN** the shared critic accepts a candidate answer
- **THEN** `control.loop_status` SHALL be `succeeded`
- **AND** `control.final_executor` SHALL be `agentic_loop`

#### Scenario: The loop reaches a hard terminal
- **WHEN** the loop exhausts, stagnates, becomes unrecoverable, needs clarification, or lacks evidence
- **THEN** the corresponding terminal status and final verdict SHALL be returned
- **AND** any provisional answer SHALL explicitly state that evidence or execution budget was insufficient

#### Scenario: The effective autonomy policy is reported
- **WHEN** any query completes through the loop
- **THEN** control SHALL expose the effective autonomy mode and its resolution source
- **AND** `control.final_executor` SHALL remain `agentic_loop` regardless of the mode

#### Scenario: Advisory rules produced gaps
- **WHEN** the run used advisory critic or citation checking and gaps were hit
- **THEN** control SHALL expose the bounded advisory gap count
- **AND** the terminal status SHALL NOT be derived from those gaps

## ADDED Requirements

### Requirement: 自主度由调用方注入而非编排器推断
`ReactAgentOrchestrator` SHALL 接收已解析的自主度策略作为输入并原样透传给 loop，SHALL NOT 自行解析配置或依据查询内容推断自主度。

#### Scenario: 策略透传
- **WHEN** 上层以某个已解析策略调用 orchestrator
- **THEN** loop SHALL 使用该策略执行
- **AND** orchestrator SHALL NOT 覆盖其中任何字段

#### Scenario: 未提供策略
- **WHEN** 直接调用方未提供策略
- **THEN** orchestrator SHALL 使用 `guided` 预设
- **AND** 控制元数据 SHALL 标明策略来源为默认值
