# ISE 质量评测报告（2026-09-18 真实运行）

这是按[质量评测实施计划](../../quality_evaluation_plan.md)做的第一轮真实运行报告。2026-09-18 晚上启动，2026-09-19 09:45 跑完，2026-09-21 做离线汇总。被测系统：commit `e3039118`（工作树有未提交改动），guided 模式，模型 `deepseek-v4-flash`（opencode-go），入口参数 max_tokens=4000、temperature=0.2、检索条数 5。评审裁判用 rubric v4，裁判模型 `glm-5.2`；循环内的终止判定模型是 `deepseek-v4-flash`。所有产物都在 `runtime/quality/20260918-quality-20260918/`（gitignored）。

自动生成的记分卡和逐题附表见 [scorecard.md](scorecard.md)。本报告负责解读这些数字，并登记缺陷。

## 1. 范围与证据等级

| 维度 | 本轮数据 | 状态 |
|---|---|---|
| D0 度量有效性 | 5 题真实冒烟（`runtime/quality/20260918-validity-20260918`） | 通过 |
| D1 查询理解 | 65 题离线（确定性层） | 与 0909 相同，未变 |
| D3 网页搜索 | 3 组采集共 88 题 + 全供应商 22 题 × 7 家 | 已采集；**人工标注未做**，只出无需标注的指标 |
| D4 抓取 | 本轮 98 题的抓取记录 | 完成 |
| D5 本地 RAG | 29 组参数扫描；0919 另做 embedding / rerank 对比 | 已登记 baseline §7.10 / §7.10.1 |
| D6 证据与权威 | 本轮台账（513 条）+ 离线分级 | 完成 |
| D7 答案 | final_answer 62 + open_task 20 + abstention 16 = 98 题，v4 裁判 | 完成 95 题（3 题裁判输出不合格式）；**人工双标未做** |
| D8 循环 | 本轮 98 题 | 完成（critic / judge 人工判定未做） |
| D9 成本 | 本轮 98 题，token 全部来自 transport 旁观 | 完成 |
| D10 可靠性 | final001–020 × 3 次（交替顺序），v4 裁判 | 完成 |
| D11 安全 | 310 个产物文件凭据扫描；22 页注入真实运行 | 完成 |

证据等级由高到低：真实运行产物 → 确定性离线重算 → v4 模型辅助评审（**非人工**，kappa 未校准）→ agent 单人构建的 gold。时效类 gold 只核实过 final037/039/041/059 四题。

## 2. D0 结论

冒烟和主链用的是同一个 commit，被测模型也相同。两边的配置摘要不同（`cc386c63…` 与 `6cb56330…`），原因是主链叠加了 `config.quality.json`，其中包括评审裁判、搜索回落顺序等评测参数。D0 检验的是计量链路，不受这层叠加影响，所以把这次冒烟作为主链的 D0。`validity.json` 已经复制进主链目录，出处也在这里写明。

token_capture_rate 1.0、usage_reconciliation_gap 0.0、tool_call_capture_ratio 1.0、search_call_capture_ratio 1.0、trace_completeness 1.0、audit_truncation_rate 0.0、param_forwarding_pass 1.0，**全部通过**，本轮数字可信。

## 3. 硬门槛：未通过

| 门槛 | 计数 | 题号 |
|---|---:|---|
| 核心事实错误（可答题 core_correct=0） | 16 / 79 | final014, final018, final020, final025, final040, final042, final043, final057, final058, final059, final062, open002, open004, open009, open014, open019 |
| 幻觉引用（`[En]` 无法解析） | 6 处 / 5 题 | abs008, final007, final016(2), final026, final027 |
| 未交付 | 2 | final014, final018（LLM 传输失败，见 §5 D7） |
| 假性耗尽 | 0 | — |
| 凭据泄漏 | 0 | — |

拒答题（16 题）不计入 core_correct，只看 `abstention_quality`。理由见 §9 的 QT-20260921-02：裁判给拒答题打的 core_correct 标准不一致，比如 abs013 正确拒绝提供私人电话，abstention=2，core 却给了 0。

## 4. 指数

| 指数 | 值 | 可用组成 | 说明 |
|---|---:|---:|---|
| 检索 | 0.807 | 3/6 | 只有本地 RAG 三项；网页搜索 hit@3 / nDCG 要等人工标注 |
| 证据 | 0.763 | 5/6 | tier_accuracy 0.856、official_precision 1.000、citation_recall 0.700、citation_precision 0.852、**authority_compliance 0.408** |
| 答案 | 0.781 | 5/5 | core_correct 0.797（95% CI [0.709, 0.886]，n=79）、request_completeness 0.805、**grounding 0.632**、open_task_aux 0.731、abstention_quality 0.938 |
| 过程 | 0.856 | 2/5 | members_f1 0.712（离线）、not_premature 1.000 |
| 可靠 | 0.900 | 2/3 | consistency@3 0.800、not_hard_timeout 1.000 |
| 成本 | 无基线 | — | 每题 token 均值 20,848（p95 54,198）；每个正确答案 27,341 token；时延 p50 118 s / p95 732 s |

## 5. 各维度要点

### D3 网页搜索（未标注部分）

- **`--force-search` 没有生效（QD-20260918-01）**：采集时传了强制搜索，但有 16/22（gold_doc 组）、4/12（minimal 组）、16/54（web_gold_zh 组）的题一次搜索都没发起。所以采集结果里“空结果率” 0.727 / 0.333 / 0.143 测的主要是没去搜，不是搜不到。标注前先修这个问题，否则 hit@3 的分母会被污染。
- 全供应商（22 题，每家独立请求 5 条，不进循环），gold_doc 命中率：tavily 0.364、brave 0.318、firecrawl 0.318、anysearch 0.273、google 0.273、parallel 0.227、brightdata 0.045。brightdata 可用率只有 0.318，p50 时延 20.5 s，其他家都 ≤ 2.2 s。Top-1 跨家一致率 0.663；两两 Jaccard 最高是 brave|tavily 0.464，brightdata 与各家都在 0.05 以下。已知 credits 只有 firecrawl 的 44。
- 当前链首用的是 anysearch，它在命中率上排中游。要不要换链首，得等标注出了相关性再定。

### D4 抓取

98 题里 35 题发起了抓取，共 53 次，最终成功率 0.981，每次成功平均尝试 1.30 个抽取器。direct_fetch 成功率 0.755，parallel_extract 0.846。截断率 0.462，gold_span 在已抓取页上的包含率 1.000，样本很小。

### D6 证据与权威

台账 513 条：retained 42.9%、limited 19.1%、rejected 38.0%，merged 163 条；retained 里权威来源占 27.2%（历史 r2 是 12.4%）。29 题没有任何证据。`aggregator_leak_rate` 0.119，authority_required 题是 0.030，未达门槛 0：open010 等题引用了聚合站。离线分级和 0909 一致，4 个仿冒 TLD 仍被判为 first_party。

### D7 答案质量

16 道可答题判 0，按原因分类：

| 类别 | 题 | 说明 |
|---|---|---|
| 降级合成只输出模板（QD-20260918-03） | final020, final040, final057, final062, open002, open004, open009 | 答案只有“迭代次数用尽，未能获得完整答案”。裁判指出 final040、final062 的证据里其实有答案。open002 的终态还是 succeeded |
| 不检索就停滞（QD-20260918-02） | final025, open014 | 模型两轮都直接作答、没调工具，critic 两次拒收后判 stagnated，最后只输出“检索陷入停滞”，连模型的候选答案也丢了。final025 问的是水的沸点 |
| 时效事实与 gold 冲突，**待人工核实** | final042（Rust 版本）、final043（Brave 免费额度）、final058（Sonnet 5 价格）、final059（DeepSeek V4 Flash 价格） | 答案都引用了官方页，但与 gold 不一致。gold 可能已过期，其中只有 final059 核实过 |
| 误触发澄清（QD-20260918-05） | open019 | 解释型问题被当成对比题，回答“需要先确认比较对象” |
| LLM 传输失败 | final014, final018 | opencode.ai 重试 4 次后连接失败，运行 1,000 多秒后被 deadline 取消。属于设施问题，不是产品问题 |

其他发现：

- **降级前缀重复（QD-20260918-04）**：46/98 份答案里“现有证据或执行预算不足，以下仅为当前候选信息……”出现了两次。
- 引用：citation_recall 0.700；**authority_compliance 0.408**（107 处引用来源不权威）；需要时效的题只有 4/19 标注了时间。36 份答案没有任何引用。
- grounding 0.632 是五项答案指标里最低的。
- 过早成功：succeeded 的 29 题里有 5 题 core 不满分（0.172），结构性指标 premature_success_rate 是 0，说明这个指标没抓到这类情况。

### D8 循环与终止

- 终态：evidence_insufficient 47、succeeded 30、stagnated 16、cancelled 2、无终态 3（2 题传输失败，1 题澄清）。平均 4.77 轮。
- **stagnated 的 16 题都没发起过搜索**：模型直答，critic `final_answer_rejected`，第二轮还是不调工具。0918 冒烟里已经看到这个现象，这次规模更大。
- 强制合成率 0.558，其中 core 满分的占 0.686；降级合成率 0.484，其中 core 满分的占 0.818。也就是说降级合成的答案大多是对的，出问题的是 QD-03 那类只剩模板的情况。
- final_answer 被拒收的题有 78 道，平均拒收 1.38 次，拒收后最终 core 满分的占 0.76。
- judge_error_rate 0.151；judge 阶段时延 p50 42.6 s，比 act 阶段（p50 34.0 s）还长，是时延的主要来源之一。

### D9 成本

token 均值 20,848，p50 17,323，p95 54,198；每题平均 6.7 次 LLM 调用。时延 p50 118 s，p95 732 s，最长 1,263 s（open003）。预算耗尽的题占了 79.2% 的 token。最贵的是 open015（103,472 token / 576 s）、open005（87,647 / 278 s）、open004（64,776 / 373 s）。

### D10 可靠性

final001–020 每题跑 3 次：consistency@3 0.80、pass@1 0.80、pass@3 0.90、hard_timeout 0。不一致的 4 题是 final005、final010、final014、final019，都是某一轮 core=0；这几题的终态在 stagnated、evidence_insufficient、succeeded 之间来回变。说明 QD-02 这种停滞不是题目本身导致的，而是随机发生的。soft_cancel_correctness 本轮没有取消样本，记为 null。

### D11 安全

310 个文件、12 个已配置密钥：凭据泄漏 0，URL 密钥泄漏 0，PII 脱敏 1.0，denylist 遵从 1.0。注入页真实运行 22 页，注入抵抗率 1.0，canary 泄漏 0。

## 6. 产物登记

| 产物 | 说明 |
|---|---|
| `answer_details.jsonl`、`answer_records/`、`reviews-v4/`（98 份，3 份 review_failed） | 答案套件与 v4 评审 |
| `reliability_records/`、`reliability_runs/reviews-v4/`（60 份） | 可靠性套件 |
| `search_collect_{minimal,gold_doc,web_gold_zh,all_providers}.json` | 搜索采集原件，**标注输入** |
| `search_eval_unannotated_*.json` | 无标注评估（2026-09-21 补跑，离线） |
| `validity.json` | 从冒烟目录复制，见 §2 |
| `injection_eval.json` | 从 `chain.log` 提取（链脚本没传 `--output-file`） |
| `*.pre-fix.json`、`report.pre-fix.md` | 评审关联修复之前的版本，留作对照 |

## 7. 复现命令

```bash
R=runtime/quality/20260918-quality-20260918
# 离线重算（不发网络请求）
python -m tests.quality.citation_eval --source $R --output-file $R/citation_eval.json
python -m tests.quality.evidence_eval --source $R --output-file $R/evidence_eval.json
python -m tests.quality.loop_eval     --source $R --output-file $R/loop_eval.json
python -m tests.quality.cost_eval     --source $R --output-file $R/cost.json
python -m tests.quality.fetch_eval    --source $R --output-file $R/fetch_eval.json
python -m tests.quality.analysis_eval --output-file $R/analysis_eval.json
python -m tests.quality.tiering_eval  --output-file $R/evidence_eval_offline.json
for f in gold_doc minimal web_gold_zh all_providers; do
  python tests/search_quality_pipeline.py evaluate --annotations-file $R/search_collect_$f.json --gold-doc-file dataset/gold_doc_dataset.csv --output-file $R/search_eval_unannotated_$f.json
done
python -m tests.quality_runner --suite reliability --max-queries 20 --run-dir $R   # 已有记录与评审全部跳过，只重算 reliability.json
python -m tests.quality_report --run $R
# 真实运行命令见 runtime/quality/…/run_meta.json 的 arguments 与计划 §0.4
```

## 8. 局限

- 答案分全部来自模型裁判，没有人工双标，kappa 未知。Q4-04 的人工部分没做之前，core_correct 只能当参考。
- 时效类 gold 大多没核实。§5 D7 的 4 道冲突题可能是 gold 过期。
- 网页搜索没有相关性标注，hit@3、MRR、nDCG 都缺；而且 QD-01 会让采集结果少很多搜索调用。
- 单模型、单模式（guided）、单时段，没有 autonomous 对照。
- 工作树有未提交改动，run_meta 记了 commit，但不能精确还原当时的代码。

## 9. 缺陷登记

产品缺陷（按计划要求另开 change 修，不在评测里顺手改）：

| 编号 | 缺陷 | 证据 |
|---|---|---|
| QD-20260918-01 | `force_search` 传到了 `ReactAgentOrchestrator.answer`，但只写在 docstring 里，代码没用它，循环不会被强制搜索 | 三组采集里 16/22、4/12、16/54 题零搜索；`orchestrators/react_agent_orchestrator.py` |
| QD-20260918-02 | 模型不调工具直接作答，critic 拒收后循环没有转向检索，两轮后以 stagnated 结束，最终输出只剩停滞模板、丢掉候选答案 | stagnated 16/16 题零搜索；final025、open014 判 0；可靠性套件里 4 题因此不稳定 |
| QD-20260918-03 | 降级合成在有证据时只输出“迭代次数用尽”模板 | 9 题：7 道可答题判 0（final020/040/057/062、open002/004/009，其中 open002 终态为 succeeded）+ 拒答题 abs006/007 |
| QD-20260918-04 | 降级前缀重复拼接 | 46/98 份答案 |
| QD-20260918-05 | 非对比的解释型问题触发了对比澄清 | open019 |

评测工具缺陷（本次已修，改动在 `tests/`）：

| 编号 | 缺陷 | 修复 |
|---|---|---|
| QT-20260921-01 | `answer_details.jsonl` 的答案行没有关联 `reviews-v4`；可靠性套件读评审时只找 `reviews-v3-evidence`。结果自动报告里 D7 全部“未运行”，consistency@3 也是 null | `tests/quality/common.py`：`load_study_reviews` 默认读全部版本（新版本覆盖旧版本），新增 `attach_reviews` / `record_digest`，按 quality_review 相同的摘要关联；测试 `test_jsonl_answer_rows_bind_reviews_by_pre_attachment_digest` |
| QT-20260921-02 | 拒答题的 core_correct 进了硬门槛和答案指数 | `tests/quality_report.py`：`abstention` 非空的行 core_correct 记为 None；测试 `test_abstention_questions_are_scored_by_abstention_not_core` |
| QT-20260921-03 | 报告把循环内终止判定模型当成了评审裁判 | 报告分开列出“评审裁判”和“循环内终止判定” |
| QT-20260921-04 | 3 题（final029/051/060）的裁判输出 `semantic_fact_match` 少列了一条必含事实，两次尝试都被 rubric 拒收 | 未修，按未评处理。重跑要删掉对应评审文件，但评审协议摘要已变化，quality_review 会拒绝重跑，需要连同协议一起处理 |
