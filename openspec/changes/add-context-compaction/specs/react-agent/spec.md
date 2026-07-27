## MODIFIED Requirements

### Requirement: ReAct Agent 引擎
系统 SHALL 提供基于显式状态机（LangGraph graph）的迭代推理引擎，图结构为 `act → observe → evaluate → (continue | compact | finish)`。`compact` 节点 SHALL 只能由 `evaluate` 进入并且只能回到 `act`，SHALL NOT 递增 `iteration`。该引擎接收用户查询，输出最终回答，并且是唯一的 ReAct 响应循环实现。

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

#### Scenario: 终止判定优先于压缩
- **WHEN** evaluate 同时判定循环可终止且预算越过压缩阈值
- **THEN** 系统 SHALL 直接终止
- **AND** 系统 SHALL NOT 执行压缩

#### Scenario: 压缩后回到决策
- **WHEN** `compact` 节点执行完毕
- **THEN** 控制流 SHALL 回到 `act`
- **AND** `iteration` SHALL NOT 因该次压缩而增加
