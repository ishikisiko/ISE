## MODIFIED Requirements

### Requirement: System SHALL trigger ReAct fallback only for recoverable failures
系统 SHALL 在两类情形下将执行升级至 ReAct 路径：（a）统一默认搜索主链路的 post-check 失败且失败类型适合多步工具补救；（b）会话续跑中用户反馈被判定为延续，此时人类反馈充当裁判角色。两类情形 SHALL 复用同一 fallback 上下文构造机制，上下文来源（机器 verdict 或人类反馈）SHALL 在元数据中可区分。

#### Scenario: Recoverable unified-pipeline failure triggers ReAct fallback
- **WHEN** post-check 识别出统一默认搜索主链路的回答存在可恢复失败，如约束覆盖缺失、需要补证据或需要多跳综合
- **THEN** 系统 SHALL 调用 `ReactAgentOrchestrator` 作为 fallback-only 执行器
- **AND** 返回结果 SHALL 标记 fallback 已触发以及触发原因

#### Scenario: Human feedback triggers ReAct continuation
- **WHEN** 携带 `conversation_id` 的反馈轮被判定为延续
- **THEN** 系统 SHALL 构造包含上一轮答案、用户反馈与继承约束的续跑上下文
- **AND** 调用 ReAct 路径在该会话 checkpoint 上续跑
- **AND** 返回元数据 SHALL 标记本轮裁判来源为人类反馈

#### Scenario: Non-recoverable failure does not trigger ReAct
- **WHEN** post-check 失败原因是搜索不可用、外部 API 不可用、数据源缺失或首答已明确承认无足够信息
- **THEN** 系统 SHALL NOT 自动触发 ReAct fallback
- **AND** 系统 SHALL 返回首答或现有错误信息并保留 post-check verdict
