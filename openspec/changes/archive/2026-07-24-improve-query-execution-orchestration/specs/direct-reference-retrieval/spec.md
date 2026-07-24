## ADDED Requirements

### Requirement: Direct reference retrieval SHALL use configured page-extraction adapters
系统 SHALL 将已选定的公开 URL 作为独立的 `direct_reference` 执行步骤，而不是将 URL 伪装为搜索查询或搜索 provider。

#### Scenario: Parallel Extract configuration is available
- **WHEN** `parellel2` 包含已配置的 API key 或包含 `api_key` 的设置对象
- **THEN** 系统 SHALL 配置 Parallel Extract adapter，默认调用 `https://api.parallel.ai/v1/extract`
- **AND** adapter SHALL 使用 `x-api-key`、`urls` 和可选的提取 objective
- **AND** adapter SHALL 归一化成功 URL、标题、内容或 excerpts、发布时间和逐 URL 失败信息

#### Scenario: Firecrawl Scrape configuration is available
- **WHEN** `firecrawl2` 包含已配置的 API key 或包含 `api_key` 的设置对象
- **THEN** 系统 SHALL 配置 Firecrawl Scrape adapter，默认调用 `https://api.firecrawl.dev/v2/scrape`
- **AND** adapter SHALL 使用 Bearer authentication 和 markdown 主内容抓取
- **AND** adapter SHALL 显式保持 TLS verification，不转发调用方 cookies、headers 或浏览器 actions

### Requirement: Direct-reference provider fallback SHALL be bounded and observable
系统 SHALL 按配置顺序尝试页面提取 provider，并仅在前一 provider 未返回该 URL 的可用内容时使用 fallback。

#### Scenario: Primary extractor returns usable content
- **WHEN** 首选 provider 为 URL 返回非空归一化内容
- **THEN** 系统 SHALL 将该 provider 记录为实际执行者
- **AND** 系统 SHALL NOT 为同一 URL 额外调用 fallback provider

#### Scenario: Primary extractor fails for a URL
- **WHEN** 首选 provider 返回请求失败、逐 URL 错误或空内容
- **THEN** 系统 SHALL 记录简明失败原因
- **AND** 系统 SHALL 仅在尚有配置 provider 和预算时尝试下一 provider

### Requirement: Direct-reference records SHALL remain secret-safe
系统 SHALL 在归一化结果、trace 和审计中保留必要的 URL、provider、状态、内容大小和 provider request ID，但 SHALL NOT 序列化 API key、Authorization header、完整原始响应或调用方敏感 headers。

#### Scenario: Provider response contains opaque metadata
- **WHEN** provider 返回未建模的 metadata、诊断内容或错误正文
- **THEN** 系统 SHALL 仅保留明确允许的稳定字段
- **AND** 未建模 payload SHALL NOT 进入响应控制数据或过程审计
