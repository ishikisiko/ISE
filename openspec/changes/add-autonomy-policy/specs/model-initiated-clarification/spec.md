## ADDED Requirements

### Requirement: ask_user 是一个本地非检索工具
系统 SHALL 提供 `ask_user` 工具，用于模型在执行过程中向用户提出澄清问题。该工具 SHALL NOT 发起任何外部调用、SHALL NOT 产生证据记录、SHALL NOT 进入 `EvidenceLedger`，并 SHALL 受独立的调用预算约束。

#### Scenario: 模型发起澄清
- **WHEN** `clarification_owner=model` 且模型调用 `ask_user`
- **THEN** 系统 SHALL 不执行任何 provider 请求
- **AND** 该次调用 SHALL NOT 产生 `EvidenceItem` 或影响证据增量判定

#### Scenario: 澄清预算耗尽
- **WHEN** 模型在同一次执行中超出 `ask_user` 的调用预算
- **THEN** 工具 SHALL 返回结构化 `budget_exhausted`
- **AND** 循环 SHALL 继续按现有证据推进而非中断

#### Scenario: 工具面按自主度暴露
- **WHEN** `clarification_owner=system`
- **THEN** `ask_user` SHALL NOT 出现在模型可见的工具面中

### Requirement: 澄清是可恢复的非终态
模型发起的澄清 SHALL 使本次执行进入等待用户输入的非终态：系统 SHALL 返回该澄清问题、SHALL 保留当前 loop 状态以供续跑、并 SHALL 在控制元数据中将其与预算耗尽、证据不足等终态明确区分。

#### Scenario: 流式响应表达非终态
- **WHEN** 模型在流式请求中调用 `ask_user`
- **THEN** 流 SHALL 发出可识别的等待输入事件并携带澄清问题
- **AND** 该事件 SHALL NOT 被表示为循环失败或答案已完成

#### Scenario: 用户回答后续跑
- **WHEN** 用户在同一会话中提供了澄清答复
- **THEN** 循环 SHALL 从保留的状态继续，而非从头重新执行
- **AND** 澄清前已获取的证据 SHALL 保留

#### Scenario: 用户不回答
- **WHEN** 会话在澄清等待状态下被放弃或重置
- **THEN** 系统 SHALL 保持状态一致并 SHALL NOT 泄漏未收口的执行记录

### Requirement: 澄清归属互斥
同一次执行中，确定性澄清短路与模型自主澄清 SHALL 互斥。`clarification_owner=system` 时 SHALL 保持现有的 loop 前确定性澄清短路；`clarification_owner=model` 时 SHALL NOT 在 loop 前短路，歧义 SHALL 由模型在循环内自行处置。

#### Scenario: 系统归属下保持现状
- **WHEN** `clarification_owner=system` 且分析判定存在关键歧义
- **THEN** 系统 SHALL 在进入 loop 前返回澄清响应
- **AND** 行为 SHALL 与引入本能力之前一致

#### Scenario: 模型归属下不短路
- **WHEN** `clarification_owner=model` 且分析判定存在关键歧义
- **THEN** 系统 SHALL 进入 loop 并把歧义信息提供给模型
- **AND** 是否发问 SHALL 由模型决定

#### Scenario: 确定性 preflight 不受影响
- **WHEN** 任一澄清归属下模型以不合法参数调用 skill
- **THEN** preflight SHALL 照常拒绝并返回 reason

### Requirement: 澄清轮次的会话语义
模型发起的澄清与用户的答复 SHALL 作为同一逻辑问答的组成部分记录，SHALL NOT 被计为一次独立的已完成问答，且 SHALL 在会话记录与 audit 中可识别为澄清往返。

#### Scenario: 澄清不产生独立答案记录
- **WHEN** 一次执行以模型澄清结束
- **THEN** 会话记录 SHALL 标记该轮为澄清等待而非已完成回答
- **AND** audit 记录 SHALL 完整覆盖该次执行

#### Scenario: 续跑后的完整记录
- **WHEN** 用户答复后循环继续并产出最终答案
- **THEN** 该逻辑问答的记录 SHALL 同时包含澄清往返与最终答案
- **AND** provenance 链 SHALL 覆盖澄清前后的全部工具调用
