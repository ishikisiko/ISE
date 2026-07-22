# react-agent Specification (delta)

## MODIFIED Requirements

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
