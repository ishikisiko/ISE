# react-loop-evaluation Specification

> **Status:** active - roadmap M5 sole-loop termination contract.

## Purpose
TBD - created by archiving change langgraph-react-loop. Update Purpose after archive.
## Requirements
### Requirement: LoopVerdict 结构化迭代判定
系统 SHALL 在 ReAct 循环的每次迭代后产出结构化 `LoopVerdict`，至少包含字段：`new_evidence`、`constraints_met`、`constraints_missing`、`should_continue`、`reason`、`action`、`deterministic_pass`、`hard_stop`、`failure_types`、`rule_hits`、`autonomy_mode` 与 `advisory_gaps`。`advisory_gaps` SHALL 记录本轮以 advisory 方式产生、未绑定模型行为的缺口及其是否已注入上下文。

#### Scenario: 每轮迭代产出判定
- **WHEN** ReAct 循环完成一次 act → observe 迭代
- **THEN** evaluate 节点 SHALL 产出该轮的 `LoopVerdict` 并写入循环状态
- **AND** 全部轮次的 LoopVerdict SHALL 可在执行轨迹中检索

#### Scenario: 判定字段可供终止决策使用
- **WHEN** evaluate 节点完成一轮判定
- **THEN** 循环的继续/终止决策 SHALL 仅依据 LoopVerdict 与迭代预算作出
- **AND** 决策结果 SHALL 记录对应 LoopVerdict 的 `reason`

#### Scenario: advisory 缺口被单独记录
- **WHEN** 生效策略把某类规则设为 advisory 且该规则命中缺口
- **THEN** 缺口 SHALL 写入 `advisory_gaps` 而非驱动 `should_continue`
- **AND** verdict SHALL 标明该缺口是否已作为观察注入上下文

### Requirement: 规则化迭代内评估
系统 SHALL 在每轮迭代后调用唯一的零 LLM 成本 termination critic，检查约束覆盖、来源等级、证据增量与预算。该 critic SHALL 在所有自主度下均被调用。loop SHALL NOT 维护第二套继续/终止规则。critic 结论是否绑定模型行为 SHALL 由 `AutonomyPolicy.critic_verdict` 决定：`binding` 时结论驱动继续/终止，`advisory` 时结论只进入 verdict 与上下文观察。

#### Scenario: 约束覆盖检查
- **WHEN** 查询携带时间约束、对比意图或多跳分析意图
- **THEN** 规则评估 SHALL 检查当前已收集证据与答案草稿是否覆盖对应约束
- **AND** 未覆盖项 SHALL 写入 `constraints_missing`

#### Scenario: 证据增量检测
- **WHEN** 本轮 observation 与证据池已有内容的 token 重叠比例高于阈值
- **THEN** 规则评估 SHALL 判定本轮 `new_evidence` 为 false
- **AND** 连续无进展计数 SHALL 加一

#### Scenario: advisory 下规则仍然执行
- **WHEN** `critic_verdict=advisory`
- **THEN** critic SHALL 照常完整计算约束覆盖、来源等级与证据增量
- **AND** 计算结果 SHALL 进入 verdict、执行轨迹与 audit
- **AND** 结果 SHALL NOT 改变循环的继续/终止决策

### Requirement: 分级 LLM 评审
系统 SHALL 支持按 `termination.judge_interval` 调用 `termination.judge` 评审循环进展，并在候选终答或非歧义强制终止前执行一次终局评审。loop SHALL 只使用该 judge 角色与 JSON 输出协议。当生效策略的 `judge_enabled` 为 false 时，系统 SHALL NOT 发起任何 judge 调用，评估 SHALL 仅依据确定性规则。

#### Scenario: judge 不能推翻确定性缺口或预算
- **WHEN** judge 输出 `passes=true`，但 critic 仍有来源等级、证据覆盖、约束或预算缺口
- **THEN** 系统 SHALL 保留确定性缺口与 hard stop
- **AND** judge SHALL NOT 清空 `constraints_missing`、延长预算或把 verdict 改成成功

#### Scenario: judge 可以否决确定性通过
- **WHEN** critic 规则通过但 judge 输出 `passes=false`
- **THEN** verdict SHALL 增加语义充分性缺口
- **AND** 仅在预算仍有余量时继续

#### Scenario: 按间隔评审
- **WHEN** 循环迭代次数达到 `judge_interval` 的整数倍且规则评估未判定终止
- **THEN** 系统 SHALL 调用 LLM judge 评审当前答案草稿与证据
- **AND** judge 结论 SHALL 合并进当轮 LoopVerdict

#### Scenario: judge 调用失败
- **WHEN** LLM judge 调用抛出异常或返回不可解析内容
- **THEN** 系统 SHALL 记录 judge 错误并退化为仅使用规则评估结果
- **AND** 循环 SHALL NOT 因 judge 失败而中断

#### Scenario: judge 成本约束
- **WHEN** 配置 `judge_interval` 大于 1
- **THEN** 非间隔轮次 SHALL NOT 发起任何 LLM 评审调用

#### Scenario: 策略关闭 judge
- **WHEN** 生效策略的 `judge_enabled` 为 false
- **THEN** 系统 SHALL NOT 在任何轮次发起 judge 调用
- **AND** verdict SHALL 标明 judge 未参与本次评估

### Requirement: 停滞检测
系统 SHALL 检测两类停滞信号并在持续超阈值时判定循环停滞：工具调用指纹重复（相同工具名与归一化参数）、连续无新证据。阈值 SHALL 可配置（`repeat_threshold`、`no_progress_threshold`）。

#### Scenario: 重复工具调用
- **WHEN** 连续 `repeat_threshold` 轮迭代产生相同的工具调用指纹
- **THEN** 系统 SHALL 判定循环停滞并终止循环
- **AND** 终止原因 SHALL 标记为 `stagnated`

#### Scenario: 连续无新证据
- **WHEN** 连续 `no_progress_threshold` 轮迭代的 LoopVerdict 均为 `new_evidence=false`
- **THEN** 系统 SHALL 判定循环停滞并终止循环
- **AND** 终止原因 SHALL 标记为 `stagnated`

### Requirement: 循环终止语义
系统 SHALL 通过元数据暴露 `succeeded`、`exhausted`、`stagnated`、`unrecoverable`、`evidence_insufficient`、`clarification_required` 与 `cancelled` 终止原因。当 `critic_verdict=advisory` 时，模型提议终答 SHALL 直接被接受为终止条件，确定性缺口 SHALL NOT 触发返工。

#### Scenario: 成功终止
- **WHEN** 模型提议输出最终答案且 evaluate 判定约束清单为空、规则抽查通过
- **THEN** 循环 SHALL 终止并标记 `loop_status=succeeded`
- **AND** 最终答案 SHALL 被接受返回

#### Scenario: 迭代预算用尽
- **WHEN** 迭代次数达到生效策略的迭代上界且约束清单仍有缺项
- **THEN** 循环 SHALL 终止并标记 `loop_status=exhausted`
- **AND** 系统 SHALL 返回当前最佳答案草稿

#### Scenario: 不可恢复失败
- **WHEN** 工具调用连续失败次数达到 `tool_error_threshold` 且期间无一次成功 observation
- **THEN** 循环 SHALL 终止并标记 `loop_status=unrecoverable`
- **AND** 系统 SHALL 保留错误原因说明

#### Scenario: 模型提前收尾被拒
- **WHEN** `critic_verdict=binding` 且模型提议输出最终答案但 evaluate 判定 `constraints_missing` 非空且剩余迭代预算大于 0
- **THEN** 循环 SHALL 继续，缺项清单 SHALL 作为反馈注入循环状态供下一轮使用

#### Scenario: advisory 下模型自主收尾
- **WHEN** `critic_verdict=advisory` 且模型提议输出最终答案
- **THEN** 循环 SHALL 终止并返回该答案
- **AND** 终止原因 SHALL 标明由模型自主收尾
- **AND** 未满足的确定性缺口 SHALL 记录在 `advisory_gaps` 中

### Requirement: 显式成功标准注入
循环 SHALL 从共享 `QueryAnalysis` 构建初始成功标准，evaluate 节点 SHALL 在每轮对照同一标准检查进展。标准是否作为终止条件 SHALL 由 `AutonomyPolicy.checklist_injection` 决定。

#### Scenario: QueryAnalysis 包含比较或时间约束
- **WHEN** 循环以结构化分析启动且 `checklist_injection=enforce`
- **THEN** 初始 checklist SHALL 由分析中的比较、时间和权威约束派生
- **AND** 模型 SHALL NOT 通过改写工具查询清除这些成功标准

#### Scenario: 标准降为提示
- **WHEN** `checklist_injection=hint`
- **THEN** 同一 checklist SHALL 仍被派生并用于每轮的覆盖度计算
- **AND** 未覆盖项 SHALL NOT 阻止模型收尾
- **AND** 未覆盖项 SHALL 记录在 `advisory_gaps` 中

### Requirement: Loop verdicts SHALL be streamed as safe iteration details
Every LangGraph ReAct evaluation SHALL complete the corresponding iteration
workflow event with its `LoopVerdict` facts. The event SHALL expose the
continue/terminate reason, evidence increment flag, and bounded met/missing
constraint summaries without exposing model reasoning or answer drafts.

#### Scenario: An iteration continues
- **WHEN** evaluation decides another iteration is required
- **THEN** the completed iteration event SHALL state the continue reason and
  missing constraints
- **AND** the next iteration SHALL begin as a separate ordered event

#### Scenario: An iteration terminates
- **WHEN** evaluation reaches succeeded, exhausted, stagnated, or
  unrecoverable termination
- **THEN** the completed iteration event SHALL identify the terminal reason
- **AND** the final result SHALL retain the existing `loop_status` and
  `loop_verdicts` metadata

### Requirement: Each verdict SHALL have one detailed presentation layer
The complete verdict items SHALL belong to the per-iteration evaluation event.
The enclosing iteration and outer-loop completion events MAY summarize status,
but SHALL NOT repeat those items.

#### Scenario: A traced iteration completes
- **WHEN** `react_evaluate_N` completes with a verdict
- **THEN** its event SHALL contain the detailed verdict facts
- **AND** `react_iteration_N` and `react_loop` SHALL not duplicate those
  verdict rows

### Requirement: Resumed responses SHALL expose current-turn verdicts only
The result and additive control metadata for a resumed request SHALL contain
only verdicts generated during that request. The checkpoint MAY retain earlier
verdicts for continuation.

#### Scenario: A conversation continuation starts after prior ReAct turns
- **WHEN** the graph resumes from a checkpoint that contains verdict history
- **THEN** current-turn `loop_verdicts` SHALL exclude the earlier turn's rows
- **AND** retained evidence and historical state SHALL remain available to the
  graph

### Requirement: 强制合成旁路受策略约束
当 `AutonomyPolicy.forced_synthesis` 为 off 时，系统 SHALL NOT 因接近迭代上界、终态降级或确定性工作流需要而替模型生成最终答案。此时最终答案 SHALL 只来自模型自身的输出或预算耗尽后的收口说明。

#### Scenario: 关闭强制合成
- **WHEN** `forced_synthesis=off` 且循环接近迭代上界但模型仍在调用工具
- **THEN** 系统 SHALL NOT 切换到强制合成
- **AND** 循环 SHALL 按预算自然终止并暴露对应终止原因

#### Scenario: 开启强制合成保持现状
- **WHEN** `forced_synthesis=on`
- **THEN** 现有的接近上界强制合成、终态降级合成与确定性价格合成路径 SHALL 保持引入本能力之前的行为

#### Scenario: 关闭时仍保留证据收口
- **WHEN** `forced_synthesis=off` 且循环因预算耗尽终止且账本存在已保留证据
- **THEN** 响应 SHALL 返回模型最后的草稿并明确标注执行预算不足
- **AND** SHALL NOT 丢弃已保留证据

### Requirement: advisory 缺口的注入去重
系统 SHALL 保证同一条 advisory 缺口在单次执行中最多被注入一次模型上下文，并 SHALL 在循环状态中记录已注入集合以支持跨轮去重。

#### Scenario: 缺口首次命中
- **WHEN** 某条 advisory 缺口首次被规则命中
- **THEN** 系统 SHALL 以有界观察形式注入该缺口
- **AND** SHALL 把该缺口标记为已注入

#### Scenario: 缺口再次命中
- **WHEN** 同一缺口在后续轮次再次被命中
- **THEN** 系统 SHALL NOT 重复注入
- **AND** verdict SHALL 仍记录该缺口本轮仍然存在

#### Scenario: 续跑保留已注入集合
- **WHEN** 会话续跑同一条 loop 轨迹
- **THEN** 已注入集合 SHALL 从 checkpoint 保留
- **AND** 上一轮已注入的缺口 SHALL NOT 在新一轮被重复注入
