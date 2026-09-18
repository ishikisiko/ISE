# conversation-resume Specification

> **Status:** active — 当前契约，且在目标架构中存续。 分类依据见 `docs/agentic_loop_roadmap.md`。

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

### Requirement: 跨轮自主度变更不续跑同一轨迹
系统 SHALL 记录每个会话最近一轮生效的自主度。当新一轮请求的生效自主度与该记录不同时，系统 SHALL NOT 续跑既有 loop 轨迹，SHALL 以新一轮初始状态执行，并 SHALL 在控制元数据中说明发生了自主度切换导致的轨迹重置。

#### Scenario: 同一会话中切换自主度
- **WHEN** 会话存在 checkpoint 且新一轮请求的生效自主度与上一轮不同
- **THEN** 系统 SHALL 以初始状态执行本轮而非续跑
- **AND** 控制元数据 SHALL 标明本轮因自主度切换而重置

#### Scenario: 自主度不变时正常续跑
- **WHEN** 新一轮请求的生效自主度与上一轮相同且被判定为延续
- **THEN** 续跑语义 SHALL 与引入本能力之前一致
- **AND** 证据池与历史 verdicts SHALL 照常从 checkpoint 保留

#### Scenario: 会话首轮
- **WHEN** 会话尚无自主度记录
- **THEN** 本轮 SHALL 正常执行并记录其生效自主度
- **AND** SHALL NOT 被判定为切换

### Requirement: 澄清等待状态的续跑
当上一轮以模型发起的澄清结束时，系统 SHALL 把该会话的下一轮用户输入视为对该澄清的答复并续跑保留的 loop 状态，SHALL NOT 将其判定为话题重置或新问题。

#### Scenario: 用户答复澄清
- **WHEN** 会话上一轮处于澄清等待状态且用户提供了新输入
- **THEN** 系统 SHALL 以追加语义写入该输入并续跑同一轨迹
- **AND** 澄清前已获取的证据与已注入的 advisory 缺口集合 SHALL 保留

#### Scenario: 用户改问别的问题
- **WHEN** 会话处于澄清等待状态而用户显式重置会话
- **THEN** 系统 SHALL 丢弃该等待状态并按新问题执行
- **AND** 被放弃的澄清轮次 SHALL 已有完整 audit 记录

#### Scenario: 澄清等待不重置循环控制字段
- **WHEN** 系统构造澄清答复的续跑输入
- **THEN** `iteration` 与工具预算计数 SHALL 延续澄清发生时的取值而非按新一轮重置
- **AND** 该次澄清往返 SHALL NOT 使执行绕开预算上界
