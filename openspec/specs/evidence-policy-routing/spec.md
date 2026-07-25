# evidence-policy-routing Specification

> **Status:** superseded by roadmap M5 — 该能力将在对应里程碑删除，不要新增 requirement 或加固；仅接受阻断性缺陷的最小修复。 分类依据见 `docs/agentic_loop_roadmap.md`。

## Purpose
TBD - created by archiving change improve-query-execution-orchestration. Update Purpose after archive.

## Requirements
### Requirement: System SHALL derive evidence policies from query constraints and claim classes
系统 SHALL 根据 `QueryAnalysis` 选择可组合的证据策略，而不是仅根据单一领域标签或关键词决定检索行为。

#### Scenario: Numeric or current claim requires authority
- **WHEN** 计划包含当前、数值、价格、合规或其他需要权威性的声明
- **THEN** 系统 SHALL 应用来源权威性策略
- **AND** 该策略 SHALL 定义可接受的来源层级和未满足时的结果状态

#### Scenario: Comparison requires membership coverage
- **WHEN** 计划包含两个或更多比较成员
- **THEN** 系统 SHALL 应用比较覆盖策略
- **AND** 验证阶段 SHALL 能识别每个成员是否被证据支持、证据不足或需要澄清

#### Scenario: Historical coverage is explicitly requested
- **WHEN** 计划包含明确时间范围、历史或趋势约束
- **THEN** 系统 SHALL 应用时间覆盖策略并定义所需时间范围
- **AND** 该策略 SHALL 可以请求受预算限制的补充检索

### Requirement: Generic lexical cues SHALL NOT select specialized evidence policies alone
系统 SHALL 要求与策略相匹配的结构化约束，且不得仅凭通用词语触发专门的时间、权威或比较检索行为。

#### Scenario: Bare comparison text has no temporal constraint
- **WHEN** 查询只包含 `对比`、`比较`、`compare` 或 `comparison` 等比较词
- **THEN** 系统 SHALL 创建比较覆盖约束而不创建时间覆盖约束
- **AND** 系统 SHALL NOT 触发按年份的补充检索

#### Scenario: Explicit temporal comparison selects both policies
- **WHEN** 查询同时包含比较成员和明确的跨年份、历史或趋势约束
- **THEN** 系统 SHALL 同时应用比较覆盖和时间覆盖策略
- **AND** 计划 SHALL 记录它们各自的证据目标与预算

### Requirement: Evidence policy SHALL classify source acceptance independently of provider availability
系统 SHALL 将“可调用 provider”与“可接受为某项声明证据的来源层级”分开判断。受限（limited）证据不得单独作为已验证声明的支持依据，但在没有任何满足策略的证据时，可以作为明确标注不确定性的受限作答依据，并交由 post-check 与验证终判。

#### Scenario: Available source does not meet claim authority
- **WHEN** provider 返回可用网页结果但其来源层级不满足计划的权威策略
- **THEN** 系统 SHALL 将该结果作为受限上下文或拒绝证据处理
- **AND** 系统 SHALL NOT 将其单独作为已验证声明的支持依据

#### Scenario: Limited evidence supports a qualified answer when nothing better exists
- **WHEN** 恢复循环结束后仍无满足权威策略的 retained 证据，但存在 limited 证据
- **THEN** 系统 SHALL 允许基于 limited 证据生成明确标注来源局限与不确定性的回答
- **AND** 该回答 SHALL 通过 post-check 或验证终判，而不是在生成前被静默替换为证据不足

#### Scenario: No evidence at all remains a hard stop
- **WHEN** 账本中既无 retained 也无 limited 证据
- **THEN** 系统 SHALL 直接产出证据不足终态
- **AND** 系统 SHALL NOT 调用模型生成无依据的回答

### Requirement: System SHALL assign source tiers to web evidence
系统 SHALL 在 web 证据进入证据账本前为其判定来源层级，而不是将全部网页结果无条件标记为 `unknown`。对比较成员或查询实体的一方/官方来源（如品牌官网、官方文档、官方定价页）SHALL 标记为 `first_party` 或 `official`；无法判定的结果保持 `unknown` 层级。

#### Scenario: Official source for a comparison member is tiered
- **WHEN** web 搜索结果的来源域名属于某个比较成员或查询实体的官方网站
- **THEN** 该证据 SHALL 以 `first_party` 或 `official` 层级写入证据账本
- **AND** authority 策略 SHALL 将其视为可接受来源

#### Scenario: Unclassifiable web result remains unknown
- **WHEN** web 搜索结果的来源无法与任何查询实体的官方来源建立对应
- **THEN** 该证据 SHALL 保持 `unknown` 层级
- **AND** 系统 SHALL NOT 仅凭猜测提升其层级

### Requirement: Authority-tier gaps SHALL be recoverable before final insufficiency
当证据账本存在证据但没有任何条目满足 authority 策略时，验证 SHALL 产出 `RECOVERABLE_GAP`（缺失约束含 `authority`）而不是直接产出 `EVIDENCE_INSUFFICIENT`，以驱动指向官方来源的查询改写恢复；仅当恢复预算耗尽或恢复未改善覆盖时，SHALL 才产出终态 `EVIDENCE_INSUFFICIENT`。

#### Scenario: Evidence exists but none meets the authority policy
- **WHEN** 账本中存在 retained 或 limited 证据但无一条满足 authority 策略的接受层级
- **AND** 计划仍有剩余恢复预算
- **THEN** 验证 SHALL 返回 `RECOVERABLE_GAP` 且缺失约束包含 `authority`
- **AND** 系统 SHALL NOT 直接以证据不足终态返回

#### Scenario: Recovery budget exhausted with authority still unmet
- **WHEN** 恢复预算已用尽且仍无满足 authority 策略的证据
- **THEN** 验证 SHALL 返回 `EVIDENCE_INSUFFICIENT`
- **AND** 失败类型 SHALL 记录 `recovery_budget_exhausted`

### Requirement: Official coverage SHALL be evaluated per configured comparison target
The evidence policy SHALL require retained official evidence with a pricing signal for
every mapped target before classifying an authority-required pricing comparison with
configured official-domain targets as verified.

#### Scenario: One target remains without official evidence
- **WHEN** a pricing comparison has official evidence for one mapped member but not another
- **THEN** verification SHALL report the missing target as an authority/comparison gap
- **AND** the response SHALL not present a normal verified price comparison

#### Scenario: All mapped targets have official evidence
- **WHEN** every mapped comparison member has retained official evidence with a pricing signal
- **THEN** verification SHALL allow normal answer generation subject to the remaining plan policies

#### Scenario: Official homepage lacks pricing information
- **WHEN** a target-domain result is an official homepage but its retained content lacks a pricing signal
- **THEN** verification SHALL keep that target missing for the pricing comparison
