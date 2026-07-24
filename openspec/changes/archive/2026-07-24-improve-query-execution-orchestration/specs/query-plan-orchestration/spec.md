## ADDED Requirements

### Requirement: System SHALL create a shared query analysis before evidence execution
系统 SHALL 在默认搜索路径执行来源选择或检索前创建结构化 `QueryAnalysis`，至少表达意图形态、实体、歧义、时间/新鲜度约束、声明类别、比较成员和是否允许搜索。

#### Scenario: Query contains multiple constraints
- **WHEN** 用户问题同时包含比较、实体、时间、新鲜度或数值声明等多个约束
- **THEN** 系统 SHALL 将这些约束保存在同一 `QueryAnalysis` 中
- **AND** 后续路由、检索、融合和验证 SHALL 使用该分析而不是各自重新从原始文本推断

#### Scenario: Query has no evidence requirement
- **WHEN** 分析表明问题可由 direct answer 或 local-only 路径完整处理
- **THEN** 系统 SHALL 标记不需要外部证据计划
- **AND** 系统 SHALL 保留现有快速路径语义

### Requirement: System SHALL derive a bounded query plan from the analysis
系统 SHALL 将需要证据的分析转换为 `QueryPlan`，其中包含证据策略、按顺序执行的步骤、每步目的、允许来源、查询标识以及查询和结果预算。

#### Scenario: Search-enabled query receives a plan
- **WHEN** `QueryAnalysis` 判定需要外部或领域证据
- **THEN** 系统 SHALL 创建带有至少一个明确用途的计划步骤
- **AND** 每个步骤 SHALL 受该计划的查询和结果预算约束

#### Scenario: Existing domain data is appropriate
- **WHEN** 分析和证据策略要求现有 weather、transportation、finance 或 sports 等领域数据
- **THEN** 系统 SHALL 将领域 API 调用表示为显式计划步骤
- **AND** 该步骤的结果 SHALL 进入同一证据账本

### Requirement: Critical ambiguity SHALL be resolved at the planning boundary
系统 SHALL 在无法安全形成证据计划时产生澄清状态，而不是猜测实体、来源或约束。

#### Scenario: Required entity is ambiguous
- **WHEN** 用户要求比较、核验或获取数值声明，但关键实体不能唯一解析
- **THEN** 系统 SHALL 生成 `clarification_required` 计划结果
- **AND** 系统 SHALL NOT 以猜测的实体或来源执行证据检索

#### Scenario: Non-critical ambiguity does not block a plan
- **WHEN** 歧义不影响用户请求的核心实体、声明或证据标准
- **THEN** 系统 SHALL 在计划中记录该歧义
- **AND** 系统 SHALL 继续执行满足其余约束的受限计划
