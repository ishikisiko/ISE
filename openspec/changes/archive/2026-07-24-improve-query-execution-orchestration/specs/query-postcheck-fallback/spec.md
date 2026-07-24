## MODIFIED Requirements

### Requirement: Default pipeline SHALL run post-check before returning search answers
系统 SHALL 在有证据计划的默认搜索主链路生成首答后执行 plan-aware 验证，再决定直接返回、执行受限恢复、要求澄清或返回证据不足状态。

#### Scenario: Search answer satisfies the plan
- **WHEN** 默认搜索主链路生成首答且验证判定其满足计划约束和证据策略
- **THEN** 系统 SHALL 直接返回首答
- **AND** 返回结果 SHALL 记录验证 verdict 和最终执行路径为默认主链路

#### Scenario: Non-search paths can skip full verification
- **WHEN** 查询走 small talk、直接回答、纯 local-only 或纯领域 API 直出等没有证据计划的路径
- **THEN** 系统 SHALL 支持跳过或短路完整 plan-aware 验证
- **AND** 返回结果 SHALL 明确记录未执行完整验证的原因

### Requirement: Post-check SHALL use rule-based screening before LLM judging
系统 SHALL 先使用规则将 draft answer、证据账本和 `QueryPlan` 对照，以识别缺失约束、覆盖不足、来源策略失败或其他高风险状态，再决定是否调用 LLM judge。

#### Scenario: Plan constraint failure produces a typed verdict
- **WHEN** 回答遗漏比较成员、时间范围、要求的来源层级、数值支持或其他计划约束
- **THEN** 系统 SHALL 产生对应的 failure types 和 missing constraints
- **AND** verdict SHALL 标明该失败是可恢复、需要澄清还是证据不足

#### Scenario: No structural rule failure is found
- **WHEN** 回答和证据账本满足全部计划约束
- **THEN** 系统 SHALL 将回答标记为规则通过
- **AND** 系统 SHALL 仅按配置和风险条件调用可选 LLM judge

## ADDED Requirements

### Requirement: Verification SHALL direct bounded recovery or clarification from the plan
系统 SHALL 根据 typed verification outcome 决定后续动作，且不得以无约束重复检索掩盖证据缺口。

#### Scenario: Recoverable evidence gap remains within budget
- **WHEN** verdict 识别到可由一个计划允许的受限步骤补齐的证据缺口，且预算尚未耗尽
- **THEN** 系统 SHALL 执行该恢复步骤并重新验证
- **AND** trace SHALL 记录恢复依据和结果

#### Scenario: Critical ambiguity blocks recovery
- **WHEN** verdict 识别到关键实体或约束歧义，且无法安全形成恢复步骤
- **THEN** 系统 SHALL 返回 `clarification_required` 或等价状态
- **AND** 系统 SHALL NOT 使用更多无约束网页搜索代替澄清
