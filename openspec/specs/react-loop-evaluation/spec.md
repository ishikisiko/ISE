# react-loop-evaluation Specification

## Purpose
TBD - created by archiving change langgraph-react-loop. Update Purpose after archive.
## Requirements
### Requirement: LoopVerdict 结构化迭代判定
系统 SHALL 在 ReAct 循环的每次迭代后产出结构化 `LoopVerdict`，至少包含字段：`new_evidence`（本轮是否带来新证据）、`constraints_met`（已满足的约束清单）、`constraints_missing`（未满足的约束清单）、`should_continue`（是否继续迭代）、`reason`（判定原因）。

#### Scenario: 每轮迭代产出判定
- **WHEN** ReAct 循环完成一次 act → observe 迭代
- **THEN** evaluate 节点 SHALL 产出该轮的 `LoopVerdict` 并写入循环状态
- **AND** 全部轮次的 LoopVerdict SHALL 可在执行轨迹中检索

#### Scenario: 判定字段可供终止决策使用
- **WHEN** evaluate 节点完成一轮判定
- **THEN** 循环的继续/终止决策 SHALL 仅依据 LoopVerdict 与迭代预算作出
- **AND** 决策结果 SHALL 记录对应 LoopVerdict 的 `reason`

### Requirement: 规则化迭代内评估
系统 SHALL 在每轮迭代后执行零 LLM 成本的规则评估，检查约束覆盖与证据增量。规则评估 SHALL 复用 postcheck 的判定原语（约束模式匹配、数字抽取、证据文本化）。

#### Scenario: 约束覆盖检查
- **WHEN** 查询携带时间约束、对比意图或多跳分析意图
- **THEN** 规则评估 SHALL 检查当前已收集证据与答案草稿是否覆盖对应约束
- **AND** 未覆盖项 SHALL 写入 `constraints_missing`

#### Scenario: 证据增量检测
- **WHEN** 本轮 observation 与证据池已有内容的 token 重叠比例高于阈值
- **THEN** 规则评估 SHALL 判定本轮 `new_evidence` 为 false
- **AND** 连续无进展计数 SHALL 加一

### Requirement: 分级 LLM 评审
系统 SHALL 支持按配置间隔调用 LLM judge 评审循环进展，judge 间隔 SHALL 可通过配置调整（`judge_interval`），且在循环被强制终止前 SHALL 执行一次终局评审。judge SHALL 复用 postcheck judge 的 LLM 配置与 JSON 输出协议。

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
系统 SHALL 将循环终止原因细分为四类并通过元数据暴露：`succeeded`（约束满足且评估通过）、`exhausted`（迭代预算用尽）、`stagnated`（停滞检测触发）、`unrecoverable`（工具持续失败等不可恢复错误）。

#### Scenario: 成功终止
- **WHEN** 模型提议输出最终答案且 evaluate 判定约束清单为空、规则抽查通过
- **THEN** 循环 SHALL 终止并标记 `loop_status=succeeded`
- **AND** 最终答案 SHALL 被接受返回

#### Scenario: 迭代预算用尽
- **WHEN** 迭代次数达到 `max_iterations` 且约束清单仍有缺项
- **THEN** 循环 SHALL 终止并标记 `loop_status=exhausted`
- **AND** 系统 SHALL 返回当前最佳答案草稿

#### Scenario: 不可恢复失败
- **WHEN** 工具调用连续失败次数达到 `tool_error_threshold` 且期间无一次成功 observation
- **THEN** 循环 SHALL 终止并标记 `loop_status=unrecoverable`
- **AND** 系统 SHALL 保留错误原因说明

#### Scenario: 模型提前收尾被拒
- **WHEN** 模型提议输出最终答案但 evaluate 判定 `constraints_missing` 非空且剩余迭代预算大于 0
- **THEN** 循环 SHALL 继续，缺项清单 SHALL 作为反馈注入循环状态供下一轮使用

### Requirement: 显式成功标准注入
当循环作为 postcheck 回退执行时，系统 SHALL 将 postcheck verdict 的 `failure_types`、`missing_constraints` 与 `recovery_goal` 作为显式成功标准写入循环初始状态，evaluate 节点 SHALL 据此构建约束 checklist。

#### Scenario: 回退上下文转化为 checklist
- **WHEN** ReAct 循环以 fallback 上下文启动
- **THEN** 初始约束 checklist SHALL 由 postcheck 的 `failure_types` 与 `missing_constraints` 派生
- **AND** evaluate 每轮 SHALL 对照该 checklist 判定补救进展

#### Scenario: 无回退上下文
- **WHEN** ReAct 循环独立启动（无 fallback 上下文）
- **THEN** 初始 checklist SHALL 由查询自身的约束解析结果（时间约束、对比意图等）派生

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
