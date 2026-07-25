# evidence-source-layer Specification

> **Status:** active — 当前契约，且在目标架构中存续。 分类依据见 `docs/agentic_loop_roadmap.md`。

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

### Requirement: Official web evidence labels SHALL be bound to current query targets
The system SHALL label a web result as official only when its domain matches a configured
official domain for an entity in the current query plan, and SHALL preserve the matched
entity in metadata when known.

#### Scenario: Unrelated configured domain appears in results
- **WHEN** a result belongs to a configured official domain for an entity not named by the query
- **THEN** the result SHALL not be counted or displayed as official evidence for the query targets

#### Scenario: Target official domain appears in results
- **WHEN** a result domain matches a configured official domain for a current comparison member
- **THEN** the evidence metadata SHALL identify the matched member and classify the result as official

### Requirement: Official source tier SHALL be determined through the domain resolver
The system SHALL determine whether a web URL is `official`, `first_party`, or `unknown` for a query entity by routing ownership lookups through the official-domain resolver, with configured `pins` taking precedence over discovered results. Ownership matching SHALL be performed at host granularity with optional path prefixes: a URL is owned when its host matches a resolved official host entry (per the explicit subdomain rule) and, when the entry carries a path prefix, the URL path starts with that prefix. `classify_web_source_tier` and `official_entity_for_url` SHALL NOT consult the raw alias map except via the resolver's pin layer, and SHALL NOT collapse URLs to registrable domains for ownership decisions.

#### Scenario: Unconfigured entity resolves to official
- **WHEN** a web result's host matches a resolver-confirmed official host entry for a current query entity that has no configured pin
- **THEN** the result SHALL be classified as `official`
- **AND** the matched entity SHALL be preserved in the evidence metadata

#### Scenario: Host-level official entry does not bless the whole family
- **WHEN** `ai.google.dev` is resolver-confirmed official for an entity
- **AND** a result URL is on `google.com/search`
- **THEN** the result SHALL NOT be classified as `official` for that entity

#### Scenario: Resolver returns no official domain
- **WHEN** the resolver returns `confidence="none"` for every query entity
- **THEN** no result SHALL be classified as `official` for those entities
- **AND** results whose host label merely overlaps the entity stem MAY be classified as `first_party`

### Requirement: Confirmed official domains SHALL auto-accept well-known subdomains
The system SHALL treat a URL as `official` when its host equals a confirmed official host entry OR when its host is a well-known subdomain (`docs.`, `platform.`, `developer.`, `open.`, `api.`, `www.`, `dev.`, `developers.`, `documentation.`) or single-label subdomain of a confirmed official host entry, without requiring each subdomain to be individually configured. Acceptance SHALL use the explicit shared matcher and SHALL NOT rely on registrable-domain collapsing; hosts flagged as suspected aggregators SHALL NOT receive subdomain acceptance.

#### Scenario: Subdomain of confirmed official domain
- **WHEN** a result host is `docs.<confirmed-official-host>`
- **AND** `<confirmed-official-host>` is resolver-confirmed official for a query entity
- **THEN** the result SHALL be classified as `official` without a dedicated subdomain entry in `pins`

#### Scenario: Unrelated sibling host is not accepted
- **WHEN** `google.com` is confirmed official for a query entity
- **AND** a result host is `deepmind.google`
- **THEN** the result SHALL NOT be classified as `official` on that basis

### Requirement: Non-evidence hosts SHALL be excluded from ordinary web evidence
The system SHALL exclude URLs on `non_evidence` hosts (search engines, AI answer sites, aggregator mirrors) from ordinary web evidence in addition to never judging them official. `classify_web_source_tier` SHALL classify such URLs so the evidence pipeline can filter them before fusion, independent of any entity resolution.

#### Scenario: Search-engine URL is filtered
- **WHEN** a web result URL is on a `non_evidence` host (e.g. a search results page)
- **THEN** the result SHALL be marked for exclusion from the evidence set
- **AND** this SHALL hold even when no query entity resolves to any official host

### Requirement: Hosting-platform URLs SHALL classify official when ownership holds
The system SHALL classify a URL on a `hosting_platforms` host as `official` for a query entity when the resolver confirms an ownership relation (declared package homepage or matching repo owner), and SHALL otherwise classify it no higher than `unknown` for official-judgement purposes.

#### Scenario: Docs host with declared-homepage ownership
- **WHEN** the resolver confirms `<pkg>.readthedocs.io` as official for an entity via a declared package homepage
- **THEN** results on `<pkg>.readthedocs.io` SHALL be classified as `official`

#### Scenario: Hosting-platform URL without ownership
- **WHEN** a result is on a `hosting_platforms` host
- **AND** the resolver has no ownership relation between that host and any query entity
- **THEN** the result SHALL NOT be classified as `official`

### Requirement: Official-page enrichment SHALL rely on analyzer entities, not alias grepping
The system SHALL select official pages for extraction using entity candidates produced by the query analyzer and/or the resolver, and SHALL NOT recover entities by splitting the raw query text and matching tokens against configured alias keys.

#### Scenario: Analyzer emits no entity and resolver has no result
- **WHEN** the analyzer produces no candidate entity for the query
- **AND** the resolver returns no confirmed official domain
- **THEN** official-page extraction SHALL select no official pages
- **AND** the system SHALL NOT manufacture an entity by grepping alias keys out of the query string
