## MODIFIED Requirements

### Requirement: 上下文预算控制
系统 SHALL 在续跑前将 checkpoint 消息历史的预算控制委托给 `context-compaction` 的分级压缩，SHALL NOT 按消息条数直接删除工具/观察消息。各轮用户消息与最终答案消息 SHALL NOT 被压缩或删除。压缩结果经 checkpoint 持久化，续跑 SHALL 直接继承上一轮压缩后的消息序列。

#### Scenario: 续跑前超出预算
- **WHEN** 续跑时 checkpoint 消息的 token 预算估算超过阈值
- **THEN** 系统 SHALL 执行一次分级压缩而非按条数删除
- **AND** 首轮用户消息、各轮最终答案与最近保留窗口内交互 SHALL 保留
- **AND** 被折叠证据的完整正文 SHALL 仍可通过 `recall_evidence` 回灌

#### Scenario: 预算内不压缩
- **WHEN** checkpoint 消息的 token 预算估算未超过阈值
- **THEN** 续跑 SHALL NOT 执行任何压缩或消息移除操作

#### Scenario: 续跑继承已压缩序列
- **WHEN** 上一轮运行中发生过压缩
- **THEN** 本轮续跑 SHALL 以 checkpoint 中已压缩的消息序列为起点
- **AND** 系统 SHALL NOT 重放被压缩掉的原始工具消息

#### Scenario: 续跑输入保持配对完整
- **WHEN** 系统构造续跑输入
- **THEN** 输入消息序列中 SHALL NOT 存在没有对应工具结果消息的 `tool_calls`
