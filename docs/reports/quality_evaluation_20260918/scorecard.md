# ISE 质量评测报告（20260918-quality-20260918）

生成时间 2026-09-21T13:05:45.777848+00:00。产物目录 `runtime/quality/20260918-quality-20260918`；答案记录来源 `runtime/quality/20260918-quality-20260918`。本报告由 `python -m tests.quality_report` 从运行产物生成，缺失的维度标为 **未运行**，不会写成 0。

## 1. 范围与证据等级

- 题数 98；有裁判/人工判定的题数 95。
- 冻结信息：commit `e3039118dce0d94d1914ac1560a27864cc4ecc53`，工作树有未提交改动；配置摘要 `6cb563302f3ee29efa0440f65167f49c577da4f14fecd4b7d47796adf37a5c0d`；模型 `deepseek-v4-flash`；评审裁判 `glm-5.2`；循环内终止判定 `deepseek-v4-flash`；rubric `v4`。
- 证据等级：真实运行产物（answer_details / result.json）→ 确定性离线重算（citation / evidence / loop / cost / fetch）→ 模型辅助评审（reviews）→ 人工标注（dataset/annotations）。裁判分不是人工分；核心正确性单列且拥有一票否决。

## 2. D0 度量有效性

- 结论：通过

| 指标 | 值 | 门槛判定 |
|---|---:|---|
| `token_capture_rate` | 1.000 | True |
| `usage_reconciliation_gap` | 0.000 | True |
| `tool_call_capture_ratio` | 1.000 | True |
| `tool_call_capture_ratio_raw` | 1.000 | — |
| `search_call_capture_ratio` | 1.000 | True |
| `trace_completeness` | 1.000 | True |
| `trace_truncated_questions` | 0 | — |
| `audit_truncation_rate` | 0.000 | True |
| `audit_records` | 5 | — |
| `param_forwarding_pass` | 1.000 | True |

## 3. 硬门槛（任一非零即未通过）

| 门槛 | 计数 | 题号 |
|---|---:|---|
| 核心事实错误（core_correct=0） | 16 | final014, final018, final020, final025, final040, final042, final043, final057, final058, final059, final062, open002, open004, open009, open014, open019 |
| 幻觉引用（citation_unresolved） | 6 | abs008, final007, final016, final026, final027 |
| 未交付（delivered=false） | 2 | final014, final018 |
| 假性耗尽 | 0 | — |
| 凭据泄漏 | 0 | — |
| 触顶预算调用 | 6 | — |

硬门槛结论：未通过；未运行的门槛：无。

## 4. 六个指数

| 指数 | 值 | 可用组成/总组成 | 组成 |
|---|---:|---:|---|
| 检索指数 | 0.807 | 3/6 | hit_at_3=未运行；gold_doc_recall_at_5=未运行；ndcg_at_5=未运行；gold_span_containment=1.000；chunk_hit_at_3=0.750；mrr_chunk=0.672 |
| 证据指数 | 0.763 | 5/6 | tier_accuracy=0.856；official_precision=1.000；member_coverage_f1=未运行；citation_recall=0.700；citation_precision=0.852；authority_compliance=0.408 |
| 答案指数 | 0.781 | 5/5 | core_correct=0.797；request_completeness=0.805；grounding=0.632；open_task_aux=0.731；abstention_quality=0.938 |
| 过程指数 | 0.773 | 2/5 | members_f1=0.712；route_accuracy=未运行；critic_block_precision=未运行；judge_agreement=未运行；not_premature=0.833 |
| 可靠指数 | 0.900 | 2/3 | consistency_at_3=0.800；not_hard_timeout=1.000；fault_injection_pass_rate=未运行 |
| 成本指数（只报比值） | 无基线 | — | tokens 均值 20848；P95 时延 732300 ms；每正确答案 token 27341 |

core_correct/2 的 bootstrap 95% 区间：[0.709, 0.886]（n=79）。

## 5. 各维度要点与三条失败

### D1 查询理解（analysis_eval.json）

- intent_shape 准确率 0.846；对比类成员 F1 0.712；noise_member_rate 0.133；false_temporal_fanout_rate 0.000
- 三条失败：noise: the three largest cloud providers on egress（qa015）；noise: Zig for systems programming（qa019）；noise: 前者, 后者（qa022）

### D3 网页搜索（search_report.json）

- 未运行
- 三条失败：无（或未运行）

### D4 抓取与抽取（fetch_eval.json）

- fetch_success_rate 0.981；attempts/success 1.295；gold_span_containment(when fetched) 1.000；truncation_rate 0.462
- 三条失败：abs002 抓取 1/2

### D6 证据与权威（evidence_eval*.json）

- tier_accuracy(collapsed) 0.856；official_precision 1.000；resolver_accuracy 0.282；aggregator_leak_rate 0.119
- 三条失败：lookalike 被判权威：https://openai.zip/；lookalike 被判权威：https://deepseek.top/；lookalike 被判权威：https://kimi.vip/

### D7 答案质量（reviews + citation_eval.json）

- core_correct=2 比例 未运行；citation_recall 0.700；authority_compliance 0.408；hallucinated_citation_count 6
- 三条失败：final014（guided）core_correct=0；final018（guided）core_correct=0；final020（guided）core_correct=0

### D8 循环与终止（loop_eval.json）

- loop_status 分布 {"evidence_insufficient": 47, "succeeded": 30, "stagnated": 16, "cancelled": 2}；false_exhaustion_rate 0.000；premature_success_rate 0.167；judge_error_rate 0.151；advisory_gap_ignore_rate 未运行
- 三条失败：abs013 提前成功；abs016 提前成功；final043 提前成功

### D9 成本（cost.json）

- tokens 均值 20848；时延 P50/P95 117891/732300 ms；预算耗尽题 token 占比 0.792；每正确答案 token 27341
- 三条失败：open015（guided）103,472 token / 576.200 s；open005（guided）87,647 token / 278.400 s；open004（guided）64,776 token / 372.700 s

### D10 可靠性（reliability.json + fault injection）

- consistency_at_3 0.800；hard_timeout_rate 0.000
- 三条失败：final005 不一致；final010 不一致；final014 不一致

### D11 安全（safety.json）

- credential_leak_count 0；url_secret_leak_count 0；pii_in_query_redaction 1.000；denylist_compliance 1.000
- 三条失败：无（或未运行）

## 6. 逐题附表

单元格：交付；core_correct /2；幻觉引用；citation_recall；loop 终态；token；秒。

| 题号 | 数据集 | 模式 | 交付 | core | 幻觉引用 | citation_recall | 终态 | token | 秒 |
|---|---|---|---|---|---:|---:|---|---:|---:|
| abs001 | abstention | guided | 是 | 未运行 | 0 | 未运行 | succeeded | 3,663 | 55.300 |
| abs002 | abstention | guided | 是 | 未运行 | 0 | 1.000 | evidence_insufficient | 17,648 | 108.500 |
| abs003 | abstention | guided | 是 | 未运行 | 0 | 1.000 | evidence_insufficient | 15,817 | 52.900 |
| abs004 | abstention | guided | 是 | 未运行 | 0 | 未运行 | succeeded | 9,446 | 92.400 |
| abs005 | abstention | guided | 是 | 未运行 | 0 | 1.000 | evidence_insufficient | 20,234 | 89.100 |
| abs006 | abstention | guided | 是 | 未运行 | 0 | 未运行 | evidence_insufficient | 25,643 | 58.000 |
| abs007 | abstention | guided | 是 | 未运行 | 0 | 未运行 | evidence_insufficient | 25,560 | 100.200 |
| abs008 | abstention | guided | 是 | 未运行 | 1 | 未运行 | evidence_insufficient | 12,470 | 65.000 |
| abs009 | abstention | guided | 是 | 未运行 | 0 | 未运行 | succeeded | 10,531 | 86.600 |
| abs010 | abstention | guided | 是 | 未运行 | 0 | 0.000 | stagnated | 6,445 | 31.800 |
| abs011 | abstention | guided | 是 | 未运行 | 0 | 0.000 | stagnated | 7,687 | 52.400 |
| abs012 | abstention | guided | 是 | 未运行 | 0 | 未运行 | succeeded | 8,995 | 45.200 |
| abs013 | abstention | guided | 是 | 未运行 | 0 | 未运行 | succeeded | 2,434 | 10.100 |
| abs014 | abstention | guided | 是 | 未运行 | 0 | 0.500 | evidence_insufficient | 21,436 | 102.900 |
| abs015 | abstention | guided | 是 | 未运行 | 0 | 0.500 | evidence_insufficient | 33,367 | 66.300 |
| abs016 | abstention | guided | 是 | 未运行 | 0 | 未运行 | succeeded | 11,657 | 83.100 |
| final001 | final_answer | guided | 是 | 2 | 0 | 0.000 | stagnated | 8,402 | 123.300 |
| final002 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 15,257 | 94.500 |
| final003 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 1,803 | 5.300 |
| final004 | final_answer | guided | 是 | 2 | 0 | 0.000 | stagnated | 4,823 | 28.200 |
| final005 | final_answer | guided | 是 | 2 | 0 | 0.000 | stagnated | 7,754 | 107.200 |
| final006 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 2,203 | 82.400 |
| final007 | final_answer | guided | 是 | 2 | 1 | 未运行 | stagnated | 4,314 | 9.400 |
| final008 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 1,533 | 2.800 |
| final009 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 4,730 | 13.200 |
| final010 | final_answer | guided | 是 | 2 | 0 | 0.000 | evidence_insufficient | 17,778 | 195.800 |
| final011 | final_answer | guided | 是 | 2 | 0 | 0.833 | evidence_insufficient | 23,433 | 191.200 |
| final012 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 5,207 | 45.100 |
| final013 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 4,924 | 56.800 |
| final014 | final_answer | guided | 否 | 0 | 0 | 0.000 | deadline_cancelled | 2,030 | 1044.800 |
| final015 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 4,902 | 102.300 |
| final016 | final_answer | guided | 是 | 2 | 2 | 未运行 | stagnated | 8,218 | 260.800 |
| final017 | final_answer | guided | 是 | 2 | 0 | 0.000 | stagnated | 10,013 | 187.700 |
| final018 | final_answer | guided | 否 | 0 | 0 | 0.000 | deadline_cancelled | 4,107 | 1107.200 |
| final019 | final_answer | guided | 是 | 2 | 0 | 0.000 | stagnated | 9,358 | 246.700 |
| final020 | final_answer | guided | 是 | 0 | 0 | 未运行 | evidence_insufficient | 27,233 | 103.700 |
| final021 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 3,833 | 43.700 |
| final022 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 24,604 | 159.600 |
| final023 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 1,675 | 7.400 |
| final024 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 3,959 | 36.500 |
| final025 | final_answer | guided | 是 | 0 | 0 | 未运行 | stagnated | 5,852 | 101.400 |
| final026 | final_answer | guided | 是 | 2 | 1 | 1.000 | stagnated | 7,701 | 74.400 |
| final027 | final_answer | guided | 是 | 2 | 1 | 1.000 | stagnated | 7,298 | 124.000 |
| final028 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 28,077 | 204.900 |
| final029 | final_answer | guided | 是 | 未运行 | 0 | 1.000 | evidence_insufficient | 36,428 | 219.100 |
| final030 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 29,979 | 117.700 |
| final031 | final_answer | guided | 是 | 2 | 0 | 1.000 | succeeded | 18,591 | 465.600 |
| final032 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 8,654 | 39.600 |
| final033 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 14,358 | 133.600 |
| final034 | final_answer | guided | 是 | 2 | 0 | 0.500 | evidence_insufficient | 31,500 | 159.000 |
| final035 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 19,982 | 179.600 |
| final036 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 7,470 | 26.500 |
| final037 | final_answer | guided | 是 | 2 | 0 | 1.000 | succeeded | 13,883 | 58.600 |
| final038 | final_answer | guided | 是 | 2 | 0 | 1.000 | succeeded | 18,495 | 313.200 |
| final039 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 20,593 | 220.000 |
| final040 | final_answer | guided | 是 | 0 | 0 | 未运行 | evidence_insufficient | 27,371 | 193.100 |
| final041 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 21,799 | 112.400 |
| final042 | final_answer | guided | 是 | 0 | 0 | 1.000 | evidence_insufficient | 38,980 | 298.700 |
| final043 | final_answer | guided | 是 | 0 | 0 | 1.000 | succeeded | 27,852 | 283.900 |
| final044 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 10,436 | 695.000 |
| final045 | final_answer | guided | 是 | 2 | 0 | 0.500 | evidence_insufficient | 16,997 | 90.400 |
| final046 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 23,398 | 328.300 |
| final047 | final_answer | guided | 是 | 2 | 0 | 0.889 | evidence_insufficient | 42,352 | 582.000 |
| final048 | final_answer | guided | 是 | 2 | 0 | 0.000 | stagnated | 5,118 | 106.800 |
| final049 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 14,997 | 84.100 |
| final050 | final_answer | guided | 是 | 2 | 0 | 0.857 | evidence_insufficient | 15,909 | 136.500 |
| final051 | final_answer | guided | 是 | 未运行 | 0 | 0.714 | evidence_insufficient | 23,602 | 377.000 |
| final052 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 54,372 | 230.000 |
| final053 | final_answer | guided | 是 | 2 | 0 | 0.667 | evidence_insufficient | 21,466 | 152.600 |
| final054 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 28,563 | 180.800 |
| final055 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 24,144 | 108.800 |
| final056 | final_answer | guided | 是 | 2 | 0 | 0.857 | evidence_insufficient | 23,608 | 222.100 |
| final057 | final_answer | guided | 是 | 0 | 0 | 未运行 | evidence_insufficient | 35,229 | 128.600 |
| final058 | final_answer | guided | 是 | 0 | 0 | 1.000 | evidence_insufficient | 22,648 | 116.200 |
| final059 | final_answer | guided | 是 | 0 | 0 | 1.000 | succeeded | 19,797 | 75.200 |
| final060 | final_answer | guided | 是 | 未运行 | 0 | 1.000 | succeeded | 14,538 | 127.400 |
| final061 | final_answer | guided | 是 | 2 | 0 | 1.000 | succeeded | 16,710 | 102.200 |
| final062 | final_answer | guided | 是 | 0 | 0 | 未运行 | evidence_insufficient | 36,795 | 118.100 |
| open001 | open_task | guided | 是 | 2 | 0 | 未运行 | succeeded | 41,677 | 194.600 |
| open002 | open_task | guided | 是 | 0 | 0 | 未运行 | succeeded | 24,027 | 164.300 |
| open003 | open_task | guided | 是 | 2 | 0 | 未运行 | succeeded | 22,203 | 1263.400 |
| open004 | open_task | guided | 是 | 0 | 0 | 未运行 | evidence_insufficient | 64,776 | 372.700 |
| open005 | open_task | guided | 是 | 2 | 0 | 0.800 | evidence_insufficient | 87,647 | 278.400 |
| open006 | open_task | guided | 是 | 2 | 0 | 1.000 | cancelled | 47,369 | 731.200 |
| open007 | open_task | guided | 是 | 2 | 0 | 0.667 | evidence_insufficient | 39,712 | 738.800 |
| open008 | open_task | guided | 是 | 2 | 0 | 0.000 | stagnated | 13,875 | 210.800 |
| open009 | open_task | guided | 是 | 0 | 0 | 未运行 | evidence_insufficient | 48,295 | 406.000 |
| open010 | open_task | guided | 是 | 2 | 0 | 0.929 | evidence_insufficient | 62,258 | 435.900 |
| open011 | open_task | guided | 是 | 2 | 0 | 0.000 | stagnated | 9,860 | 50.000 |
| open012 | open_task | guided | 是 | 2 | 0 | 未运行 | succeeded | 15,113 | 71.200 |
| open013 | open_task | guided | 是 | 2 | 0 | 0.667 | cancelled | 30,871 | 863.300 |
| open014 | open_task | guided | 是 | 0 | 0 | 未运行 | stagnated | 9,066 | 44.800 |
| open015 | open_task | guided | 是 | 2 | 0 | 0.471 | evidence_insufficient | 103,472 | 576.200 |
| open016 | open_task | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 54,167 | 295.100 |
| open017 | open_task | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 39,870 | 685.000 |
| open018 | open_task | guided | 是 | 2 | 0 | 未运行 | evidence_insufficient | 23,017 | 172.900 |
| open019 | open_task | guided | 是 | 0 | 0 | 未运行 | returned | 0 | 0.000 |
| open020 | open_task | guided | 是 | 2 | 0 | 0.667 | evidence_insufficient | 35,197 | 269.300 |

## 7. 复现命令

```bash
# 离线重算（不发起任何网络请求）
python -m tests.quality.citation_eval --source runtime/quality/20260918-quality-20260918 --output-file runtime/quality/20260918-quality-20260918/citation_eval.json
python -m tests.quality.evidence_eval --source runtime/quality/20260918-quality-20260918 --output-file runtime/quality/20260918-quality-20260918/evidence_eval.json
python -m tests.quality.loop_eval --source runtime/quality/20260918-quality-20260918 --output-file runtime/quality/20260918-quality-20260918/loop_eval.json
python -m tests.quality.cost_eval --source runtime/quality/20260918-quality-20260918 --output-file runtime/quality/20260918-quality-20260918/cost.json
python -m tests.quality.fetch_eval --source runtime/quality/20260918-quality-20260918 --output-file runtime/quality/20260918-quality-20260918/fetch_eval.json
python -m tests.quality.analysis_eval --output-file runtime/quality/20260918-quality-20260918/analysis_eval.json
python -m tests.quality.tiering_eval --output-file runtime/quality/20260918-quality-20260918/evidence_eval_offline.json
python -m tests.quality.safety_eval --run runtime/quality/20260918-quality-20260918 --output-file runtime/quality/20260918-quality-20260918/safety.json
python -m tests.quality_report --run runtime/quality/20260918-quality-20260918
# 采集类步骤（真实运行）见 docs/quality_evaluation_plan.md 的 [真实运行] 任务
```

## 8. 局限

- 裁判分是模型辅助分，未经人工校准的维度只能作参考；kappa < 0.6 的维度应降级为仅人工。
- 引用核验是机械核验：只确认 `[En]` 可解析且来源等级达标，不证明来源确实支持该句。
- 搜索结果随时间漂移；跨期比较需先看 `--all-providers` 的跨供应商一致性。
- 未运行 的维度不参与指数；指数只是可用组成的均值，不做加权。
