# ISE 质量评测首份报告（2026-09-09）

按 [质量评测实施计划](../../quality_evaluation_plan.md) 施工的第一份报告。本轮**没有授权任何真实运行**（不消耗搜索配额、LLM 调用与 provider credits），因此 D3 采集、D5 embedding 扫描、D0 冒烟、裁判 v4 校准、全供应商采集与重复一致性全部标 **未运行**；报告里的数字来自三类离线来源：

1. **零成本离线回归**（`python -m tests.quality_runner --suite offline`，产物 `runtime/quality/20260909-offline/`，`TransportObserver` 确认 0 次网络请求）：D1、D6 分级/解析器回放、D2 preflight、D11 注入结构检查、数据集 lint、本地 gold 定位。
2. **对历史真实产物的离线重算**（`runtime/baseline/autonomy-20260908-measured/r1|r2`，2026-09-08 两轮 80+80 条真实运行，产物 `runtime/quality/20260909-historical-r1|r2/`）：D4、D6 台账、D7 引用、D8 循环、D9 成本，以及按 qid 配对的两模式比较。
3. **禁网探针**：`tests/quality/test_param_forwarding.py` 用真实 builder 捕获 loop 的线上请求体。

证据等级从高到低：真实运行产物 → 确定性离线重算 → v3-evidence 模型辅助评审（历史产物自带，非 v4，非人工） → 本次由 agent 单人构建的 gold（`gold_verified_by` 标明"unverified by a second annotator"）。

自动生成的记分卡见 [historical_r2_scorecard.md](historical_r2_scorecard.md)（含逐题附表）与 [offline_scorecard.md](offline_scorecard.md)。

## 1. 范围与证据等级

| 维度 | 本轮数据来源 | 状态 |
|---|---|---|
| D0 度量有效性 | 禁网参数透传探针；5 题真实冒烟 | 探针完成；冒烟未运行 |
| D1 查询理解 | `dataset/query_analysis_gold.csv`（65 题）离线 | 完成 |
| D2 路由 | `skills/*/evals/cases.jsonl`（161 例）离线；routing_eval 需要运行产物 | preflight 完成；路由指标未运行 |
| D3 网页搜索 | 需 `collect` 真实采集 + 人工标注 | 未运行（脚本与 gold 已就绪） |
| D4 抓取 | 历史 r1/r2 重算 | 完成（样本小） |
| D5 本地 RAG | 需 embedding 扫描 | 未运行（语料 16 文件 / 209 chunk、gold 38 题已就绪） |
| D6 证据与权威 | 167 URL / 78 实体离线 + 历史台账 | 完成 |
| D7 答案 | 历史 r1/r2 引用重算 + v3 裁判分 | 引用部分完成；v4 裁判与人工校准未运行 |
| D8 循环 | 历史 r1/r2 重算 | 完成（critic/judge 人工判定未运行） |
| D9 成本 | 历史 r1/r2 重算 | 完成（credits 账本自本次起才有） |
| D10 可靠性 | 故障注入 8 例离线 | 注入完成；重复一致性未运行 |
| D11 安全 | 历史 r2 产物凭据扫描；22 页注入结构检查 | 完成（注入抵抗率需真实运行） |

## 2. D0 结论

- `param_forwarding_pass`：**未通过（按缺陷登记）**。禁网探针证实入口 `max_tokens=4000 / temperature=0.2 / num_search_results=3` 到达 loop 时分别为 `5000 / 0.7 / 5`（模型对象默认值与 `web_search` 固定 `num_results=5`），`autonomy` 正确到达。三条断言以严格 xfail 固定，缺陷编号 **QD-20260909-01**（生成参数）与 **QD-20260909-02**（检索条数），change 见 `openspec/changes/forward-entry-generation-params/`。
- `token_capture_rate`、`usage_reconciliation_gap`、`tool_call_capture_ratio`、`search_call_capture_ratio`、`trace_completeness`、`audit_truncation_rate`：**未运行**（`python -m tests.quality.validity --max-queries 5` 已可执行，需授权 < 10 分钟的真实调用）。分母侧的代理计数（`TransportObserver` 非 LLM 请求分类）与分子侧的补记（`search_recovery` 内部 provider 请求、resolver 发现/验证/探针、抽取器逐次尝试进入 `response_times.tool_calls`，`kind` ∈ search / extract / resolver_*）已落地并有单元测试。
- 历史产物的 D0 已知缺口不变：r2 应用记账 token 有 75/80 题只能从 transport 旁观恢复（`cost.json` 的 `token_sources`）。

按设计 §4 D0 规则，后续各节的历史数字只能视为**未经 D0 冒烟确认**的重算结果。

## 3. 硬门槛（历史 r2，80 题）

| 门槛 | 计数 | 题号 |
|---|---:|---|
| 核心事实错误（core_correct=0，v3 裁判） | 1 | final016（autonomous，两轮均错） |
| 幻觉引用（`citation_unresolved`） | 7 处 / 3 题 | final007、final012（guided）、open002（autonomous） |
| 未交付（delivered=false） | 4 | open004（autonomous 硬超时）、open008/013/018（guided 硬超时） |
| 假性耗尽 | 0 / 3 exhausted | — |
| 凭据泄漏（397 个文件扫描，12 个配置密钥值） | 0 | — |
| 触顶预算调用 | 0 | — |

r1 同口径：核心错误 1（final016）、幻觉引用 7 处 / 2 题（final018、open002）、假性耗尽 0 / 9。硬门槛结论：**未通过**（核心事实错误与幻觉引用非零）。幻觉引用是本次新暴露的问题：原报告只统计了 `unresolved_citation_ids` 的存在，没有把它列为硬门槛。

## 4. 六个指数

| 指数 | 历史 r2 | 离线 run | 组成说明 |
|---|---:|---:|---|
| 检索指数 | 1.000（1/6 组成） | 未运行 | 只有 `gold_span_containment(when fetched)`=1.0（n=1）可用；`hit_at_3`、`gold_doc_recall`、`ndcg`、`chunk_hit_at_3`、`mrr_chunk` 未运行——这个 1.0 不能当检索分数 |
| 证据指数 | 0.639（4/6） | 0.928（2/6） | tier_accuracy(collapsed) 0.856、official_precision 1.000、citation_recall 0.486、authority_compliance 0.215；member_coverage_f1 与 citation_precision（需 v4 裁判）未运行 |
| 答案指数 | 0.798（4/5） | 未运行 | core_correct/2 0.975、request_completeness/2 0.988、grounding/2 0.463、open aux 0.766（v3 裁判）；abstention 未运行 |
| 过程指数 | 0.847（2/5） | 0.712（1/5） | members_f1 0.712；1 − premature_success_rate 0.981；route_accuracy、critic_block_precision、judge_agreement 未运行 |
| 成本指数 | 无基线（只报绝对值） | — | r2 token 均值 18,963、P95 时延 438.5 s、每正确答案 7,333 token |
| 可靠指数 | 未运行 | 1.000（1/3） | 故障注入 8/8；consistency_at_3、hard_timeout_rate 未运行 |

指数是可用组成的均值，组成缺失时不补零；两个 run 的指数不能横向比较（组成不同）。core_correct/2 的 bootstrap 95% 区间 [0.925, 1.000]（n=40）。

## 5. 各维度要点与三条失败

### D1 查询理解（`analysis_eval.json`，65 题，确定性层，无 LLM 纠错）

- intent_shape 准确率 0.846（对比类被判 information_request 9 题：隐式对比线索"哪个更…/有什么不同/versus"未覆盖）；对比类成员 P/R/F1 = 0.867 / 0.605 / 0.712；**noise_member_rate 0.133（门槛 0，未通过，QD-20260909-04）**；实体 P/R = 0.474 / 0.846；claim_classes micro-F1 0.747；critical_ambiguity P/R = 0.80 / 0.73（门槛 P ≥ 0.9 未通过）；existence_query 精确率 0.60；time_scope 准确率 0.908；**false_temporal_fanout_rate 0.0（通过）**。
- 三条失败：qa015 "the three largest cloud providers on egress" 被当成成员；qa019 "Zig for systems programming" 尾句进入成员；qa022 "前者/后者" 指代未判为关键歧义（澄清漏报）。

### D2 路由（`preflight_eval.json`）

| skill | 例数 | precision | recall | known_gap |
|---|---:|---:|---:|---:|
| finance | 34 | 0.720 | 1.000 | 7 |
| weather | 34 | 0.655 | 0.950 | 11 |
| location | 32 | 0.680 | 1.000 | 8 |
| transportation | 31 | 0.536 | 1.000 | 13 |
| sports | 30 | 0.500 | 1.000 | 14 |

- preflight_precision 门槛 0.95 全部未通过：`handles_query` 是宽松词表匹配（"train a neural network"、"Route 53"、"credit score"、"从零开始" 都被路由到 skill）。53 条 known_gap 已固定在 `cases.jsonl` 里，pytest 只钉住观察值，不掩盖缺口。
- 三条失败：sports "Game theory and the Nash equilibrium"；transportation "Train a neural network from scratch"；location "离职后社保怎么办"。

### D3 网页搜索：未运行

`collect` 已对齐当前 `control`（`query_analysis` / `execution_trace` / `evidence_coverage` / `loop_verdicts` / `tool_budgets` / `search_api_calls` / 带 `eid` 的 `evidence_records`），`evaluate` 新增 nDCG@5、gold_doc_recall@3/5、authoritative@k、aggregator@k、domain_diversity@5、empty_result_rate、按类别宏平均与 provider 记分卡；`--all-providers` 模式与 `unique_yield` / 跨供应商一致性已实现并有单元测试。gold：`gold_doc_dataset.csv` 22 条 + 新增 `web_gold_zh.csv` 54 条（54/54 当日抓取且 gold_span 在正文中，`tests/quality/web_gold_check.py --fetch`）。

### D4 抓取（历史 r1 / r2）

- r2：8 题发生抓取、11 次尝试，`fetch_success_rate` 1.0，每次成功 1.25 次抽取器尝试（direct_fetch 10/11、firecrawl_scrape 1/1、parallel_extract 0/1），`content_sufficiency_rate` 1.0，已抓记录 60.7% 被 8000 字截断；r1：10 题 / 17 次，成功率 1.0，截断率 70.6%。
- `gold_span_containment`：仅 1 题同时有 gold 与抓取（含有率 1/1）；40 题 gold_chunk 里绝大多数没有触发抓取（autonomous 只用摘要），样本不足以下结论。
- 三条失败：parallel_extract r2 0/1；截断率 > 60%（`truncation_loss_rate` 尚无可判样本）；抓取覆盖率低（8/80 题）。

### D5 本地 RAG：未运行

固定语料 `tests/fixtures/local_corpus/`（16 文件、中英各 8、md/txt/pdf，209 chunk @1000/200）与 `dataset/local_chunk_gold.csv`（38 题：多 span 10、`is_absent` 6、跨语言 7，全部可定位）已就绪；`tests/local_chunk_grid_search.py` 新增 chunk 级指标（`chunk_hit_at_k` / `mrr_chunk` / `context_recall` / `score_margin` / `abstention_candidates` / `cross_lingual_hit`，`--top-k 3,5`）。发现并修复了一个环境缺陷：`env1` 缺 `markdown` 包导致所有 `.md` 文件被静默跳过（QD-20260909-03，`LangChainFileReader` 已加纯文本回落）。

### D6 证据与权威

- 离线分级（167 URL）：strict 5×5 准确率 0.671，权威折叠后 0.856；**official_precision 1.000（通过）**、official_recall 0.457（未 pin 的官方域只能到 first_party 或 unknown）；**denylist_compliance 1.0、non_evidence_exclusion 1.0（通过）**；4 个仿冒 TLD（openai.zip / deepseek.top / kimi.vip / mongodb.info）被词干启发式判为 first_party（`lookalike_false_authority_rate` 0.133）。
- 解析器回放（78 实体，缓存快照 242 stems）：`resolver_accuracy` 0.282（pinned 15/15，未 pin 7/63），`resolver_none_rate` 0.654；缓存里 4 个错误 official（PostgreSQL→anxs.io、Google→wordpress.com、Microsoft→aka.ms、Cloud Run→cloudrun.io）。
- 历史台账 r2：413 条目 → retained 34.1% / limited 14.0% / rejected 51.8%，merged 101；retained 中权威占 12.4%；37/80 题无任何证据（autonomous 直答）；`aggregator_leak_rate` 0.063，authority_required 题 0（通过）。
- 三条失败：cloud.tencent.com 官方产品页因整域在 never_official 表被判 aggregator；仿冒 TLD 获 first_party；缓存把 PostgreSQL 解析到 anxs.io。

### D7 答案质量（历史 r2，v3-evidence 裁判，非人工）

- core_correct=2：39/40；request_completeness/2 0.988；evidence_support/2 0.463。
- 引用（离线重跑 `check_citations`）：`citation_recall` 0.486（guided 事实题 1.0，autonomous 事实题 0.255）、`authority_compliance` 0.215、`pricing_source_compliance` 0.947、`recency_compliance` 0/3、每答引用 3.1（37/80 题零引用）、`unverified_hedge_rate` 0.085；失败类型合计：missing 162、not_authoritative 102、unresolved 7、needs_official_source 5、recency_missing 3；与 loop 末轮引用判定抽样 10 题一致 10/10。
- 三条失败：final016 autonomous 核心日期错（两轮）；final007 / final012 guided 幻觉引用；autonomous 事实题 15/20 零引用。

### D8 循环与终止（历史）

| 组别（r2） | 有状态 | 平均迭代 | 状态分布 | judge_error_rate |
|---|---:|---:|---|---:|
| 事实 guided | 20 | 3.20 | succeeded 9 / evidence_insufficient 9 / stagnated 2 | 0.141 |
| 事实 autonomous | 20 | 1.05 | succeeded 20 | 0 |
| 开放 guided | 16 | 4.75 | evidence_insufficient 11 / succeeded 4 / stagnated 1 | 0.224 |
| 开放 autonomous | 19 | 1.42 | succeeded 19 | 0 |

与原报告 §4.4 的迭代数、advisory 条目（1.00 / 1.68）与峰值上下文比（0.092 / 0.077 / 0.301 / 0.131）逐项一致。全量：**false_exhaustion_rate 0（通过）**；premature_success_rate 0.019（final016：succeeded 且 core_correct=0）；forced_synthesis_rate 0.267、degraded 0.253（其后核心正确率 9/9）；`final_answer_rejected` 25 题；judge_invocation_rate 0.410、**judge_error_rate 0.138（门槛 ≤ 0.05 未通过）**；invalid_tool_request_rate 0.005；narration guard 0；no_progress_streak P95 2；首次答案平均第 1.47 轮；advisory gap 52 条、**忽略率 0.923**；压缩 0。r1：judge_error_rate 0.072、advisory 忽略率 0.957、premature 0.018。

三条失败：judge 超时/不可解析占比过高；advisory 缺口几乎不被后续动作回应；guided 开放题 11/16 以 evidence_insufficient 结束。

### D9 成本（历史 r2）

- token 均值 18,963（P50 5,239 / P95 76,892，75/80 题取自 transport 旁观）；时延 P50 34.3 s / P95 438.5 s；阶段均值：act 45.2 s、judge 25.7 s、synthesize 5.9 s、search 5.2 s、fetch 1.1 s。
- `budget_exhaustion_cost`：23 题非成功终态平均 53,731 token / 231.8 s，占总 token **81.5%**（门槛 ≤ 10% 未通过；guided 开放题 94.4%）。
- 每正确答案 token 7,333（事实题：guided 12,452 / autonomous 1,944）；credits 账本本次才启用，历史为 null；USD 未知。
- 三条失败：open010 guided 265,605 token / 578 s；open009 guided；guided 开放题 P95 575 s。

### D10 可靠性

故障注入 8/8 通过（回落层级、`fallback` 与 `reason` 记录、Brave 月配额切换、`batch_sizes` 默认与自定义、LLM 4xx/5xx 如实暴露且不换模型）。consistency_at_3、hard_timeout_rate（历史 r2 硬超时 4/80）、soft_cancel_correctness 需重复运行。

### D11 安全

历史 r2 产物 397 文件扫描：凭据泄漏 0、URL 密钥 0（audit 记录数 0——研究运行的 audit 目录名不同，脱敏检查空集）。注入集 22 页：全部可加载、canary 不进入台账头部、伪官方页仍为 unknown、`[E99]` 不可解析；`injection_resistance` 需 `--live` 真实运行。

## 6. 数据集与产物登记

| 数据集 | 规模 | 标注人 |
|---|---:|---|
| `dataset/query_analysis_gold.csv` | 65（对比 22 / 时效 17 / 歧义 11 / 闲聊本地 15） | agent 单人 |
| `dataset/source_tier_gold.csv` | 167 URL（official 92 / aggregator 25 / excluded 15 / unknown 35，含 14 仿冒） | agent 单人 |
| `dataset/official_domain_gold.csv` | 78 实体（未 pin 63） | agent 单人 |
| `dataset/local_chunk_gold.csv` + `tests/fixtures/local_corpus/` | 38 题 / 16 文件 209 chunk | agent 单人，脚本校验 |
| `dataset/web_gold_zh.csv` | 54（文档定位 28 / 政策 FAQ 11 / 价格 8 / 对比 7） | agent 单人，54/54 抓取校验 |
| `dataset/final_answer_dataset.csv` | 20 → 62（中文 29、时效 10、对比 10、价格 6）；final041/059 按官方页勘误，其余时效 gold 标 UNVERIFIED | agent 单人 |
| `dataset/abstention_set.csv` | 16 | agent 单人 |
| `dataset/hard_loop_set.csv` | 54（每类 ≥ 10，`expected_loop_behavior`） | agent 单人 |
| `dataset/adversarial_pages/` | 22 页 + 索引 | agent 单人 |
| `dataset/multi_turn_set.csv` | 16 组 × 3 轮 | agent 单人 |
| `dataset/route_intent_dataset.csv` | 57（新增 `allowed_first_tools` / `allowed_tool_sequences`） | agent 单人 |
| `skills/*/evals/cases.jsonl` | 161（含 53 known_gap） | agent 单人 |

全部数据集通过 `tests/quality/dataset_lint.py`。

## 7. 复现命令

```bash
# 零成本离线回归（本报告第 1 类数字）
env1/bin/python -m pytest -q -m quality_offline
env1/bin/python -m tests.quality_runner --suite offline --run-dir runtime/quality/20260909-offline

# 历史产物重算（第 2 类数字）
for r in r1 r2; do
  for m in citation_eval evidence_eval loop_eval fetch_eval; do
    env1/bin/python -m tests.quality.$m --source runtime/baseline/autonomy-20260908-measured/$r --output-file runtime/quality/20260909-historical-$r/${m}.json
  done
  env1/bin/python -m tests.quality.cost_eval --source runtime/baseline/autonomy-20260908-measured/$r --output-file runtime/quality/20260909-historical-$r/cost.json
done
env1/bin/python -m tests.quality.safety_eval --run runtime/baseline/autonomy-20260908-measured/r2 --output-file runtime/quality/20260909-historical-r2/safety.json
env1/bin/python -m tests.quality_report --run runtime/quality/20260909-historical-r2 --answers runtime/baseline/autonomy-20260908-measured/r2
env1/bin/python -m tests.quality_report --compare-modes runtime/baseline/autonomy-20260908-measured/r2

# 参数透传探针（禁网）
env1/bin/python -m pytest -q tests/quality/test_param_forwarding.py -rxX

# 待授权的真实运行（预算见计划 §0.4）
env1/bin/python -m tests.quality.validity --max-queries 5                     # Q0-05
env1/bin/python -m tests.quality_runner --suite search                          # Q1-04 + Q3-04
env1/bin/python -m tests.quality_runner --suite local                           # Q1-09（仅 embedding）
env1/bin/python -m tests.quality_runner --suite answer --datasets final_answer,open_task,abstention   # Q4-04
env1/bin/python -m tests.quality_runner --suite reliability --max-queries 20   # Q5-05
env1/bin/python -m tests.quality.injection_eval --live                          # D11 注入抵抗
```

## 8. 局限

- 没有一项数字经过 D0 真实冒烟确认；历史 token 数是 transport 旁观下界。
- 裁判分来自历史 v3-evidence 评审，不是 v4，也不是人工；kappa 校准未做，答案指数只能作参考。
- 所有新 gold 由同一 agent 单人构建，未经第二人复核；时效类 gold 除 final037/039/041/059 外未对照官方页核实，使用前须复核。
- `tier_accuracy` 的 gold 把"真官方但未 pin"记为 official，因此 strict 准确率偏低是口径使然，折叠后的 0.856 更能反映权威判定质量。
- D3/D5 的检索分数——本计划的最小可交付——仍缺真实采集与 embedding 扫描。

## 9. 缺陷登记

| 编号 | 内容 | 登记位置 |
|---|---|---|
| QD-20260909-01 | 入口 `max_tokens` / `temperature` 未到达 loop 模型调用 | `openspec/changes/forward-entry-generation-params/`；`tests/quality/test_param_forwarding.py` 严格 xfail |
| QD-20260909-02 | 入口 `num_search_results` 未到达 `web_search` | 同上 |
| QD-20260909-03 | `env1` 缺 `markdown` 包，`.md` 本地文档被静默跳过 | 已在 `LangChainFileReader` 加纯文本回落；requirements 未补依赖（待决定） |
| QD-20260909-04 | 确定性对比成员抽取保留指令尾句/限定语 | `tests/quality/test_analysis_eval.py` 严格 xfail |
| QD-20260909-05 | skill `handles_query` 词表宽松，53 例困难负例误路由 | `cases.jsonl` known_gap；`preflight_eval.json` |
