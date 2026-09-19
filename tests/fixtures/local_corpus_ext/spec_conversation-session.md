# conversation-session Specification

> **Status:** active — 当前契约，且在目标架构中存续。 分类依据见 `docs/agentic_loop_roadmap.md`。

## Purpose
Define the lifecycle, persistence, and governance rules for client-supplied `conversation_id` values that identify a continuous multi-turn dialogue.

## Requirements
### Requirement: 会话标识与会话生命周期
系统 SHALL 支持以 `conversation_id` 标识一段连续对话。`conversation_id` 由客户端生成并随 answer 请求提交；服务端 SHALL 将其映射为 ReAct 图的 `thread_id` 与会话记录的归属键。请求缺省 `conversation_id` 时，系统 SHALL 按无状态单轮处理，行为与现状完全一致。

#### Scenario: 请求缺省会话标识
- **WHEN** answer 请求（JSON 或 SSE）未携带 `conversation_id`
- **THEN** 系统 SHALL 按无状态单轮问答处理
- **AND** 不创建任何 checkpoint 或会话记录

#### Scenario: 首次携带会话标识
- **WHEN** answer 请求携带一个服务端从未见过的 `conversation_id`
- **THEN** 系统 SHALL 以该 id 开启新会话
- **AND** 本轮请求/应答 SHALL 写入会话记录
- **AND** 若本轮进入 ReAct 路径，图状态 SHALL 以该 id 为 `thread_id` 持久化

#### Scenario: 后续轮次携带同一会话标识
- **WHEN** answer 请求携带已存在的 `conversation_id`
- **THEN** 系统 SHALL 加载该会话的历史记录与（如存在的）ReAct checkpoint
- **AND** 按 conversation-resume 能力的规则处理本轮请求

### Requirement: 会话记录持久化
系统 SHALL 将每一轮问答（无论走 small talk、direct、搜索主链路或 ReAct 路径）写入持久化会话记录，至少包含：`conversation_id`、轮次序号、用户 query、最终 answer、解析出的时间约束（如有）、创建时间。会话记录 SHALL 与 ReAct checkpoint 存放于同一本地 SQLite 库。

#### Scenario: 全路径落库
- **WHEN** 一次携带 `conversation_id` 的请求完成应答
- **THEN** 无论本次走了哪条执行路径，该轮 query 与 answer SHALL 均可在会话记录中检索

#### Scenario: 时间约束随轮次落库
- **WHEN** 某轮 query 经 `time_parser` 解析出时间约束
- **THEN** 该结构化约束 SHALL 随本轮会话记录一并持久化

### Requirement: 会话显式重置
前端 SHALL 提供"新会话"操作，生成新的 `conversation_id` 并使后续请求携带新 id。前端 SHALL NOT 为会话续跑展示专门的阶段文案，流式阶段展示沿用现有 tracer 输出。

#### Scenario: 用户点击新会话
- **WHEN** 用户在前端触发"新会话"
- **THEN** 前端 SHALL 生成新 `conversation_id` 并用于后续请求
- **AND** 旧会话的 checkpoint 与记录 SHALL 保留在服务端不被删除

### Requirement: 会话存储治理
系统 SHALL 提供 checkpoint 库路径与最大线程数的配置项（`CONVERSATION_CHECKPOINT_PATH`、`CONVERSATION_MAX_THREADS`）。当线程数超过上限时，系统 SHALL 按最近最少使用清理最旧的 checkpoint 及其会话记录。

#### Scenario: 超出线程上限触发清理
- **WHEN** 新建会话使线程总数超过 `CONVERSATION_MAX_THREADS`
- **THEN** 最久未使用的会话 checkpoint 与记录 SHALL 被清理
- **AND** 当前活跃会话 SHALL NOT 受影响

### Requirement: 会话并发串行化
系统 SHALL 对同一 `conversation_id` 的并发请求按线程加锁串行执行，防止 checkpoint 交错污染。

#### Scenario: 同会话并发请求
- **WHEN** 两个携带相同 `conversation_id` 的请求同时到达
- **THEN** 第二个请求 SHALL 等待第一个请求完成后再执行
- **AND** 第二个请求 SHALL 基于第一个请求写入的状态续跑
