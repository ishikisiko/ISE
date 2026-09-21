## MODIFIED Requirements

### Requirement: 停滞检测
系统 SHALL 检测三类停滞信号并在持续超阈值时判定循环停滞：工具调用指纹重复（相同工具名与归一化参数）、发起了工具调用却连续无新证据、连续不发起任何工具调用且候选答案未被接受。阈值 SHALL 可配置（`repeat_threshold`、`no_progress_threshold`、`tool_free_threshold`）。不调工具的轮次 SHALL NOT 计入"连续无新证据"计数。

#### Scenario: 重复工具调用
- **WHEN** 连续 `repeat_threshold` 轮迭代产生相同的工具调用指纹
- **THEN** 系统 SHALL 判定循环停滞并终止循环
- **AND** 终止原因 SHALL 标记为 `stagnated`

#### Scenario: 连续无新证据
- **WHEN** 连续 `no_progress_threshold` 轮发起了工具调用的迭代其 LoopVerdict 均为 `new_evidence=false`
- **THEN** 系统 SHALL 判定循环停滞并终止循环
- **AND** 终止原因 SHALL 标记为 `stagnated`

#### Scenario: 连续不调工具
- **WHEN** 连续 `tool_free_threshold` 轮迭代模型既未调用工具、其输出又未被接受为最终答案（直答被拒、过程叙述或无效工具请求）
- **THEN** 系统 SHALL 判定循环停滞并终止循环
- **AND** 终止原因 SHALL 标记为 `stagnated`
- **AND** 最终答案 SHALL 采用消息历史中最后一条非空候选答案并标注证据不足，而非仅返回终态模板

#### Scenario: 直答被拒后仍有检索机会
- **WHEN** 模型在没有任何已保留证据时直接给出答案且 critic 拒收
- **THEN** 拒收反馈 SHALL 明确要求调用搜索工具获取证据后再作答
- **AND** 该轮 SHALL NOT 增加"连续无新证据"计数
- **AND** 模型在下一轮调用工具后 SHALL 按正常路径继续评估

### Requirement: 循环终止语义
系统 SHALL 通过元数据暴露 `succeeded`、`exhausted`、`stagnated`、`unrecoverable`、`evidence_insufficient`、`clarification_required` 与 `cancelled` 终止原因。当 `critic_verdict=advisory` 时，模型提议终答 SHALL 直接被接受为终止条件，确定性缺口 SHALL NOT 触发返工。任何非成功终止的最终答案 SHALL 优先返回模型已产出的候选答案并恰好附加一次限定前缀；终态模板 SHALL 只在没有任何候选答案时出现，且 SHALL NOT 被 critic 接受为 `succeeded`。

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

#### Scenario: 限定前缀只出现一次
- **WHEN** 综合阶段产出的候选答案已带限定前缀，随后循环以非成功原因终止
- **THEN** 最终答案 SHALL 只包含一次限定前缀
- **AND** 前缀之后 SHALL 是候选答案正文

#### Scenario: 终态模板不被接受为成功
- **WHEN** 当前草稿等于某条终态模板（如"迭代次数用尽，未能获得完整答案。"）
- **THEN** critic SHALL 将其视为空答案
- **AND** 循环 SHALL NOT 以 `succeeded` 终止

### Requirement: 强制合成旁路受策略约束
当 `AutonomyPolicy.forced_synthesis` 为 off 时，系统 SHALL NOT 因接近迭代上界、终态降级或确定性工作流需要而替模型生成最终答案。此时最终答案 SHALL 只来自模型自身的输出或预算耗尽后的收口说明。当 `forced_synthesis` 为 on 且账本存在已保留证据时，任何进入综合节点的路径 SHALL 基于已保留证据生成接地答案，SHALL NOT 以终态模板替代。

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

#### Scenario: 接近上界的强制合成有证据时接地
- **WHEN** `forced_synthesis=on`、约束已满足且循环接近迭代上界而模型仍在调用工具，账本存在已保留证据
- **THEN** 综合节点 SHALL 基于已保留证据生成带 `[En]` 标注的答案
- **AND** 综合失败时 SHALL 退回消息历史中的候选答案，而非空草稿

## ADDED Requirements

### Requirement: 请求级强制搜索由 critic 裁决
当请求携带 `force_search=true` 且搜索被允许、工具面包含搜索工具时，系统 SHALL 在模型尚未发起任何搜索工具调用（`web_search` / `search_recovery`）之前拒收其最终答案，缺口记为 `search_required`。该规则 SHALL 由确定性 critic 执行，SHALL 受 `tool_free_threshold` 与迭代预算约束，SHALL NOT 替模型发起工具调用。

#### Scenario: 强制搜索下模型直答
- **WHEN** `force_search=true` 且模型在零次搜索调用后提议最终答案
- **THEN** critic SHALL 判 `final_answer_rejected`，`rule_hits` 含 `search_required`
- **AND** 拒收反馈 SHALL 明确要求调用 `web_search`
- **AND** 系统提示 SHALL 包含"先检索再作答"的成功标准

#### Scenario: 模型随后搜索
- **WHEN** 模型在被拒后调用了搜索工具
- **THEN** `search_required` 缺口 SHALL 消失
- **AND** 后续评估 SHALL 按正常规则进行

#### Scenario: 强制搜索不越过预算
- **WHEN** `force_search=true` 且模型连续 `tool_free_threshold` 轮不调用工具
- **THEN** 循环 SHALL 以 `stagnated` 终止
- **AND** SHALL NOT 无限拒收

#### Scenario: 搜索被禁用
- **WHEN** `allow_search=false` 或工具面不含搜索工具
- **THEN** `force_search` SHALL 不生效
- **AND** `control.request_parameters.force_search` SHALL 回显 false
