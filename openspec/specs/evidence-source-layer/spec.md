# evidence-source-layer Specification

## Purpose
TBD - created by archiving change unify-evidence-sources. Update Purpose after archive.
## Requirements
### Requirement: System SHALL expose a unified EvidenceSource interface for first-class evidence retrieval
系统 SHALL 为默认主链路中的一级证据来源提供统一的 `EvidenceSource` 接口，至少覆盖 `web`、`domain`、`local` 三类来源。

#### Scenario: Web search source is represented as an EvidenceSource
- **WHEN** 系统需要从网页搜索 provider 获取证据
- **THEN** 系统 SHALL 通过 `source_type=web` 的 `EvidenceSource` 执行检索
- **AND** 该来源 SHALL 暴露稳定的 `source_id` 和显示名称以供观测与调试

#### Scenario: Local document retrieval is represented as an EvidenceSource
- **WHEN** 系统需要从上传目录或本地文档目录检索证据
- **THEN** 系统 SHALL 通过 `source_type=local` 的 `EvidenceSource` 执行检索
- **AND** 系统 SHALL NOT 要求本地来源伪装为 `SearchClient` provider

#### Scenario: Domain API retrieval is represented as an EvidenceSource
- **WHEN** 查询需要 weather、finance、sports 或其他领域 API 提供的结构化证据
- **THEN** 系统 SHALL 通过 `source_type=domain` 的 `EvidenceSource` 获取证据
- **AND** 该来源 SHALL 能与 web/local 来源一起进入统一后处理流程

### Requirement: EvidenceSource retrieval SHALL be selectively enabled by the orchestrator
系统 SHALL 允许默认主编排器按查询条件启用或禁用特定 EvidenceSource，而不是通过切换完全不同的 pipeline 来改变来源集合。

#### Scenario: Search-disabled query enables local-only evidence retrieval
- **WHEN** 查询在 `allow_search=false` 条件下执行
- **THEN** 默认主编排器 SHALL 禁用 `web` 类型来源
- **AND** 默认主编排器 SHALL 继续允许 `local` 类型来源参与检索

#### Scenario: Domain-assisted query enables multiple source types
- **WHEN** 领域 API 返回需要继续搜索或继续补证据的结果
- **THEN** 默认主编排器 SHALL 能同时启用 `domain` 和其他来源类型
- **AND** 系统 SHALL 不要求领域数据只能旁路注入字符串上下文

### Requirement: Response metadata SHALL expose active evidence source semantics
系统 SHALL 在默认主链路与 fallback 路径中暴露统一的来源元数据语义，以说明哪些一级来源被计划启用、哪些实际执行，以及哪些独立证据引用被最终使用。

#### Scenario: Default pipeline reports planned and executed evidence sources
- **WHEN** 默认主链路完成一次回答
- **THEN** 返回结果 SHALL 标识计划启用的一级来源类型和来源标识
- **AND** 返回结果 SHALL 将实际执行的来源或 provider 与仅配置或可选的来源区分开

#### Scenario: Response reports distinct final evidence references
- **WHEN** 默认主链路使用多个证据项生成回答
- **THEN** `evidence_sources_used` SHALL 保留每个最终使用的规范化 URL 或等价唯一引用
- **AND** 系统 SHALL NOT 因共享 aggregate client identity 将多个引用折叠为单个来源记录

#### Scenario: Fallback pipeline reports reused evidence source types
- **WHEN** ReAct fallback 或高层恢复工具复用统一来源层
- **THEN** 返回结果或工具输出 SHALL 能说明复用了哪些来源类型和最终证据引用
- **AND** 该元数据 SHALL 与默认主链路使用兼容的来源语义

### Requirement: EvidenceSource output SHALL preserve plan and policy provenance
系统 SHALL 让归一化证据携带来源层级、原始计划步骤和可用于策略验证的元数据，而不改变其通用 `EvidenceItem` 接口。

#### Scenario: Web result enters the ledger
- **WHEN** 网页搜索结果被归一化为 `EvidenceItem`
- **THEN** 系统 SHALL 保留其规范化引用、执行步骤和来源层级或等价策略标记
- **AND** 该信息 SHALL 可供融合、验证和 trace 使用

#### Scenario: Domain or local result enters the ledger
- **WHEN** 领域 API 或本地文档结果被归一化为 `EvidenceItem`
- **THEN** 系统 SHALL 保留其来源身份和产生它的计划步骤
- **AND** 系统 SHALL 允许其与网页证据共同满足计划约束

