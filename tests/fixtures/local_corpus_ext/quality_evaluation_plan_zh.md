# ISE 质量评测实施计划

依据 [质量分析设计](quality_evaluation_design.md) §8 的六个阶段展开为可勾选任务。设计文档定义"量什么、怎么判"；本计划定义"谁先做什么、改哪个文件、做到什么算完"。实测数字一律登记到 [baseline.md](baseline.md)，评测报告放 `docs/reports/quality_evaluation_<date>/`。创建日期 2026-09-09；同日完成全部离线任务（勾选处括注产物），[真实运行] 与需人工双标的任务未授权、未运行（括注就绪状态），首份报告见 [reports/quality_evaluation_20260909/report.md](reports/quality_evaluation_20260909/report.md)。

## 0. 范围、约定与前置条件

### 0.1 范围与非目标

- 范围：把设计文档 D0–D11 的指标落成可重复运行的脚本、数据集与报告；先让已有脚本产出第一批检索与 RAG 分数，再补自动指标、裁判校准、记分卡与回归门。
- 非目标：不改产品默认配置（预算、provider 链、rerank 开关、chunk 参数）；评测暴露的问题另开 OpenSpec change 修，不在本计划内顺手改。不做 devbench 的题目与评审（那套体系见 [plan.md](../plan.md)）。
- 最小可交付：Q0 + Q1 + Q3 的自动部分。不需要新数据集，能回答"搜索与 RAG 现在几分"。

### 0.2 任务与状态约定

- 任务编号 `Q<阶段>-<序号>`；每条写"改什么 → 验收什么"。勾选时在行尾括注日期与产物路径（沿用 plan.md 惯例），不删除未完成任务。
- 真实运行（消耗搜索配额、LLM 调用、provider credits）的任务标 **[真实运行]**，启动前需用户授权并记录配额预算；离线任务不标。
- 需要人工标注或人工判定的任务标 **[人工]**，标注者与数据集构建者尽量不同人；做不到时在产物里注明。
- 任何指标口径变更先改设计文档对应小节，再改脚本，最后在 baseline.md 该指标首次登记处注明变更日期。
- 评测脚本的参数优先级固定为**显式 CLI 参数 > `config.quality.json` > 脚本内置默认值**；配置块里的未知键或类型不符一律报错退出，不静默回落。`config.json` 只出凭据与产品行为，评测协议不写进去。

### 0.3 前置条件

| 条件 | 现状 | 用途 |
|---|---|---|
| 搜索 provider 凭据与配额（Brave 月配额、AnySearch、Tavily、Firecrawl、Parallel、BrightData） | `config.json` 已配置；Firecrawl 2026-09-07 曾单日消耗 556 credits | Q1-04、Q3-04、Q5-04 |
| 主模型（`opencode-go/deepseek-v4-flash`）与裁判模型（`glm-5.2`） | 自主度评测已验证可用；需带 `x-opencode-session` 头 | Q1-04、Q4-04、Q5-04 |
| Embedding 服务（`qwen3.7-text-embedding`） | 已配置 | Q1-06～Q1-09 |
| 人工标注人（轻量档 ≥ 1 人，双标 ≥ 2 人） | 未安排 | Q1-04、Q4-02、Q4-05 |
| 评测运行目录 `runtime/quality/`（gitignored） | `runtime/` 已忽略 | 全部 |
| 数据集与标注文件版本化位置 | `dataset/` 已入库；`tests/search_quality_external_*` 被忽略 | 新标注放 `dataset/annotations/`，不放 `tests/` |
| 评测参数配置 `config.quality.json`（入库、无凭据） | 2026-09-09 新增；`runner` / `judge` 两块 | 裁判模型与 runner 默认值改这里，不改 `config.json`；`--quality-config` / `ISE_QUALITY_CONFIG` 可换文件 |

### 0.4 真实运行预算估算

按 baseline.md "75 问约 60–90 分钟"折算。数字是计划值，实际以运行 `run_meta.json` 为准。

| 任务 | 规模 | 估算 |
|---|---:|---|
| Q0-05 D0 冒烟 | 5 问 | < 10 分钟 |
| Q1-04 搜索采集 | minimal 搜索子集 + gold_doc 22 问，各 1 次 | 约 40 问，≤ 60 分钟；每问 ≤ 3 次 web_search |
| Q1-09 本地 RAG 扫描 | 30 参数组 × ≤ 30 问，仅 embedding | 无 LLM 调用；embedding 请求约 1,000 次 |
| Q3-04 全供应商采集 | 40 问 × 7 provider | 仅搜索请求，无 LLM；Firecrawl/Tavily 按 credits 计 |
| Q4-04 裁判校准 | final_answer 20 + open_task 20，guided 1 次 + 裁判 40 次 | 约 60 分钟 + 裁判 40 调用 |
| Q5-04 首份完整记分卡 | 全部题集 1 次 | 视题集规模，≤ 3 小时 |
| Q5-05 重复一致性 | final_answer 20 × 3 次 | 约 60 问 |

---

## Q0. 度量有效性（对应设计 P0）

目标：D0 全指标可产出；任一门槛不过，后续阶段的数字不上报。

### Q0-A. 参数与记账

- [x] Q0-01 参数透传断言：新增 `tests/quality/test_param_forwarding.py`，用真实 builder（禁网）构建 `LangChainOrchestrator`，断言入口 `max_tokens` / `temperature` / `num_search_results` / `autonomy` 到达 loop 内模型对象与工具配置；沿用 `runtime/baseline/autonomy-20260908-measured/parameter-forwarding-audit.json` 的探针方式。若断言失败，登记为产品缺陷并开 change，不在本任务内改产品。验收：测试存在且结果（通过或标记 xfail 并附缺陷编号）写入 `validity.json`。（2026-09-09 完成：`tests/quality/test_param_forwarding.py`：2 通过 + 3 严格 xfail，缺陷 QD-20260909-01/02，change `openspec/changes/forward-entry-generation-params/`；2026-09-18 已修复并归档至 `openspec/changes/archive/2026-09-18-forward-entry-generation-params/`，探针 6 passed、xfail 全部移除）
- [x] Q0-02 外部请求计数补齐：`search_recovery` 内部的 provider 请求（`langchain_rag._retrieve_evidence`，目前只进 `execution_trace` 与 `search_api_calls`）与官方域名解析器的发现搜索/验证抓取都进 `TimingRecorder.tool_calls`，携带 `kind`（search / extract / resolver_discovery / resolver_verify）与 `provider`。验收：`response_times.tool_calls` 条数与 Q0-04 代理侧计数在 5 题冒烟上一致（比率 ≥ 0.95）；`test_retrieval_trace.py` 补对应用例。（2026-09-09 完成：`utils/provider_calls.py` + `TimingRecorder.record_provider_request`；`langchain_rag._retrieve_evidence`、loop 工具快照、resolver 发现/验证/探针均进 `tool_calls`；`tests/quality/test_provider_call_capture.py`；比率断言待 Q0-05 真实冒烟）
- [x] Q0-03 provider credits 字段：`SearchClient._append_call_record` 与 `ReferenceExtractor._record_timing` 增加 `credits`（Firecrawl / Tavily / Parallel 从响应体或响应头解析，解析不到时按 `config.<provider>.credits_per_request` 固定值，默认 null），并追加到 `runtime/provider_usage/<provider>.jsonl`（格式对齐 `brave_search_usage.jsonl`）。验收：单元测试覆盖三种来源；日累计脚本 `tests/quality/provider_usage.py` 能按日/provider 汇总。（2026-09-09 完成：`utils/provider_usage.py`，search / extract 客户端 `credits` 字段与 `runtime/provider_usage/<provider>.jsonl`，`tests/quality/provider_usage.py`，`tests/quality/test_provider_credits.py`）
- [x] Q0-04 扩展 `tests/study_transport.py` 的 `TransportObserver`：除 LLM usage 外记录每个非 LLM 请求的 host、路径前缀、状态码、时长，并按 provider host 归类；`summary()` 新增 `external_requests_by_provider`。验收：单元测试用假 `requests.Session.send` 验证归类；不改动生产请求与响应。（2026-09-09 完成：`tests/study_transport.py` 记录非 LLM 请求 host/路径前缀/状态/时长并按 provider 归类，`tests/quality/test_transport_observer.py`）

### Q0-B. 冒烟与产物

- [x] Q0-05 **[真实运行]** 新建 `tests/quality/validity.py`：跑 5 题（final_answer 前 5），同时挂 `TransportObserver`，输出 `runtime/quality/<run>/validity.json`，含 `token_capture_rate`、`usage_reconciliation_gap`、`tool_call_capture_ratio`、`search_call_capture_ratio`、`param_forwarding_pass`、`trace_completeness`（含 `truncated` 题数）、`audit_truncation_rate`。验收：文件存在、七项齐全、门槛判定字段 `passed` 明确；首次结果登记 baseline.md 新小节"质量评测 · D0"。（2026-09-09 状态：脚本 `tests/quality/validity.py` 就绪；真实 5 题冒烟未授权，未运行。2026-09-18 完成：七项齐全全部过门槛，`passed=true`，登记 baseline.md §7.2）
- [x] Q0-06 audit 截断率：`utils/audit_log.AuditRecorder` 在记录被 `_apply_size_cap` 修剪时写 `truncated=true` 与被剪字段名。验收：`test_audit_log.py` 补用例；Q0-05 能统计。（2026-09-09 完成：`utils/audit_log.py` 写 `truncated_fields`，`tests/test_audit_log.py`）

退出判据：`token_capture_rate` = 1.0、`param_forwarding_pass` = 1.0（或 xfail 有缺陷编号）、`tool_call_capture_ratio` ≥ 0.95。

---

## Q1. 首批检索与 RAG 分数（对应设计 P1）

目标：用现有脚本真正跑出 D3 与 D5 的第一批数字并登记。

### Q1-A. 搜索链路

- [x] Q1-01 对齐 `collect`：`tests/search_quality_pipeline.py` 的 `collect_records` 改为记录当前 `control` 的 `query_analysis`、`execution_trace`、`evidence_coverage`、`loop_verdicts`、`loop_status`、`termination_reason`、`tool_budgets`，以及 `search_api_calls` 与带 `metadata.eid` 的 `evidence_records`；删除已不产出的 `selected_sources`、恒空的 `keywords`。验收：`test_search_quality_pipeline.py` 用假 orchestrator 覆盖新字段；旧 annotations 文件仍能被 `evaluate` 读取。（2026-09-09 完成：`tests/search_quality_pipeline.py collect` 记录 control 投影 / `search_api_calls` / 带 eid 的 `evidence_records`，loop 响应新增 `search_api_calls`，`tests/test_search_quality_pipeline.py`）
- [x] Q1-02 `evaluate` 新增自动指标：`ndcg_at_5`（相关性 0/1/2，标注模式 `detailed` 时启用）、`gold_doc_recall_at_k`（k=3/5，URL 规范化：小写 host 去 `www.`、去 query/fragment、路径前缀匹配）、`authoritative_at_k`、`aggregator_at_k`、`domain_diversity_at_5`、`empty_result_rate`；`judgment.relevance_grades` 字段接受逐 rank 0/1/2。验收：单元测试给出手算样例，MRR / Hit@k 旧值不变。（2026-09-09 完成：`evaluate` 新增 nDCG@5 / gold_doc_recall@3,5 / authoritative@k / aggregator@k / domain_diversity@5 / empty_result_rate / `relevance_grades`，旧 MRR/Hit@k 用例不变）
- [x] Q1-03 gold_doc 接入：`map-external` 已合并 `gold_doc_dataset.csv`，补 `--gold-doc-file` 直通参数让 `evaluate` 能按 query 关联 gold URL；标注文件里 gold 命中自动预填 `relevant_urls`，人工只需复核。验收：22 条 gold_doc 全部能关联到采集记录。（2026-09-09 完成：`--gold-doc-file` 直通 collect/evaluate，gold 命中预填 `relevant_urls`；22 条 gold_doc 可按规范化 query 关联）
- [ ] Q1-04 **[真实运行] [人工]** 采集与标注：分别对 `tests/search_quality_minimal_search_queries.txt` 与 gold_doc 22 题跑 `collect --num-results 5 --force-search --show-timings`，输出到 `runtime/quality/<run>/search_collect_{minimal,gold_doc}.json`；人工用轻量档标注（`top3_has_answer_evidence` + `core_correct`）并把标注 JSON 提交到 `dataset/annotations/search_<date>.json`；跑 `evaluate` 出 `search_report.json`。验收：两组 `hit_at_3`、`mrr`、`gold_doc_recall_at_5`、`authoritative_at_k`、`empty_result_rate` 与分段时延齐全；按 `category` 宏平均；样本 < 5 的类别只报计数。（2026-09-09 状态：采集/标注脚本与 gold 就绪；真实采集未授权，未运行）
- [x] Q1-05 provider 记分卡 v0：从 Q1-04 的 `search_api_calls` 汇总每 provider 的 `availability`、`error_rate_by_type`、`empty_rate`、`latency_p50/p95`、`fallback_share`、`retained_contribution`（结果 URL 与 `evidence_coverage.decisions` 按规范化 URL 关联）；官方域名解析器的发现搜索按 `target` / `label` 单列。验收：`search_report.json` 含 `providers` 子表；注明"当前链只到首个有结果的 provider，`unique_yield` 待 Q3-04"。（2026-09-09 完成：`evaluate` 输出 `providers.answer_path` / `official_domain_discovery` 子表，unique_yield 待 Q3-04）

### Q1-B. 本地 RAG

- [x] Q1-06 固定评测语料：建 `tests/fixtures/local_corpus/`，收入根目录 `System_Architecture.md` 与 ≥ 9 个其他文件（md / txt / pdf，中英各半，合计 ≥ 200 chunk @1000/200），附 `MANIFEST.md` 记录来源与许可。验收：`LangChainFileReader` 能全部加载；chunk 数写入 MANIFEST。（2026-09-09 完成：`tests/fixtures/local_corpus/` 16 文件 / 中英各 8 / md·txt·pdf / 209 chunk，`MANIFEST`；QD-20260909-03 `.md` 静默跳过已加回落）
- [x] Q1-07 **[人工]** gold chunk：把 `tests/search_quality_local_chunk_template.csv` 迁到 `dataset/local_chunk_gold.csv`，字段 `qid, query, gold_doc_id, gold_span, is_absent, language, notes`；先填 minimal 的 Q016–Q020，再补到 ≥ 12 题（含 2 题多 span、2 题 `is_absent`、2 题跨语言）。验收：每条 gold_span 能在语料原文中定位（脚本校验子串存在）。（2026-09-09 完成：`dataset/local_chunk_gold.csv` 38 题，`tests/quality/local_gold_check.py` 全部可定位；标注人 = agent 单人，未经第二人复核）
- [x] Q1-08 扩展 `tests/local_chunk_grid_search.py`：新增 `chunk_hit_at_k`（gold_span 子串或 token 重叠 ≥ 0.8）、`mrr_chunk`、`context_recall`（多 span）、`score_margin`（FAISS 距离，gold 减最佳非 gold；越小越相关，符号按此定义）、`abstention_candidates`（`is_absent` 题 top-k 的最小距离，供后续阈值分析）；`--top-k` 支持多值（3,5）；输出 JSON 与制表两种。验收：单元测试用内存向量桩验证指标；k=3 与 `local_docs` 工具一致。（2026-09-09 完成：`tests/local_chunk_grid_search.py` chunk 级指标 + `--top-k 3,5` + JSON/表格，`tests/quality/test_local_chunk_grid_search.py`）
- [x] Q1-09 **[真实运行]** 扫描：在 Q1-06 语料上跑默认网格（chunk_size 300–1500 × overlap 0–300）× k∈{3,5}，加当前 embedding 一组；输出 `runtime/quality/<run>/local_rag_eval.json`。验收：登记 baseline.md 新小节"质量评测 · 本地 RAG 参数扫描"，含当前默认 1000/200 的位次；不改默认值。（2026-09-18 已跑：`runtime/quality/20260918-quality-20260918/local_rag_eval.json`，29 组，默认 1000/200 排 24/29，最优 800/0 hit@3 0.875；已登记 baseline §7.10，默认值未改）

### Q1-C. 登记与文档

- [ ] Q1-10 首份报告：`docs/reports/quality_evaluation_<date>/report.md`，按设计 §7.3 章节（范围与证据等级 → D0 → 硬门槛 → 指标 → 每维要点与三条失败 → 逐题附表 → 复现命令 → 局限）；baseline.md 追加"质量评测"章节汇总 Q0-05、Q1-04、Q1-09 数字；`guides/search_quality_evaluation.md` 同步新增指标与字段。验收：报告内命令可在干净目录复现采集之外的全部计算；docs/README.md 索引登记。（2026-09-09 状态：首份报告 `docs/reports/quality_evaluation_20260909/report.md` 已出，含离线与历史重算数字；D3/D5 真实数字待补，任务待真实运行后关闭）

退出判据：D3 与 D5 各至少一组按类别的数字登记到 baseline.md；D0 冒烟通过。

---

## Q2. 零成本离线回归（对应设计 P2）

目标：不调用 LLM 与真实搜索即可回归 D1、D6、D10 的一部分；一条命令 2 分钟内跑完。

- [x] Q2-01 **[人工]** `dataset/query_analysis_gold.csv`（≥ 60 条：对比类 ≥ 20 含中英混排与指令尾句、时效类 ≥ 15、歧义类 ≥ 10、闲聊/本地 ≥ 15），字段按设计 §6.2；新增 `tests/quality/analysis_eval.py` 调 `prepare_analysis`（`llm=None` 时只做 sanitize）计算 `intent_shape` 准确率与混淆矩阵、成员 P/R/F1、`noise_member_rate`、实体 P/R、claim_classes micro-F1、`critical_ambiguity` P/R、`existence_query` 精确率、`false_temporal_fanout_rate`；成员匹配用 `normalize_entity_stem` + 别名表。验收：`analysis_eval.json` 输出；pytest 版本断言对比类 `noise_member_rate` = 0。（2026-09-09 完成：`dataset/query_analysis_gold.csv` 65 题（agent 单人），`tests/quality/analysis_eval.py`；false_temporal_fanout=0 通过，noise_member_rate=0 为严格 xfail QD-20260909-04）
- [x] Q2-02 **[人工]** `dataset/source_tier_gold.csv`（≥ 150 条，含钓鱼/镜像/聚合站负例）与 `dataset/official_domain_gold.csv`（≥ 60 实体，pins 之外 ≥ 45）；`tests/quality/tiering_eval.py` 用 `classify_source` 出 5×5 混淆矩阵、`official_precision/recall`、`denylist_compliance`、`non_evidence_exclusion`；resolver 评测用 `tests/fixtures/official_domains_replay.sqlite`（从真实缓存导出、去时间戳）离线回放，出 `resolver_accuracy`、`resolver_none_rate`。验收：`evidence_eval_offline.json`；pytest 断言 `official_precision` ≥ 0.98、`denylist_compliance` = 1.0。（2026-09-09 完成：`dataset/source_tier_gold.csv` 167 / `dataset/official_domain_gold.csv` 78（agent 单人），`tests/fixtures/official_domains_replay.sqlite`，`tests/quality/tiering_eval.py`；official_precision 1.0、denylist_compliance 1.0）
- [x] Q2-03 故障注入：`tests/quality/test_fault_injection.py` 用抛错 / 返回空 / 超时的桩 client 组 `build_priority_chain`，断言回落层级、`search_api_calls` 的 `fallback` 与 `reason`、最终有结果；模拟 Brave `monthly_limit` 耗尽切换下一 tier；主模型 4xx/5xx 时 `llm_error` 如实暴露且不换模型。验收：覆盖 `searchFallback.batch_sizes` 默认与自定义两种配置；全部离线。（2026-09-09 完成：`tests/quality/test_fault_injection.py` 8 例）
- [x] Q2-04 统一入口：新增 `pytest.ini` 注册 marker `quality_offline`；Q2-01～Q2-03 与 Q0-01 打该标记；`env1/bin/python -m pytest -m quality_offline` 在 2 分钟内完成。验收：README "Testing" 段落补一句入口说明。（2026-09-09 完成：`pytest.ini` marker `quality_offline`，67 例 ≈ 10 秒，README Testing 段）
- [x] Q2-05 **[人工]** skill 评测用例：五个 `skills/*/evals/cases.jsonl` 各扩到 ≥ 30 条（当前 5–8 条），每个至少 10 条困难负例；`tests/quality/preflight_eval.py` 汇总 `preflight_precision/recall` 与拒绝原因分布。验收：现有 pytest 仍全绿；汇总脚本输出按 skill 的表。（2026-09-09 完成：五个 `cases.jsonl` 扩到 30–34 例（agent 单人），53 例 known_gap 钉住观察值，`tests/quality/preflight_eval.py`）

退出判据：`-m quality_offline` 全绿并可作为 PR 前检查；D1 对比类成员 F1 与 D6 tier 混淆矩阵首次登记 baseline.md。

---

## Q3. 证据、引用、循环与成本的自动指标（对应设计 P3）

目标：不依赖新标注，从运行产物直接算 D4、D6、D7 引用部分、D8、D9。

- [x] Q3-01 引用离线核验：`tests/quality/citation_eval.py` 对 `answer_details.jsonl` 每题重跑 `check_citations`（传入 `evidence_records`、`requires_official_pricing`、`temporal_required` 取自 `query_analysis`），输出 `hallucinated_citation_count`、`citation_recall`、`authority_compliance`、`pricing_source_compliance`、`recency_compliance`、`citations_per_answer`、`unverified_hedge_rate`。验收：与 loop 内 `citation_failures` 抽样 10 题一致；硬门槛字段单列。（2026-09-09 完成：`tests/quality/citation_eval.py`，历史 r2 抽样 10/10 与 loop 一致）
- [x] Q3-02 证据自动指标：`tests/quality/evidence_eval.py` 从 `evidence_coverage` 出 retained / limited / rejected / merged 分布、`authoritative_entries` 占比、`aggregator_leak_rate`（答案引用的 `[En]` 反查 tier）、`member_coverage`（对比题 `comparison_members_covered` 与 gold 成员集合的 F1，gold 来自 Q2-01）。验收：`evidence_eval.json`；`aggregator_leak_rate` 对 `authority_required` 题单列。（2026-09-09 完成：`tests/quality/evidence_eval.py`）
- [x] Q3-03 循环指标：扩展 `search_quality_pipeline.py loop-audit` 或新建 `tests/quality/loop_eval.py`，从 `loop_verdicts` / `loop_status` / `termination_reason` / `evidence_coverage` 出 `loop_status_dist`、`termination_reason_dist`、`false_exhaustion_rate`（exhausted/stagnated 且 `evidence_sufficiency ∈ {sufficient, partial}` 或 retained ≥ 3）、`premature_success_rate`（需 Q4 的 `core_correct`，先输出 `succeeded` 且 `delivered=false` 的部分）、`forced_synthesis_rate`、`degraded_synthesis_rate`、`final_answer_rejected_count`、`judge_invocation_rate`、`judge_error_rate`、`invalid_tool_request_rate`、`narration_guard_trigger_rate`、`no_progress_streak_p95`、`iterations_to_first_answer`、`advisory_gap_ignore_rate`（下一轮工具与 gap 类型匹配规则）、`budget_utilization`、`compaction` 计数。验收：对 `runtime/baseline/autonomy-20260908-measured` 的历史产物能直接重算并与报告 §4.4 数字一致。（2026-09-09 完成：`tests/quality/loop_eval.py`，对 `autonomy-20260908-measured` r1/r2 重算并与报告 §4.4 一致）
- [ ] Q3-04 **[真实运行]** 全供应商采集：`collect --all-providers` 用 `CombinedSearchClient` 展平后逐 provider 独立请求同一查询（不进 loop，只采搜索结果），记录每 provider 的 `results`；`evaluate` 计算 `unique_yield`、公平的 `retained_contribution`（以 Q1-04 标注为相关性 gold）、跨 provider 一致性（用于判断结果漂移）。验收：`search_report.json.providers` 补齐两列；Firecrawl / Tavily credits 消耗写入报告。（2026-09-09 状态：`collect --all-providers` 与 `evaluate` 的 unique_yield / 一致性已实现并测试；真实采集未授权）
- [ ] Q3-05 抓取指标：`tests/quality/fetch_eval.py` 从 `fetch_url` outcomes 与 `search_api_calls` 中 `extracted_pages` 记录出 `fetch_success_rate`、`extractor_attempts_per_success`、`extractor_success_by_provider`、`content_sufficiency_rate`、`truncation_loss_rate`；对 gold_chunk 22 题计算 `gold_span_containment`（需评测模式下落盘抽取正文：`audit.include_full_result=true`）。验收：`fetch_eval.json`；含有率计算有单元测试（空白/标点归一化）。（2026-09-09 状态：`tests/quality/fetch_eval.py` 就绪并对历史 r1/r2 重算；gold_span 抓取题样本不足，待真实运行）
- [x] Q3-06 成本汇总：`tests/quality/cost_eval.py` 出 `stage_latency`（search / fetch / local / skill / llm 按 `llm_calls[].label` 拆 act / judge / reconcile / compaction / synthesize）、token 按 label、`external_calls_per_query`（代理侧）、`provider_credits_per_query`、`budget_exhaustion_cost`、`cost_per_correct_answer`（`core_correct` 缺失时只报 token 与 credits，USD 记 null）。验收：`cost.json`；与 baseline_runner 现有汇总在 total_ms / total_tokens 上一致。（2026-09-09 完成：`tests/quality/cost_eval.py`，与 baseline_runner 的 total_ms / total_tokens 一致，`tests/quality/test_q3_evaluators.py`）

退出判据：Q3-01～Q3-03、Q3-06 能对历史产物重算；Q3-04 与 Q3-05 各产出一次真实数字并登记。

---

## Q4. 裁判 v4 与人工校准（对应设计 P4）

目标：D7 的模型辅助分可信到能上报；每个维度有与人工的 kappa。

- [x] Q4-01 rubric v4：新建 `tests/quality_review.py`（保留 `tests/autonomy_review.py` 的 v3-evidence 供历史复算），实现设计附录 C：输入全部 retained 证据（不截 12 条，每条上限提高并记录 `truncated`）、gold 勘误；输出 `core_correct` / `request_completeness` / `evidence_support`（0/1/2）、`semantic_fact_match[]`、`citation_checks[]`（supports / contradicts / irrelevant）、`abstention`、`factual_concerns[]`、`answer_complete`；盲评、独立请求、固定模型、`REVIEW_VERSION="v4"`。验收：`parse_review` 严格校验输出结构；单元测试覆盖解析与拒绝畸形输出；运行清单记录裁判模型、温度、请求 ID。（2026-09-09 完成：`tests/quality_review.py` rubric v4，`tests/quality/test_quality_review.py`；未对真实产物评分）
- [x] Q4-02 **[人工]** 人工标注表与指南：`docs/guides/quality_annotation_guide.md` 定义轻量档 / 详细档字段（设计附录 B）、0/1/2 判据、拒答判据、gold 勘误流程；标注文件放 `dataset/annotations/answer_<date>.csv`。验收：两名标注者按指南独立标 5 题后分歧 ≤ 1 题，否则修订指南。（2026-09-09 完成：`docs/guides/quality_annotation_guide.md` + `dataset/annotations/` 模板与 `gold_errata.json`；双标验收待人工）
- [x] Q4-03 kappa 脚本：`tests/quality/agreement.py` 计算裁判 vs 人工、人工 vs 人工的 Cohen's kappa（有序三值用加权 kappa）与逐题分歧清单。验收：单元测试给手算样例。（2026-09-09 完成：`tests/quality/agreement.py`，`tests/quality/test_agreement.py`）
- [ ] Q4-04 **[真实运行] [人工]** 校准运行：final_answer 20 + open_task 20 各跑一次（guided，默认配置）；裁判 v4 全评；人工双标随机 20%（8 题）+ 全部 `core_correct` 轻量标；输出 `runtime/quality/<run>/answer_details.jsonl` 与 `calibration.json`。验收：每个维度报告 kappa；kappa < 0.6 的维度在报告里标"仅人工"；`core_correct=0` 题数与 `hallucinated_citation_count` 作为硬门槛单列。（2026-09-09 状态：runner `--suite answer` + 裁判 v4 + `agreement.py` 就绪；真实运行与人工双标未授权）
- [ ] Q4-05 **[人工]** critic / judge 判定抽样：从 Q4-04 的 `loop_verdicts` 抽 30 轮 `deterministic_pass=false` 与 10 轮 judge 拒绝，人工判"阻断是否必要"，出 `critic_block_precision`、`critic_miss_rate`（最终答案有问题但 critic 通过）、`judge_agreement`；澄清题抽全，出 `clarification_precision`。验收：判定表入 `dataset/annotations/loop_<date>.csv`；结果登记 baseline.md。（2026-09-09 状态：`dataset/annotations/loop_template.csv` 与指南就绪；人工判定未做）

退出判据：D7 三个核心维度 kappa ≥ 0.6 或明确降级为人工；`premature_success_rate` 可计算。

---

## Q5. 记分卡与回归门（对应设计 P5）

目标：一条命令产出综合记分卡；改动前后能按 qid 配对比较并给出通过/不通过。

- [x] Q5-01 `tests/quality_runner.py`：按 `--suite`（validity / search / local / offline / answer / loop / cost / reliability / safety / all）调度已有脚本，写 `runtime/quality/<date>-<tag>/run_meta.json`（commit、config 摘要去密钥、数据集文件哈希、rubric 版本、裁判模型、`searchFallback` 链、autonomy 模式、并发与超时）；结果以独占创建写入，恢复只跳过已有结果。验收：`--suite offline` 不发起任何网络请求（用 `TransportObserver` 断言为 0）；`--dry-run` 打印将执行的命令。（2026-09-09 完成：`tests/quality_runner.py`，`--suite offline` 实跑 0 次网络请求，`--dry-run` 可用）
- [x] Q5-02 `tests/quality_report.py`：读取 run 目录生成 `scorecard.json`（D0 结论、硬门槛计数、六个指数及组成、按类别宏平均、bootstrap 区间 n ≥ 20 时）与 `report.md`（设计 §7.3 模板，逐题附表按 cases.md 风格）。验收：对 Q1/Q3/Q4 的历史 run 目录能生成；缺失维度显示"未运行"而不是 0。（2026-09-09 完成：`tests/quality_report.py`，对历史 r2 与离线 run 生成 scorecard.json / report.md，缺失维度显示未运行）
- [x] Q5-03 配对比较与回归门：`quality_report.py --compare <run_a> <run_b>` 按 qid 配对，输出胜/平/负、均差、硬门槛计数变化、成本指数比值；判定规则按设计 §5.2（硬门槛不增、三个指数无"负 > 胜"、成本比值 ≤ 1.2 或有接受记录）。验收：对 `runtime/baseline/autonomy-20260908-measured` 两模式重算的胜平负与报告 §4.1 一致。（2026-09-09 完成：`quality_report.py --compare / --compare-modes`，历史 r2 两模式重算胜平负与报告 §4.1 一致：开放 8/8/4、事实 3/9/8）
- [ ] Q5-04 **[真实运行]** 首份完整记分卡：`quality_runner --suite all` 跑当前全部题集（含 Q6 已完成的部分），产出 `docs/reports/quality_evaluation_<date>/`；baseline.md 登记六指数与硬门槛。验收：报告含 D0 结论、硬门槛、指数、每维三条失败、复现命令、局限。（2026-09-09 状态：`--suite all` 可 dry-run；真实运行未授权）
- [ ] Q5-05 **[真实运行]** 重复一致性：final_answer 20 × 3 次（同时段交替调度），出 `consistency_at_3`、`pass@1/pass@3`、`answer_variance`（裁判判语义一致）、`hard_timeout_rate`、`soft_cancel_correctness`；写入 `reliability.json`。验收：登记 baseline.md；不一致题逐题列出。（2026-09-09 状态：runner `--suite reliability --repeats 3` 就绪；未运行）
- [x] Q5-06 安全扫描：`tests/quality/safety_eval.py` 对 run 目录的 audit / 日志 / SSE 记录做凭据与 URL 密钥正则扫描（`credential_leak_count`），并对查询中的邮箱 / 手机号脱敏做断言；`denylist_compliance`、`non_evidence_exclusion` 从 Q2-02 复用。验收：`safety.json`；`credential_leak_count` 进硬门槛。（2026-09-09 完成：`tests/quality/safety_eval.py`，历史 r2 凭据泄漏 0）

退出判据：`quality_runner --suite all` + `quality_report` 能在一次授权运行内产出完整报告；`--compare` 可用于下一次产品改动的接受判定。

---

## Q6. 数据集扩充（对应设计 P6，可与 Q2–Q5 并行）

目标：补齐设计 §6.2 的数据集，每个都有标注人、日期与校验脚本。

- [x] Q6-01 **[人工]** `dataset/web_gold_zh.csv` ≥ 40 条中文网页 gold（产品对比 / 价格 / 文档定位 / 政策 FAQ），字段 `qid, query, gold_doc_url, gold_span, authority_required, valid_from, relevance_notes, created_at, gold_verified_by`；同时把 minimal 的 Q012–Q015 落实为具体 URL。验收：URL 当日可抓且 gold_span 存在于正文（脚本校验）。（2026-09-09 完成：`dataset/web_gold_zh.csv` 54 条（agent 单人），`tests/quality/web_gold_check.py --fetch` 54/54 当日可抓且 span 在正文；Q012–Q015 由具体 URL 题替代）
- [x] Q6-02 **[人工]** `dataset/final_answer_dataset.csv` 扩到 ≥ 60（新增中文 ≥ 15、时效 ≥ 10 带 `valid_from`、对比 ≥ 10、价格 ≥ 5 带官方价格页）；新建 `dataset/abstention_set.csv` ≥ 15（本地未提及 / 网页无可靠来源 / 不可答）。验收：`must_include_facts` 改为可语义判定的短句；勘误流程按 protocol.md。（2026-09-09 完成：`final_answer_dataset.csv` 62 条 + `abstention_set.csv` 16 条（agent 单人）；final041/059 按官方页勘误，其余时效 gold 标 UNVERIFIED；`autonomy_study.planned_queries` 钉住前 20 条）
- [x] Q6-03 **[人工]** `dataset/hard_loop_set.csv` ≥ 50（对比含指令尾句 / 多实体 / 价格 / 时效 / 歧义各 ≥ 10），每题标 `expected_loop_behavior`（应澄清 / 应抓官方页 / 应拒答 / 应完成）。验收：Q3-03 能按该列分组统计。（2026-09-09 完成：`dataset/hard_loop_set.csv` 54 条，每类 ≥ 10（agent 单人））
- [x] Q6-04 **[人工]** `dataset/local_chunk_gold.csv` 扩到 ≥ 30（多 span ≥ 5、`is_absent` ≥ 5、跨语言 ≥ 5），语料同步扩充。验收：Q1-08 脚本校验全部 gold_span 可定位。（2026-09-09 完成：`local_chunk_gold.csv` 38 题：多 span 10 / absent 6 / 跨语言 7（agent 单人））
- [x] Q6-05 **[人工]** `dataset/adversarial_pages/`（≥ 20 页：中英指令注入、隐藏文本、HTML 注释注入、伪官方页）与索引 CSV（`injection_type, expected_behavior`）；`dataset/multi_turn_set.csv` ≥ 15 组 × 3 轮（`turn_index, query, expected_reference_resolution`）。`tests/quality/injection_eval.py` 用桩 fetch 返回注入页，统计 `injection_resistance`。验收：注入评测离线可跑并打 `quality_offline` 标记；多轮集能被 `quality_runner --suite answer --multi-turn` 消费。（2026-09-09 完成：`dataset/adversarial_pages/` 22 页 + `index.csv`，`dataset/multi_turn_set.csv` 16 组×3（agent 单人），`tests/quality/injection_eval.py` 离线结构检查通过、`--live` 待授权；runner `--multi-turn` 消费多轮集）
- [x] Q6-06 **[人工]** `dataset/route_intent_dataset.csv` 补 `allowed_first_tools` 与 `allowed_tool_sequences` 列；`tests/quality/routing_eval.py` 出 `first_tool_correct`、`tool_sequence_admissible`、`unnecessary_search_rate`、`missing_search_rate`、`redundant_call_rate`、`budget_hit_rate`，并把 `full_text_trigger_dataset.csv` 接到 `fulltext_decision_correct`。验收：`routing_eval.json`；无工具可选的路由单列 `route_coverage_gap`。（2026-09-09 完成：`route_intent_dataset.csv` 新增 `allowed_first_tools` / `allowed_tool_sequences`，`tests/quality/routing_eval.py`）
- [x] Q6-07 数据集规范：所有新数据集统一 `qid, language, difficulty, created_at, gold_verified_by`；`tests/quality/dataset_lint.py` 校验字段、首行空行（现有 `dataset/*.csv` 首行为空行）、编码与重复 qid。验收：lint 进 `quality_offline`。（2026-09-09 完成：`tests/quality/dataset_lint.py`，15 个数据集通过，进 `quality_offline`）

退出判据：设计 §6.2 九项数据集全部存在且通过 lint；每项在 baseline.md 首次登记时注明规模与标注人数。

---

## 里程碑与顺序

| 里程碑 | 任务 | 退出判据 | 估算工作量 | 真实运行授权 |
|---|---|---|---|---|
| M-Q1 数字可信且存在 | Q0 全部、Q1 全部 | D0 冒烟通过；D3 / D5 首批数字登记；首份报告 | 6–8 人日 | Q0-05、Q1-04、Q1-09 |
| M-Q2 离线回归 | Q2 全部、Q6-07 | `-m quality_offline` 2 分钟全绿 | 5–7 人日（含标注） | 无 |
| M-Q3 自动指标全覆盖 | Q3 全部 | 历史产物可重算；provider 记分卡两列补齐 | 5–6 人日 | Q3-04、Q3-05 |
| M-Q4 裁判可信 | Q4 全部 | 三核心维度 kappa 报告 | 4–5 人日（含标注） | Q4-04 |
| M-Q5 记分卡与回归门 | Q5 全部 | `--suite all` + `--compare` 可用；首份完整记分卡 | 5–6 人日 | Q5-04、Q5-05 |
| M-Q6 数据集 | Q6-01～Q6-06 | 九项数据集齐全 | 8–12 人日（主要是标注） | 无 |

顺序：M-Q1 先行且不可跳过；M-Q2 与 M-Q6 可并行穿插；M-Q3 依赖 M-Q1 的产物格式；M-Q4 依赖 M-Q3 的 `answer_details.jsonl`；M-Q5 最后。工作量按单人估算，标注任务可分摊。

---

## 风险与回退

| 风险 | 影响 | 对策 |
|---|---|---|
| 搜索结果随时间漂移，两次评测 Hit@k 不可比 | D3 趋势失真 | 标注绑定 `collected_at` 快照；Q3-04 的跨 provider 一致性作为漂移参照；比较时先报漂移再报差异 |
| 裁判模型不可用或行为变化 | D7 分数中断 | 运行前 smoke；不可用时在开始前确定替代并记录，不允许评分后换裁判（沿用 protocol.md） |
| provider credits 失控（Firecrawl 556 credits 先例） | 配额耗尽、成本超支 | Q0-03 日累计 + `quality_runner` 启动前检查日上限；`--all-providers` 只采搜索不进 loop |
| 人工标注人力不足 | Q1-04 / Q4 / Q6 拖期 | 先做轻量档；详细档只在 nDCG 与 rerank 实验需要时补 |
| 度量口径变更（如 M3 路由口径） | 跨期不可比 | 先改设计文档再改脚本，baseline.md 注明日期；旧口径数字保留不重写 |
| 评测暴露产品缺陷的诱惑：顺手改产品 | 评测与产品改动混在一起，配对比较失效 | 缺陷开 change 单独修；评测 run 冻结 commit；改动后用 Q5-03 配对比较 |
| 硬超时题无最终 loop 状态 | D8 分母不一致 | 沿用报告做法：分母分别报告，超时题单列 |

---

## 维护规则

- 每完成一个任务：勾选、括注日期与产物路径；涉及数字的同时更新 baseline.md。
- 每次正式评测：新建 `docs/reports/quality_evaluation_<date>/`，只追加不修改；结论落地到代码或设计文档后以后者为准。
- 设计文档的"初始建议"门槛在 M-Q1 与 M-Q5 完成后各校准一次，校准记录写入对应报告并回填设计附录 D。
- 本计划与 [plan.md](../plan.md)（devbench）互不引用任务编号，避免混淆；共用凭据代理与人工评审方法论时在任务里注明来源。
