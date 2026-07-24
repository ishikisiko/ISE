# Tasks: fix-evidence-recovery-loop

## 1. Web 证据来源分层

- [x] 1.1 在 `evidence/` 层新增 web tier 判定工具：词干归一化（小写、去版本数字/分隔符，`glm5.2`→`glm`）、可注册域名提取、`unknown` 默认
- [x] 1.2 支持 `orchestration.official_domains` 别名配置表，命中标 `official`；域名主体包含实体词干标 `first_party`
- [x] 1.3 将判定接入 `langchain_rag.py` 三处 web provenance（579/596/672），替换硬编码 `"source_tier": "unknown"`
- [x] 1.4 单测：官方域名命中提层、无关域名保持 unknown、别名表优先于词干匹配

## 2. 验证可恢复性推广

- [x] 2.1 `PlanStepKind` 新增 `QUERY_REFORMULATION`，并纳入 `PlanController._counts_as_query`
- [x] 2.2 `build_query_plan`：当 authority/comparison 策略存在且 `recovery_budget>0` 时追加 recovery_only 的改写步骤占位
- [x] 2.3 `verify_evidence_plan`：非 temporal 缺口（no_evidence、authority 未满足、比较成员缺失）在恢复预算允许时产出 `RECOVERABLE_GAP` + `next_action="recover"`；关键歧义/搜索不可用/预算耗尽保持终态
- [x] 2.4 单测：非 temporal 查询 no_evidence → recoverable；authority 缺口 → recoverable；预算耗尽 → `recovery_budget_exhausted` 终态

## 3. 改写恢复循环（编排器层）

- [x] 3.1 实现确定性改写器：按 missing_constraints 优先级（authority→官方定价表达；comparison:<member>→逐成员查询；no_evidence→放宽+意图线索）生成改写 query
- [x] 3.2 `LangChainOrchestrator.answer` 增加恢复循环：`next_action="recover"` 且预算允许时用改写 query 重跑 `_run_primary_rag`，复用 `self._current_ledger` 与 `PlanController`，每迭代写 trace（`record_recovery`）
- [x] 3.3 新增 `orchestration.reformulation_recovery.enabled`（默认 true）与配置规范化；disabled 时保持旧单次检索行为
- [x] 3.4 单测：循环终止性（预算耗尽/时间耗尽/验证通过）、改写 query 内容断言、账本跨迭代合并

## 4. limited 证据带限定作答

- [x] 4.1 `SearchRAGChain.answer` preflight：仅当账本完全无条目时早退；有 limited 条目时继续生成并附加"来源未满足权威性、须标注不确定性"的 system prompt 指令
- [x] 4.2 `_finalize_response`/`_apply_postcheck`：基于 limited 证据生成的非空回答不再被 `_mark_evidence_insufficient` 替换，control 记录 `answer_basis="limited_evidence"` 与验证结果
- [x] 4.3 单测：limited>0 时回答保留且 control 标注正确；0 条目时仍硬停

## 5. 关键词失败可观测与意图保留 fallback

- [x] 5.1 `_generate_keywords` 失败时 tracer 步骤 detail 显示 `fallback_used` 与错误摘要
- [x] 5.2 `deterministic_query_for_plan` 追加 claim-class 线索词（pricing→价格 pricing、current→最新 latest），替代裸实体拼接
- [x] 5.3 单测：LLM 异常路径的 tracer 详情与 fallback query 内容

## 6. 空目录跳过索引构建

- [x] 6.1 `SearchRAGChain.__init__` 建 store 前轻量检查可索引文件，空目录 `tracer.skip("local_index")` 且 `vector_store=None`
- [x] 6.2 单测：空目录不产生嵌入加载；新增文件后快照签名变化触发重建

## 7. 集成验证

- [x] 7.1 复现用例回归：`对比fable5 api价格和glm5.2,kimik3` 全流程，确认改写恢复执行且不再静默"证据不足"
- [x] 7.2 既有用例回归：temporal 逐年补搜、domain API、clarification、search off 路径行为不变
- [x] 7.3 `python -m pytest` 全量通过；CLI（`python main.py`）与 Flask 路由各抽样验证 search on/off
- [x] 7.4 更新 `config.example.json` 与 AGENTS.md 配置说明（`reformulation_recovery`、`official_domains`）
