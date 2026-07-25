# search-response-control Specification

> **Status:** active — 当前契约，且在目标架构中存续。 分类依据见 `docs/agentic_loop_roadmap.md`。

## Purpose
Define normalized response control metadata semantics for the default pipeline and its fallback paths.

## Requirements
### Requirement: Response control metadata SHALL use normalized search mode semantics
系统 SHALL 在默认主链路及其回退路径中统一 `control.search_mode`、`control.final_executor` 和 fallback 标记的语义，并 SHALL 以兼容方式暴露查询计划、执行摘要和验证结果。

#### Scenario: Non-search path returns normalized control metadata
- **WHEN** 查询走 direct answer、small talk、local-only 或 domain API 直出路径
- **THEN** 返回结果 SHALL 包含规范化的 `control.search_mode`
- **AND** `control.final_executor` SHALL 与实际执行路径一致

#### Scenario: Planned search path returns normalized control metadata
- **WHEN** 查询走证据计划驱动的搜索增强主链路并返回结果
- **THEN** 返回结果 SHALL 包含规范化的 `control.search_mode` 和计划/执行/验证摘要
- **AND** 搜索是否执行、实际来源元数据和验证元数据 SHALL 以一致字段暴露

### Requirement: Plan and execution metadata SHALL be additive and bounded
系统 SHALL 在 `control` 中以新增字段暴露可序列化的计划、实际步骤、证据覆盖和验证结果，同时保留现有主要响应字段。

#### Scenario: Existing caller reads a planned-search response
- **WHEN** 现有调用方继续读取 `answer`、`search_hits`、`retrieved_docs` 和 `control`
- **THEN** 系统 SHALL 继续返回这些字段
- **AND** 新增计划与 trace 字段 SHALL 不要求调用方立即迁移

#### Scenario: Metadata exceeds response or audit budget
- **WHEN** 计划、trace 或证据摘要超过配置的大小限制
- **THEN** 系统 SHALL 使用确定性裁剪并保留最终状态摘要
- **AND** 裁剪 SHALL NOT 改变 `answer`、`search_hits` 或主要兼容字段的类型

### Requirement: Response control metadata SHALL preserve compatibility fields during migration
系统 SHALL 在统一控制语义的同时保留现有主要响应字段，以避免 CLI、API 和前端在迁移期立即断裂。

#### Scenario: Existing callers parse response during migration
- **WHEN** 现有调用方继续读取 `answer`、`search_hits` 和 `control`
- **THEN** 系统 SHALL 继续返回这些字段
- **AND** 统一后的控制语义 SHALL 在兼容这些字段名的前提下提供更稳定的一致行为

#### Scenario: Fallback path returns compatibility metadata
- **WHEN** 查询经过 search unavailable 或 ReAct fallback 等回退路径
- **THEN** 系统 SHALL 保留与当前返回结构兼容的主要字段
- **AND** 系统 SHALL 明确暴露 fallback 是否触发、触发原因和最终执行者
