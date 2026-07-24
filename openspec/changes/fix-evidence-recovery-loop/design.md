# Design: fix-evidence-recovery-loop

## Context

默认链路（`LangChainOrchestrator.answer` → `SearchRAGChain.answer`）中，计划/验证骨架（`QueryPlan`、`EvidenceLedger`、`verify_evidence_plan`、`PlanController`、`recovery_budget`）已经存在，但三处断点使"价格/最新/合规"类查询系统性失败：

1. web 证据 `source_tier` 在 `langchain_rag.py:579/596/672` 硬编码 `"unknown"`，authority policy（`accepted_source_tiers=("official","first_party","authoritative")`）对纯 web 证据不可满足 → 全部 `limited` → 0 retained → preflight 在 `langchain_rag.py:1162` 直接早退。
2. `recoverable=True` 的唯一条件是计划含 `TEMPORAL_RECOVERY` 步（`query_orchestration.py:862,923`）；`next_action="recover"` 没有任何执行器消费。
3. 关键词 LLM 失败被静默吞掉（`langchain_orchestrator.py:596`），fallback 为实体拼接（`deterministic_query_for_plan`，`query_orchestration.py:516`），丢失意图线索；tracer 不可见。

约束：保持 deterministic-first 架构（验证/分层不引入新的 LLM 依赖）；恢复循环受既有 `query_budget`/`recovery_budget`/`time_budget_ms` 约束；默认行为修复但允许配置回退。

## Goals / Non-Goals

**Goals:**
- authority policy 对 web 证据可达（一方/官方域名分层）。
- authority/比较成员/无证据缺口均可触发受预算约束的改写重搜循环。
- retained=0 但 limited>0 时生成明确标注来源局限的回答，而非静默"证据不足"。
- 关键词失败可观测，fallback 保留意图线索。
- 空 uploads 不再支付 ~9.5s 索引税。

**Non-Goals:**
- 不改动 temporal 恢复（逐年补搜）现有语义。
- 不引入 LLM 来源分层分类器（保持确定性）。
- 不改变 postcheck → ReAct fallback 的启用条件与 `verification_blocks_react` 语义（见 Open Questions）。
- 不做搜索结果内容抓取（full-content fetch）级别的证据增强。

## Decisions

### D1: web 来源分层用确定性域名→实体映射，不用 LLM

在 `evidence/` 层新增 tier 判定（供 `hits_to_items` provenance 使用）：

- 取 URL 可注册域名，与 `analysis.comparison_members`/`entities` 的词干做匹配。词干归一化：小写、去除版本数字与分隔符（`glm5.2`→`glm`、`kimik3`→`kimi`、`fable5`→`fable`）。
- 命中：域名包含词干（`zhipu.cn` 含 `glm`? ——不含；需配置别名表兜底）→ `first_party`。
- 配置别名表 `orchestration.official_domains`：`{"glm": ["zhipu.cn","bigmodel.cn"], "kimi": ["moonshot.cn","kimi.com"], ...}`，命中 → `official`。
- 其余 → `unknown`。

理由：一次额外 LLM 调用换不来可审计性；误升层只会把证据从 limited 提为 retained，仍受 postcheck 终判。替代方案（LLM 分层 / provider 元数据）分别因成本与 Brave 不提供而否决。

### D2: 恢复循环放在编排器层，不放进 SearchRAGChain

循环形态：

```
result = _run_primary_rag(...)
outcome = _verify_current_plan(result)
while outcome.recoverable and budget remains:
    query = reformulate(outcome.missing_constraints, analysis)
    result = _run_primary_rag(search_query=query, 同一 ledger/plan_controller)
    outcome = _verify_current_plan(result)
```

理由：验证与预算状态（`PlanController.recoveries_used`）已在编排器层；`self._current_ledger` 跨迭代复用天然满足"同一账本"。替代方案（pipeline 内循环）会把验证语义复制到 RAG 层；temporal 恢复保留为 pipeline 内特例不动。

预算：`PlanController._can_run` 对 recovery_only 步骤已拦截 `recovery_budget_exhausted`；循环条件同时检查 `time_budget_ms`。`QUERY_REFORMULATION` 计入 `_counts_as_query`。

### D3: 改写策略由 missing_constraints 确定性模板驱动

优先级（每次恢复迭代取最高优先级缺口生成一个改写 query）：

1. `authority` → 对每个比较成员：`<member> official pricing 价格 定价`（配合 D1 分层，官方域名被提层）。
2. `comparison:<member>` → 对缺失成员：`<member> <意图线索词>`。
3. `no_evidence` → 放宽：核心单一实体 + 意图线索；或允许重试一次 LLM 关键词链。

理由：缺失约束本身已经是结构化信号，模板足以覆盖；引入 LLM 改写可作为后续增强，不进本 change。

### D4: limited 证据的"带限定作答"在 preflight 与 finalize 两处同时松绑

- `SearchRAGChain.answer` preflight（`langchain_rag.py:1162`）：仅当账本**完全无条目**时早退；有 limited 条目时继续生成，system prompt 附加"以下来源未满足权威性要求，回答必须明确标注不确定性"指令，并把 `verification_precheck` 附在 payload。
- `LangChainOrchestrator._finalize_response` / `_apply_postcheck` 的 `_mark_evidence_insufficient` 分支：当 answer 非空且基于 limited 证据生成时，保留回答，改为在 control 中记录 `verification` 与 `answer_basis="limited_evidence"`；完全不生成回答的场景才替换为证据不足文案。

理由：保留"绝不无依据作答"的硬底线，同时消除"有证据却一句话不说"的过杀。

### D5: 关键词失败可观测 + 意图保留 fallback

- `_generate_keywords` 失败路径：`keyword_info` 已有 `error`；在 `tracer.end("keywords", ...)` detail 追加 `（fallback: <error 摘要>）`。
- `deterministic_query_for_plan`：实体词干后追加 claim-class 线索词（`pricing`→`价格 pricing`、`current`→`最新 latest`、`comparison`→保留全部成员），替代裸实体拼接。

### D6: 空快照跳过索引构建

`SearchRAGChain.__init__` 在建 `LangChainVectorStore` 前做一次与 `_snapshot_local_docs` 同规则的轻量 `os.walk`；无可索引文件 → `tracer.skip("local_index", ...)` 且 `vector_store=None`。快照签名机制不变，后续新增文件会触发新签名与新 pipeline 实例，自然重新索引。

## Risks / Trade-offs

- [域名词干误命中（如 `kimi-reviews.com` 被提为 first_party）] → 词干匹配限定可注册域名主体、配置表优先；误升层仅影响 limited→retained，postcheck 仍可终判。
- [恢复循环增加延迟（+1 检索 +1 验证/迭代）] → 受 `time_budget_ms`（默认 20s）与 `recovery_budget`（默认 1）双上限；tracer 每迭代可见。
- [带限定回答可能显得"不自信"] → 标注文案模板保持一句话；postcheck judge 可继续裁决，必要时 ReAct fallback。
- [改写 query 对生僻实体（fable5?）仍可能搜不到] → 改写保留原始实体词；最终仍不足时按 D4 走带限定回答或证据不足，行为不劣于现状。
- [别名配置表需要维护] → 表为空时退化为纯词干匹配，功能可用；主流模型厂商一次性录入即可覆盖高频查询。

## Migration Plan

1. 新增配置（均有安全默认，缺失时等价于旧行为之外的修复行为）：
   - `orchestration.reformulation_recovery.enabled`（默认 `true`，设 `false` 回退旧的一次性检索行为）
   - `orchestration.official_domains`（默认空表）
2. 无数据迁移；checkpoints/conversations 不受影响。
3. 回滚：配置 `reformulation_recovery.enabled=false` 即恢复旧的单次检索 + 硬早退路径。

## Open Questions

- `recovery_budget` 默认是否从 1 提到 2（authority + comparison 双缺口可能需要两次迭代）？倾向保持 1，由配置上调，避免默认延迟膨胀。
- 带限定回答生成后 plan 验证仍可能为 `EVIDENCE_INSUFFICIENT`（authority 终未满足），此时是否解除 `verification_blocks_react` 允许 ReAct fallback？本 change 保持阻断，留待 postcheck 能力专项评估。
- `official_domains` 别名表放 `config.json` 还是独立 `config/official_domains.json`？倾向随 `orchestration` 块走 `config.json`。
