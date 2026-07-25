# web-search-provider-routing Specification

## Purpose
TBD - created by archiving change replace-serpapi-with-brightdata-and-brave. Update Purpose after archive.
## Requirements
### Requirement: Search source catalog excludes SerpAPI
The system SHALL remove SerpAPI from the configurable and user-visible general web search source catalog once this change is enabled.

#### Scenario: API rejects legacy SerpAPI source token
- **WHEN** a client submits `search_sources` containing the legacy SerpAPI source token
- **THEN** the backend SHALL reject the request as an unsupported search source

#### Scenario: Search source metadata no longer advertises SerpAPI
- **WHEN** the backend returns control or timing metadata describing configured or active search sources
- **THEN** the metadata SHALL NOT list SerpAPI as a configured, active, requested, or missing provider

### Requirement: Bright Data SERP acts as a first-class general web search provider
The system SHALL support Bright Data SERP as a general web search provider and normalize its search results into the existing `SearchHit` structure.

#### Scenario: Bright Data result normalization
- **WHEN** Bright Data returns general web search results for a query
- **THEN** the backend SHALL convert each accepted result into `title`, `url`, and `snippet` fields before passing the results to RAG, reranking, or response serialization

#### Scenario: Bright Data participates in provider metadata
- **WHEN** Bright Data is configured and available
- **THEN** the backend SHALL expose Bright Data in configured and active search source metadata using its own provider identity rather than the removed SerpAPI identity

### Requirement: General web search defaults to Brave primary
The system SHALL make Brave primary the default first-choice provider for general web search.

#### Scenario: Default general web search path
- **WHEN** a query requires general web search and the caller does not explicitly override the source set
- **THEN** the backend SHALL attempt Brave primary before any fallback general web search provider

#### Scenario: Explicit source selection constrains provider usage
- **WHEN** a caller explicitly provides a supported `search_sources` subset
- **THEN** the backend SHALL limit general web search provider selection to the supported members of that subset

### Requirement: General web search falls back in deterministic order
The system SHALL use a deterministic fallback order for general web search when the preferred provider is unavailable, rate-limited, or returns a provider-level failure.

#### Scenario: Brave secondary is used after primary failure
- **WHEN** Brave primary cannot serve a general web search request
- **THEN** the backend SHALL try the configured Brave secondary key before trying lower-priority general web search providers

#### Scenario: Non-Brave fallback only occurs after Brave paths are exhausted
- **WHEN** Brave primary and Brave secondary are both unavailable for a general web search request
- **THEN** the backend SHALL only then use the next configured fallback provider in the routing order

### Requirement: Web provider routing SHALL execute only plan-authorized provider attempts
系统 SHALL 根据 `QueryPlan` 的来源约束和用户显式 source 选择执行网页 provider，而不是让隐式特殊分支绕过这些约束。

#### Scenario: Plan authorizes priority search
- **WHEN** 计划允许默认通用网页搜索且用户未限制来源
- **THEN** 系统 SHALL 按既有确定性优先顺序执行 provider
- **AND** 每个实际尝试 SHALL 写入 `QueryExecutionTrace`

#### Scenario: Caller limits providers
- **WHEN** 调用方显式提供受支持的 `search_sources` 子集
- **THEN** 计划 SHALL 将该子集作为 provider 约束
- **AND** 未请求 provider SHALL NOT 被作为隐式 fallback 执行

### Requirement: Provider routing metadata SHALL report actual execution and fallback decisions
系统 SHALL 将 provider 库存和每个计划步骤的实际尝试分开暴露。

#### Scenario: Priority provider completes a step
- **WHEN** 优先 provider 返回满足步骤最低结果要求的结果
- **THEN** 响应和 trace SHALL 将其标记为该步骤实际执行者
- **AND** 未调用的 fallback provider SHALL 不被描述为本轮已执行

#### Scenario: Provider fallback is required
- **WHEN** provider 失败、限流或未满足步骤最低结果要求并触发 fallback
- **THEN** 系统 SHALL 记录失败原因和 fallback 决策
- **AND** 系统 SHALL 以实际执行顺序记录 fallback provider

### Requirement: Target-domain recovery SHALL continue deterministic fallback until coverage or exhaustion
For a plan-authorized target-domain recovery, the system SHALL continue through the
configured priority providers when an earlier provider returns no URL in the target's
allowed official domains.

#### Scenario: Primary provider returns only third-party pages
- **WHEN** the primary provider returns non-empty results but none is in the target official domains
- **THEN** the system SHALL record that attempt as insufficient target coverage
- **AND** it SHALL attempt the next plan-authorized provider in deterministic order

#### Scenario: Provider returns a target-domain page
- **WHEN** a provider returns at least one URL in the target official domains
- **THEN** the system SHALL retain only target-domain results for that recovery target
- **AND** it SHALL not invoke later fallback providers for that target

