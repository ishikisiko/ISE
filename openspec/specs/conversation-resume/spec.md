# conversation-resume Specification

## Purpose
Define how multi-turn feedback on an existing conversation resumes the ReAct loop via persistent checkpointing, intent classification, constraint inheritance and message-window budgeting.

## Requirements
### Requirement: ReAct 状态跨请求断点续跑
系统 SHALL 在编译 ReAct 循环图时挂载持久化 checkpointer（SQLite 实现）。同一会话的后续轮次 SHALL 以该会话 `thread_id` 的部分输入重新调用图：循环计数与终止标志按新一轮重置，`messages` 以追加语义写入用户反馈，`evidence_pool` 与历史 `verdicts` 从 checkpoint 原样保留。

#### Scenario: 续跑保留证据池
- **WHEN** 会话存在 ReAct checkpoint 且新一轮请求被判定为延续
- **THEN** 新一轮 invoke 的输入 SHALL NOT 包含 `evidence_pool` 键
- **AND** 循环 act 节点可见的 `evidence_pool` SHALL 为上一轮结束时的内容

#### Scenario: 续跑重置循环控制字段
- **WHEN** 系统构造续跑输入
- **THEN** `iteration`、`fingerprint_streak`、`no_progress_streak`、`tool_error_streak`、`final_proposed`、`termination_reason`、`final_answer`、`judge_error` SHALL 被重置为新一轮初始值
- **AND** `verdicts` SHALL 保留历史轮次的判定记录

#### Scenario: checkpointer 不可用时降级
- **WHEN** checkpointer 初始化失败或 checkpoint 读取异常
- **THEN** 系统 SHALL 记录降级日志并按无状态模式处理本轮请求
- **AND** 主流程 SHALL NOT 因会话功能故障而报错

### Requirement: 反馈意图判别
对携带已存在 `conversation_id` 的请求，系统 SHALL 先判别本轮属于「延续」还是「新话题」。判别失败时 SHALL 默认按延续处理。

#### Scenario: 判定为延续
- **WHEN** 反馈指涉上一轮答案或主题（如"精简一点""第二部分再展开""那竞争对手呢"）
- **THEN** 系统 SHALL 将用户反馈连同续跑上下文注入该会话的 ReAct 图续跑
- **AND** 续跑上下文 SHALL 包含上一轮答案与继承的时间约束（如有）

#### Scenario: 判定为新话题
- **WHEN** 本轮 query 与会话历史无明显指涉关系
- **THEN** 系统 SHALL 忽略旧 checkpoint，按全新查询走现有路由处理
- **AND** 旧会话状态 SHALL 保留不被删除

#### Scenario: 改写类反馈不产生多余检索
- **WHEN** 延续反馈为纯改写诉求（如"换成表格""精简一半"）
- **THEN** 续跑循环 SHALL 允许模型在不调用任何工具的情况下直接产出最终答案
- **AND** 本轮结果 SHALL 仍写入 checkpoint 与会话记录

### Requirement: 时间约束跨轮继承
续跑轮中若用户反馈不含新的时间表达式，系统 SHALL 以会话记录中最近一轮的时间约束为锚注入本轮约束与上下文；若含新时间表达式，SHALL 以当前时间重新解析并覆盖继承值。

#### Scenario: 反馈无时间表达式
- **WHEN** 首轮 query 解析出时间约束（如"上周"），续跑反馈为"那后来呢"
- **THEN** 本轮 SHALL 继承首轮的结构化时间约束
- **AND** SHALL NOT 以本轮运行时刻重新锚定相对时间

#### Scenario: 反馈含新时间表达式
- **WHEN** 续跑反馈含有可解析的时间表达式（如"改成上个月"）
- **THEN** 系统 SHALL 以当前时间解析新表达式并作为本轮约束
- **AND** 新约束 SHALL 随本轮会话记录落库

### Requirement: 上下文预算控制
系统 SHALL 在续跑前按配置窗口（`CONVERSATION_HISTORY_WINDOW`）裁剪 checkpoint 消息历史。裁剪 SHALL 仅针对工具调用与观察类消息；各轮用户消息与最终答案消息 SHALL NOT 被裁剪。

#### Scenario: 超出窗口触发裁剪
- **WHEN** 续跑时 checkpoint 消息总量超出预算
- **THEN** 最旧的工具/观察消息 SHALL 通过 RemoveMessage 操作移除
- **AND** 首轮用户消息、各轮最终答案与最近窗口内交互 SHALL 保留

#### Scenario: 窗口内不裁剪
- **WHEN** checkpoint 消息总量未超预算
- **THEN** 续跑 SHALL NOT 执行任何 RemoveMessage 操作