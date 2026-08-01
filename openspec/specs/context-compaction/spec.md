# context-compaction Specification

## Purpose
TBD - created by archiving change add-context-compaction. Update Purpose after archive.
## Requirements
### Requirement: Token 预算会计
系统 SHALL 在 ReAct 循环运行期间持续估算下一次模型调用的输入 token 规模。估算 SHALL 以 provider 实测值为基线：取最近一次 `act` 响应 `usage_metadata` 中的 `input_tokens` 作为该时点的真实规模，其后新增消息用本地近似计数补足，并加上系统提示与输出预留。系统 SHALL 用「实测值 ÷ 同一批消息的本地近似值」维护一个会话内校准系数，用于修正后续增量估算；纯本地近似 SHALL NOT 单独作为阈值判定依据。

#### Scenario: 存在实测基线
- **WHEN** 本轮之前至少完成过一次 `act` 调用且响应携带 token usage
- **THEN** 预算估算 SHALL 以该 `input_tokens` 为基线
- **AND** 仅对基线之后新增的消息执行本地近似计数

#### Scenario: 无实测基线
- **WHEN** provider 未返回任何 token usage
- **THEN** 系统 SHALL 退回纯本地近似计数并应用默认校准系数
- **AND** 系统 SHALL NOT 因缺少 usage 而跳过阈值判定

#### Scenario: 上下文窗口取值
- **WHEN** 配置中 `per_model_window` 存在当前 `model_name` 的条目
- **THEN** 该条目 SHALL 优先于 `context_window` 默认值
- **AND** 两者都缺省时 SHALL 使用保守内置默认值

### Requirement: 分级压缩阶梯
系统 SHALL 按确定性优先的顺序分级压缩上下文：tier-1 为证据指针化与决策轨迹重建（无 LLM 调用），tier-2 为 LLM 摘要。tier-2 SHALL 仅在 tier-1 执行后预算仍超过阈值时触发。

#### Scenario: tier-1 足以回落
- **WHEN** 执行证据指针化后预算估算低于阈值
- **THEN** 系统 SHALL NOT 发起摘要 LLM 调用
- **AND** 本次压缩的摘要来源 SHALL 记为 `deterministic`

#### Scenario: tier-1 不足以回落
- **WHEN** 执行证据指针化后预算估算仍不低于阈值
- **THEN** 系统 SHALL 触发 tier-2 摘要
- **AND** 摘要输入 SHALL 包含决策轨迹与历次答案草稿，SHALL NOT 包含工具返回原文

### Requirement: 证据指针化
超出保留窗口的工具观察消息，系统 SHALL 将其正文替换为对应的 ledger 头部指针（引用编号、来源层级、抓取状态、来源 URL），并保留原消息标识以维持消息序列结构。被折叠消息的完整正文 SHALL 继续由 `EvidenceLedger` 与 `evidence_records` 持有，SHALL NOT 被删除。

#### Scenario: 折叠旧证据消息
- **WHEN** 某条工具观察消息落在保留窗口之外
- **THEN** 其内容 SHALL 被替换为不含正文的 ledger 头部指针
- **AND** 该指针 SHALL 携带可用于回灌的 `[En]` 编号

#### Scenario: 折叠不破坏证据可追溯性
- **WHEN** 上下文中的证据消息已被折叠
- **THEN** `evidence_records` 与 ledger 中该证据的记录 SHALL 保持完整
- **AND** 答案中对该 `[En]` 的引用 SHALL 仍能通过引用校验

### Requirement: 折叠证据按需回灌
系统 SHALL 提供 `recall_evidence` 工具，接受一个或多个 `[En]` 编号并返回 ledger 中对应的完整证据条目。该工具 SHALL 受独立调用预算约束，SHALL NOT 发起任何外部网络请求。

#### Scenario: 回灌已存在的证据
- **WHEN** 模型以有效 `[En]` 编号调用 `recall_evidence`
- **THEN** 工具 SHALL 返回该编号在 ledger 中的完整渲染条目
- **AND** 返回内容 SHALL NOT 引入 ledger 之外的新来源

#### Scenario: 回灌不存在的编号
- **WHEN** 请求的编号在 ledger 中不存在
- **THEN** 工具 SHALL 返回结构化的未找到结果
- **AND** 系统 SHALL NOT 因此中断循环

#### Scenario: 回灌预算耗尽
- **WHEN** `recall_evidence` 调用次数达到其预算上限
- **THEN** 工具 SHALL 返回预算耗尽的结构化拒绝结果
- **AND** 拒绝 SHALL 计入可观测指标

### Requirement: 决策轨迹重建
压缩时系统 SHALL 从 `verdicts`、`fetch_outcomes`、历史 `tool_calls` 与 `constraints_missing` 确定性地渲染一段决策轨迹并注入上下文。轨迹 SHALL 至少覆盖：已执行的检索动作、已失败且不应重试的抓取目标、critic 各轮驳回理由、当前未满足的约束、各工具预算消耗。

#### Scenario: 轨迹包含失败抓取
- **WHEN** `fetch_outcomes` 记录了抓取失败的规范化 URL
- **THEN** 渲染的轨迹 SHALL 列出该 URL 及其失败原因
- **AND** 轨迹 SHALL 标明其不应被重试

#### Scenario: 轨迹包含驳回理由
- **WHEN** 历史 `verdicts` 中存在 critic 驳回记录
- **THEN** 渲染的轨迹 SHALL 包含其 `rule_hits` 明细或 `missing_constraints`
- **AND** 渲染 SHALL NOT 依赖任何 LLM 调用

### Requirement: 压缩点与消息分区
系统 SHALL 在 `evaluate` 之后、`act` 之前执行压缩，SHALL NOT 在 `observe` 之后立即执行。消息序列 SHALL 划分为三区：首轮用户消息（固定保留）、可压缩区间、最近 `keep_recent_rounds` 轮的完整交互（逐字保留）。压缩 SHALL NOT 递增 `iteration`。

#### Scenario: 压缩发生在评估之后
- **WHEN** 预算越过阈值且本轮尚未终止
- **THEN** 系统 SHALL 先完成 `evaluate` 判定再执行压缩
- **AND** 本轮新证据 SHALL 已计入停滞检测与指纹去重后才可能被折叠

#### Scenario: 首轮用户消息不被压缩
- **WHEN** 任意一次压缩执行
- **THEN** 首轮用户消息 SHALL 原样保留

### Requirement: 压缩后消息序列结构完整
压缩产生的消息序列 SHALL 保持工具调用配对完整：任何携带 `tool_calls` 的助手消息，其每个调用标识 SHALL 在序列中存在对应的工具结果消息。切分点 SHALL 被前移至合法的工具回合边界，SHALL NOT 将一次工具回合的请求与结果分置于切分点两侧。

#### Scenario: 切分点落在工具回合中间
- **WHEN** 按保留窗口计算出的切分点会拆散一次工具回合
- **THEN** 系统 SHALL 将切分点前移至最近的合法边界

#### Scenario: 压缩后不存在孤儿工具调用
- **WHEN** 压缩在原生工具调用模式下完成
- **THEN** 结果序列中 SHALL NOT 存在没有对应工具结果消息的 `tool_calls`

### Requirement: 摘要生成约束
tier-2 摘要 SHALL 以结构化段落输出，覆盖：待答问题与硬性约束、已确认结论、已排除路径及原因、尚缺证据缺口、工具预算消耗、建议的下一步。摘要 SHALL 被约束为只能以已存在的 `[En]` 编号指代事实，SHALL NOT 引入新数值、新来源或新推论。摘要 SHALL 以用户角色消息注入，SHALL NOT 以助手角色注入。

#### Scenario: 摘要引用既有编号
- **WHEN** 摘要生成完成
- **THEN** 摘要中出现的证据编号 SHALL 全部存在于当前 ledger

#### Scenario: 摘要以用户角色注入
- **WHEN** 摘要被写回消息序列
- **THEN** 该消息 SHALL 为用户角色且带有可识别的摘要前缀
- **AND** 模型 SHALL NOT 将其视为自身既往输出

### Requirement: 压缩降级链
摘要生成失败时系统 SHALL 按序降级：LLM 摘要失败降级为确定性决策轨迹，确定性渲染失败降级为按 token 预算的尾部截断。任一级失败 SHALL NOT 中断 ReAct 循环。

#### Scenario: 摘要 LLM 调用失败
- **WHEN** 摘要模型调用抛出异常或超时
- **THEN** 系统 SHALL 使用确定性决策轨迹作为摘要内容
- **AND** 循环 SHALL 正常继续
- **AND** 摘要来源 SHALL 记为 `deterministic`

#### Scenario: 全部降级到截断
- **WHEN** 确定性渲染同样失败
- **THEN** 系统 SHALL 执行按 token 预算的尾部截断
- **AND** 摘要来源 SHALL 记为 `truncate`

### Requirement: 压缩防抖与收口
系统 SHALL 限制单次运行内的压缩次数不超过 `max_compactions_per_run`，且两次压缩之间 SHALL 至少间隔一轮迭代。当一次压缩后预算估算未低于上一次压缩时的估算，系统 SHALL NOT 再次压缩。压缩后仍超过阈值且不可再压缩时，系统 SHALL 转入强制合成收口，SHALL NOT 继续循环。

#### Scenario: 压缩次数达上限
- **WHEN** 本次运行的压缩次数已达 `max_compactions_per_run`
- **THEN** 系统 SHALL NOT 再执行压缩

#### Scenario: 压缩无效
- **WHEN** 压缩后的预算估算不低于上一次压缩时的估算
- **THEN** 系统 SHALL 停止继续压缩
- **AND** 系统 SHALL 转入强制合成并返回带不确定性说明的答案

#### Scenario: 无可压缩区间
- **WHEN** 三区切分后可压缩区间为空
- **THEN** 系统 SHALL 跳过本次压缩并标记压缩受阻
- **AND** 循环 SHALL 正常继续

### Requirement: 证据池上界
系统 SHALL 为 `evidence_pool` 设定可配置上界，超出时 SHALL 按最旧优先淘汰。淘汰 SHALL NOT 影响 `evidence_records` 与 ledger 中的对应记录。

#### Scenario: 证据池超出上界
- **WHEN** 累积的观察条目数超过配置上界
- **THEN** 最旧的条目 SHALL 被移出 `evidence_pool`
- **AND** 对应的 `evidence_records` 与 ledger 条目 SHALL 保持完整

### Requirement: 压缩可观测性
每次压缩 SHALL 产生一条工作流轨迹事件与审计记录，包含压缩前后消息数、压缩前后预算估算、摘要来源（`llm` / `deterministic` / `truncate`）。运行结果 SHALL 暴露本次运行的压缩次数与峰值上下文占比。

#### Scenario: 压缩事件进入轨迹
- **WHEN** 一次压缩完成
- **THEN** 工作流轨迹 SHALL 包含该压缩步骤及上述明细
- **AND** 审计记录 SHALL 同步记录该次压缩

#### Scenario: 结果暴露压缩指标
- **WHEN** 一次查询执行完毕
- **THEN** 返回的工作流元数据 SHALL 包含压缩次数与峰值上下文占比

### Requirement: 压缩能力开关
系统 SHALL 支持通过配置关闭上下文压缩。关闭时压缩节点 SHALL 存在于图中但永不被路由选中，运行时行为 SHALL 退回至无压缩语义。

#### Scenario: 压缩被关闭
- **WHEN** `orchestration.context_compaction.enabled` 为 false
- **THEN** 系统 SHALL NOT 执行任何证据折叠、轨迹注入或摘要调用
- **AND** 消息序列 SHALL 按追加语义原样保留

### Requirement: 手动压缩入口
系统 SHALL 提供针对指定会话的手动压缩接口。手动压缩 SHALL 复用与自动压缩相同的分区、配对完整性与降级约束，SHALL NOT 受阈值判定限制。

#### Scenario: 手动压缩已有会话
- **WHEN** 客户端对存在 checkpoint 的会话请求手动压缩
- **THEN** 系统 SHALL 执行一次压缩并将结果写回该会话 checkpoint
- **AND** 响应 SHALL 返回压缩前后消息数与摘要来源

#### Scenario: 手动压缩不存在的会话
- **WHEN** 目标会话没有 checkpoint
- **THEN** 系统 SHALL 返回明确的未找到结果
- **AND** 系统 SHALL NOT 创建新会话

