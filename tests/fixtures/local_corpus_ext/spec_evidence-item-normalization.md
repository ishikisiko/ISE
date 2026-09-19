# evidence-item-normalization Specification

> **Status:** active — 当前契约，且在目标架构中存续。 分类依据见 `docs/agentic_loop_roadmap.md`。

## Purpose
TBD - created by archiving change unify-evidence-sources. Update Purpose after archive.
## Requirements
### Requirement: System SHALL normalize evidence from all first-class sources into EvidenceItem records
系统 SHALL 将来自 `web`、`domain`、`local` 的原始检索结果统一归一化为 `EvidenceItem` 记录，再进入后续过滤、重排和回答拼装流程。

#### Scenario: Web search results become EvidenceItems
- **WHEN** 网页搜索返回 `SearchHit` 列表
- **THEN** 系统 SHALL 将每条结果归一化为 `EvidenceItem`
- **AND** 归一化结果 SHALL 保留标题、摘要、引用地址和来源标识

#### Scenario: Local document chunks become EvidenceItems
- **WHEN** 本地向量检索返回文档片段
- **THEN** 系统 SHALL 将每个片段归一化为 `EvidenceItem`
- **AND** 归一化结果 SHALL 保留文档来源、片段内容和必要的检索元数据

#### Scenario: Skill evidence becomes EvidenceItems
- **WHEN** registered skill 返回结构化数据或回答摘要
- **THEN** 系统 SHALL 将可用于回答和引用的部分归一化为 `EvidenceItem`
- **AND** 归一化结果 SHALL 允许通过 metadata 保留结构化字段

### Requirement: EvidenceItem SHALL provide a shared minimum schema across source types
系统 SHALL 为 `EvidenceItem` 提供跨来源共享的最小字段集合，以支持统一处理和兼容输出投影。

#### Scenario: Cross-source evidence shares required fields
- **WHEN** 系统构造任意一个 `EvidenceItem`
- **THEN** 该记录 SHALL 至少包含 `source_type`、`source_id`、可展示的内容字段和引用字段或其等价表示
- **AND** 该记录 SHALL 允许附带排序分数、rank 或原始来源 metadata

#### Scenario: Source-specific detail remains available without breaking the shared schema
- **WHEN** 某类来源需要暴露特有字段，例如 skill 的结构化数据或本地文档 chunk 信息
- **THEN** 系统 SHALL 将这些信息保存在 `EvidenceItem` 的扩展 metadata 中
- **AND** 系统 SHALL 不要求所有来源共享完全相同的细节字段

### Requirement: Compatibility views SHALL be derived from EvidenceItem records
系统 SHALL 将 `search_hits`、`retrieved_docs` 等现有兼容字段视为从 `EvidenceItem` 投影出的兼容视图，而不是继续作为独立内部真源。

#### Scenario: Web compatibility output is derived from web EvidenceItems
- **WHEN** 返回结果需要继续暴露 `search_hits`
- **THEN** 系统 SHALL 从 `source_type=web` 的 `EvidenceItem` 生成兼容输出
- **AND** 兼容输出 SHALL 与统一来源层中的证据集合保持一致

#### Scenario: Local compatibility output is derived from local EvidenceItems
- **WHEN** 返回结果需要继续暴露 `retrieved_docs`
- **THEN** 系统 SHALL 从 `source_type=local` 的 `EvidenceItem` 生成兼容输出
- **AND** 系统 SHALL 保持本地文档来源信息可引用
