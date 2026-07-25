# Proposal: fix-evidence-recovery-loop

## Why

一次普通的定价对比查询（"对比fable5 api价格和glm5.2,kimik3"）在拿到 5 条真实搜索结果的情况下，最终只回复"证据不足"。根因不是单点 bug，而是三处结构性断点叠加：

1. **authority 策略对纯 web 证据永远无法满足**：`claim_classes` 含 numeric/current/compliance 时计划会附加 authority policy（只接受 `official/first_party/authoritative`），但所有 web 证据的 `source_tier` 被硬编码为 `"unknown"`（langchain_rag.py:579/596/672），导致全部证据被判 `limited`、0 条 retained，preflight 直接以 `EVIDENCE_INSUFFICIENT` 早退——连 LLM 回答的机会都没有。任何"价格/费用/最新/合规"类查询都会踩中这个死局。
2. **恢复机制只有骨架没有接线**：`recovery_budget`、`RECOVERABLE_GAP`、`next_action="recover"` 均已存在，但 `recoverable=True` 的唯一条件是计划含 `TEMPORAL_RECOVERY` 步（query_orchestration.py:862,923），且没有任何执行器响应 `recover` 动作去做换词重搜。非 temporal 查询的恢复预算是死的。
3. **关键词生成静默失败**：LLM 调用异常被吞掉后退回实体拼接 query（丢失"价格/pricing"意图），但 tracer 与控制面均不显示失败事实，排障时无法区分"LLM 生成了烂词"与"LLM 根本没跑"。

顺带发现：uploads 目录为空时仍每请求花费约 9.5s 构建本地向量索引（langchain_rag.py:280）。

## What Changes

- **Web 证据来源分层**：检索阶段为 web 证据识别来源层级（官网/一方域名 → `first_party`/`official`，其余保持 `unknown`），使 authority policy 对 web 证据可达。
- **authority 未满足改为可恢复缺口**：当存在证据但无 authority-tier 证据时，验证产出 `RECOVERABLE_GAP`（missing: `authority`）而非直接 `EVIDENCE_INSUFFICIENT`；恢复预算耗尽后才允许终态不足。
- **新增查询改写恢复步骤**：计划词汇表增加 `QUERY_REFORMULATION`（recovery_only）步骤类型；当验证产出可恢复缺口且预算允许时，由缺失约束驱动生成改写 query（补充意图关键词、拆分比较成员逐查、放宽/收紧范围），执行受 query/recovery 预算约束的重搜，并将结果并入同一证据账本。
- **有限证据允许带限定作答**：当 retained=0 但 limited>0 时，不再静默早退，而是允许基于 limited 证据生成明确标注不确定性的回答，交由 postcheck/验证终判；完全无证据时才直接 `return_insufficient`。
- **关键词生成失败透明化**：LLM 关键词调用失败时，tracer 步骤与控制面元数据必须显示 `fallback_used` 与错误摘要；确定性 fallback query SHALL 保留查询意图线索（如价格类查询补充 pricing 词），而非仅拼接实体。
- **空目录跳过索引构建**：本地文档快照为空（0 个可索引文件）时 SHALL 跳过嵌入模型加载与索引构建。

## Capabilities

### New Capabilities

（无——全部落在现有能力的需求变更上）

### Modified Capabilities

- `evidence-policy-routing`: authority 策略的来源层级判定扩展为可对 web 证据赋予 `first_party`/`official` 层级；区分"完全无证据"与"无权威层级证据"的验证结果；limited 证据在明确标注下可作为受限作答依据。
- `query-plan-orchestration`: 计划词汇表新增 `QUERY_REFORMULATION` 恢复步骤；验证对非 temporal 缺口也可产出 `RECOVERABLE_GAP`；`next_action="recover"` 必须有执行器消费并形成 bounded 恢复循环。
- `search-routing-core`: 关键词生成失败必须可观测（tracer + control 元数据）；确定性 fallback query 必须保留意图线索。
- `unified-rag-execution`: 空本地文档目录跳过索引构建；恢复检索步骤与首次检索共用同一证据账本与预算。

## Impact

- **代码**：`utils/query_orchestration.py`（计划/验证/账本）、`langchain/langchain_rag.py`（检索 provenance、preflight、恢复执行）、`langchain/langchain_orchestrator.py`（关键词失败可观测、恢复循环接线）、`search/search.py` 或新模块（web 域名层级判定）、`evidence/`（source_tier 元数据流）。
- **配置**：可能新增 `orchestration.reformulation_recovery`（enabled/max_attempts）与 web 一方域名表配置项；保持默认向后兼容。
- **行为**：定价/最新/合规类查询不再系统性误报"证据不足"；恢复循环受既有 query/recovery/time 预算约束，不改变预算语义。
- **测试**：`tests/test_query_orchestration.py` 及搜索质量评测脚本需覆盖新验证路径与恢复循环。
