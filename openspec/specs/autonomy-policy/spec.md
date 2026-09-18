# autonomy-policy

## Purpose

自主度策略（`AutonomyPolicy`）是 agentic loop 中规则强度的唯一载体：`guided` / `autonomous` 两个预设决定 checklist 注入、critic 与引用校验的绑定性、judge、叙述守卫、强制合成、澄清归属与预算组；解析优先级为请求 > 配置 > 默认，且 preflight 与预算上界在任何自主度下均存在。

## Requirements

### Requirement: 自主度策略是唯一的规则强度载体
系统 SHALL 提供 `AutonomyPolicy`，作为 agentic loop 中全部规则强度的唯一来源，至少包含字段：`checklist_injection`（`enforce` | `hint`）、`critic_verdict`（`binding` | `advisory`）、`citation_check`（`binding` | `advisory`）、`judge_enabled`、`narration_guard`、`forced_synthesis`、`clarification_owner`（`system` | `model`）、`budgets`。loop 的实现 SHALL NOT 在策略之外硬编码任何等价的规则强度判断。

#### Scenario: 策略字段被实际消费
- **WHEN** loop 在某一节点需要判断某条规则是否绑定模型行为
- **THEN** 该判断 SHALL 取自当前生效的 `AutonomyPolicy` 字段
- **AND** 系统 SHALL NOT 存在绕过策略直接读取配置或常量的等价分支

#### Scenario: 单一执行器与单一裁判不变
- **WHEN** 任一自主度预设生效
- **THEN** 执行 SHALL 走同一个 LangGraph 图与同一个 `ReactAgentOrchestrator`
- **AND** `evaluate_termination` SHALL 是系统中唯一被调用的 termination critic

### Requirement: guided 与 autonomous 两个预设
系统 SHALL 提供 `guided` 与 `autonomous` 两个命名预设。`guided` SHALL 是默认值，其字段取值 SHALL 使执行行为与引入本能力之前一致。`autonomous` SHALL 取 `checklist_injection=hint`、`critic_verdict=advisory`、`citation_check=advisory`、`judge_enabled=false`、`narration_guard=off`、`forced_synthesis=off`、`clarification_owner=model`，并使用独立的放宽预算组。

#### Scenario: 默认配置下行为不变
- **WHEN** 配置中不存在 `autonomy` 块，或 `autonomy.mode` 为 `guided`
- **THEN** loop SHALL 按引入本能力之前的规则强度执行
- **AND** 任何仅在 `autonomous` 下才成立的分支 SHALL NOT 被选中

#### Scenario: autonomous 预设生效
- **WHEN** `autonomy.mode` 解析为 `autonomous`
- **THEN** checklist SHALL 作为提示注入而非终止条件
- **AND** 确定性 critic 与引用校验的结论 SHALL NOT 驳回模型的候选终答
- **AND** 系统 SHALL NOT 发起 termination judge 调用

#### Scenario: 未知模式名
- **WHEN** 配置或请求给出未定义的自主度名称
- **THEN** 系统 SHALL 以明确错误拒绝该取值
- **AND** SHALL NOT 静默降级为默认预设

### Requirement: 策略解析优先级与显式性
生效策略 SHALL 按「单次请求参数 > 配置文件 `autonomy.mode` > 默认 `guided`」的优先级解析，预设字段 SHALL 可被 `autonomy.profiles.<mode>` 覆盖。自主度 SHALL NOT 由查询内容、查询分类或任何模型判断推断得出。

#### Scenario: 请求覆盖配置
- **WHEN** 配置为 `guided` 而单次请求显式指定 `autonomous`
- **THEN** 本次执行 SHALL 使用 `autonomous`
- **AND** 该覆盖 SHALL NOT 影响其他并发请求或后续默认值

#### Scenario: 预设字段被局部覆盖
- **WHEN** `autonomy.profiles.autonomous` 覆盖了部分字段
- **THEN** 生效策略 SHALL 是预设与覆盖的合并结果
- **AND** 未被覆盖的字段 SHALL 保留预设取值

#### Scenario: 禁止内容推断
- **WHEN** 系统需要决定本次执行的自主度
- **THEN** 决定 SHALL 只依据请求参数与配置
- **AND** SHALL NOT 依据查询文本、`QueryAnalysis` 结论或任何 LLM 输出

### Requirement: advisory 裁决语义
当 `critic_verdict` 或 `citation_check` 取 `advisory` 时，对应的确定性规则 SHALL 照常完整执行并产出结论，但 SHALL NOT 驳回模型的候选终答、SHALL NOT 触发返工反馈循环、SHALL NOT 改变终止原因。结论 SHALL 以系统观察的形式注入模型上下文，且每条具体缺口 SHALL 在单次执行中最多注入一次。

#### Scenario: 候选终答携带未标注来源的数值
- **WHEN** `citation_check=advisory` 且模型的候选终答包含无 `[En]` 支撑的数值
- **THEN** 该终答 SHALL 被接受为最终答案
- **AND** 缺口 SHALL 作为一条观察注入上下文供模型自行判断
- **AND** 该缺口 SHALL 进入 verdict 的 advisory 字段与执行轨迹

#### Scenario: 同一缺口重复出现
- **WHEN** 同一 constraint 或同一条引用失败在后续轮次再次被规则命中
- **THEN** 系统 SHALL NOT 第二次注入该缺口的观察
- **AND** 该缺口 SHALL 仍照常记录进 verdict

#### Scenario: advisory 下的终止原因
- **WHEN** `critic_verdict=advisory` 且模型提议输出最终答案
- **THEN** 终止原因 SHALL 反映模型自主收尾，而非确定性规则通过
- **AND** 规则计算出的缺口 SHALL 与该终止原因一并记录

### Requirement: preflight 在任何自主度下均为绑定
`AutonomyPolicy` SHALL NOT 提供关闭或降级 skill `preflight` 的取值。任何自主度下，外部调用的参数合法性 SHALL 由确定性 preflight 裁决，被拒绝的调用 SHALL NOT 发起 provider 请求。

#### Scenario: autonomous 下 preflight 拒绝
- **WHEN** `autonomous` 生效且模型以不合法参数调用某个 registry skill
- **THEN** preflight SHALL 拒绝该调用并返回结构化 reason
- **AND** SHALL NOT 发生任何外部 provider 调用
- **AND** 模型 SHALL 可依据 reason 改写参数或改用其他工具

### Requirement: 预算上界在任何自主度下存在
每个自主度预设 SHALL 定义有限的迭代上界、每工具调用上界与 token 预算。`autonomous` 放宽的 SHALL 是上界数值而非上界的存在性。缺失、非正或不可解析的预算取值 SHALL 回退到该预设的内置默认值而非视为无限。

#### Scenario: 配置给出无效预算
- **WHEN** `autonomy.profiles.autonomous.budgets.max_iterations` 为 0、负数或不可解析
- **THEN** 系统 SHALL 使用该预设的内置默认上界
- **AND** SHALL 记录一条配置降级说明

#### Scenario: autonomous 预算耗尽
- **WHEN** `autonomous` 执行到达迭代上界且模型仍未收尾
- **THEN** 循环 SHALL 终止并暴露预算耗尽的终止原因
- **AND** 已获取的证据 SHALL 参与最终答案的生成

### Requirement: 生效策略可观测
系统 SHALL 在响应控制元数据与执行轨迹中暴露本次执行实际生效的自主度名称、策略来源（请求 / 配置 / 默认）与 advisory 缺口计数。两个自主度下 audit 记录的完整性 SHALL 一致。

#### Scenario: 响应回显生效模式
- **WHEN** 任一自主度下的查询完成
- **THEN** 控制元数据 SHALL 包含生效自主度名称与其来源
- **AND** 调用方 SHALL 可据此确认请求覆盖是否被采纳

#### Scenario: advisory 缺口进入留痕
- **WHEN** advisory 规则在执行中命中了缺口
- **THEN** 缺口的类型与数量 SHALL 进入 verdict、执行轨迹与 audit 记录
- **AND** 记录 SHALL 有界且不包含模型推理原文

### Requirement: 自主度可离线对比
基线评测入口 SHALL 支持按自主度分别运行同一批样本并分目录落盘，且 SHALL 支持对齐同一样本产出两模式的差异摘要（答案质量、时延、token、工具调用数、advisory 缺口）。

#### Scenario: 分模式跑分
- **WHEN** 以指定自主度运行基线评测
- **THEN** 结果 SHALL 落入按自主度区分的目录
- **AND** 每条样本记录 SHALL 包含该次执行的生效自主度

#### Scenario: 开放式任务样本
- **WHEN** 评测集包含开放式任务样本
- **THEN** 两个自主度 SHALL 均可在该样本集上运行并产出可比较记录
- **AND** 对比摘要 SHALL 同时给出质量与成本两侧的差异
