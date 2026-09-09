# ISE 质量评测报告（20260909-historical-r2）

生成时间 2026-09-08T20:56:15.195920+00:00。产物目录 `runtime/quality/20260909-historical-r2`；答案记录来源 `runtime/baseline/autonomy-20260908-measured/r2`。本报告由 `python -m tests.quality_report` 从运行产物生成，缺失的维度标为 **未运行**，不会写成 0。

## 1. 范围与证据等级

- 题数 80；有裁判/人工判定的题数 40。
- 冻结信息：commit `未知`，工作树干净；配置摘要 `未知`；模型 `未知`；裁判 `未知`；rubric `见 reviews 目录`。
- 证据等级：真实运行产物（answer_details / result.json）→ 确定性离线重算（citation / evidence / loop / cost / fetch）→ 模型辅助评审（reviews）→ 人工标注（dataset/annotations）。裁判分不是人工分；核心正确性单列且拥有一票否决。

## 2. D0 度量有效性

- validity.json 未运行；本报告的数字未经 D0 冒烟确认，按设计 §4 D0 规则应视为**数据可信度未验证**。

## 3. 硬门槛（任一非零即未通过）

| 门槛 | 计数 | 题号 |
|---|---:|---|
| 核心事实错误（core_correct=0） | 1 | final016 |
| 幻觉引用（citation_unresolved） | 7 | final007, final012, open002 |
| 未交付（delivered=false） | 4 | open004, open008, open013, open018 |
| 假性耗尽 | 0 | — |
| 凭据泄漏 | 0 | — |
| 触顶预算调用 | 0 | — |

硬门槛结论：未通过；未运行的门槛：无。

## 4. 六个指数

| 指数 | 值 | 可用组成/总组成 | 组成 |
|---|---:|---:|---|
| 检索指数 | 1.000 | 1/6 | hit_at_3=未运行；gold_doc_recall_at_5=未运行；ndcg_at_5=未运行；gold_span_containment=1.000；chunk_hit_at_3=未运行；mrr_chunk=未运行 |
| 证据指数 | 0.639 | 4/6 | tier_accuracy=0.856；official_precision=1.000；member_coverage_f1=未运行；citation_recall=0.486；citation_precision=未运行；authority_compliance=0.215 |
| 答案指数 | 0.798 | 4/5 | core_correct=0.975；request_completeness=0.988；grounding=0.463；open_task_aux=0.766；abstention_quality=未运行 |
| 过程指数 | 0.847 | 2/5 | members_f1=0.712；route_accuracy=未运行；critic_block_precision=未运行；judge_agreement=未运行；not_premature=0.981 |
| 可靠指数 | 未运行 | 0/3 | consistency_at_3=未运行；not_hard_timeout=未运行；fault_injection_pass_rate=未运行 |
| 成本指数（只报比值） | 无基线 | — | tokens 均值 18963；P95 时延 438516 ms；每正确答案 token 7333 |

core_correct/2 的 bootstrap 95% 区间：[0.925, 1.000]（n=40）。

## 5. 各维度要点与三条失败

### D1 查询理解（analysis_eval.json）

- intent_shape 准确率 0.846；对比类成员 F1 0.712；noise_member_rate 0.133；false_temporal_fanout_rate 0.000
- 三条失败：noise: the three largest cloud providers on egress（qa015）；noise: Zig for systems programming（qa019）；noise: 前者, 后者（qa022）

### D3 网页搜索（search_report.json）

- 未运行
- 三条失败：无（或未运行）

### D4 抓取与抽取（fetch_eval.json）

- fetch_success_rate 1.000；attempts/success 1.250；gold_span_containment(when fetched) 1.000；truncation_rate 0.607
- 三条失败：无（或未运行）

### D6 证据与权威（evidence_eval*.json）

- tier_accuracy(collapsed) 0.856；official_precision 1.000；resolver_accuracy 0.282；aggregator_leak_rate 0.063
- 三条失败：lookalike 被判权威：https://openai.zip/；lookalike 被判权威：https://deepseek.top/；lookalike 被判权威：https://kimi.vip/

### D7 答案质量（reviews + citation_eval.json）

- core_correct=2 比例 未运行；citation_recall 0.486；authority_compliance 0.215；hallucinated_citation_count 7
- 三条失败：final016（autonomous）core_correct=0

### D8 循环与终止（loop_eval.json）

- loop_status 分布 {"succeeded": 52, "evidence_insufficient": 20, "stagnated": 3}；false_exhaustion_rate 0.000；premature_success_rate 0.019；judge_error_rate 0.138；advisory_gap_ignore_rate 0.923
- 三条失败：final016 提前成功

### D9 成本（cost.json）

- tokens 均值 18963；时延 P50/P95 34291/438516 ms；预算耗尽题 token 占比 0.815；每正确答案 token 7333
- 三条失败：open010（guided）265,605 token / 574.400 s；open015（guided）156,693 token / 478.700 s；open009（guided）144,750 token / 578.000 s

### D10 可靠性（reliability.json + fault injection）

- 未运行（故障注入见 offline_results.json）
- 三条失败：无（或未运行）

### D11 安全（safety.json）

- credential_leak_count 0；url_secret_leak_count 0；pii_in_query_redaction 未运行；denylist_compliance 1.000
- 三条失败：无（或未运行）

## 6. 逐题附表

单元格：交付；core_correct /2；幻觉引用；citation_recall；loop 终态；token；秒。

| 题号 | 数据集 | 模式 | 交付 | core | 幻觉引用 | citation_recall | 终态 | token | 秒 |
|---|---|---|---|---|---:|---:|---|---:|---:|
| final001 | final_answer | autonomous | 是 | 2 | 0 | 0.667 | succeeded | 2,601 | 9.200 |
| final001 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 15,739 | 77.100 |
| final002 | final_answer | autonomous | 是 | 2 | 0 | 0.000 | succeeded | 1,737 | 10.600 |
| final002 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 21,905 | 238.000 |
| final003 | final_answer | autonomous | 是 | 2 | 0 | 未运行 | succeeded | 1,312 | 3.600 |
| final003 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 2,195 | 29.800 |
| final004 | final_answer | autonomous | 是 | 2 | 0 | 0.000 | succeeded | 1,428 | 3.000 |
| final004 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 11,877 | 106.500 |
| final005 | final_answer | autonomous | 是 | 2 | 0 | 0.000 | succeeded | 1,574 | 4.100 |
| final005 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 16,474 | 164.300 |
| final006 | final_answer | autonomous | 是 | 2 | 0 | 0.000 | succeeded | 1,455 | 2.300 |
| final006 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 1,809 | 4.100 |
| final007 | final_answer | autonomous | 是 | 2 | 0 | 0.000 | succeeded | 1,448 | 2.800 |
| final007 | final_answer | guided | 是 | 2 | 1 | 1.000 | stagnated | 8,722 | 190.600 |
| final008 | final_answer | autonomous | 是 | 2 | 0 | 未运行 | succeeded | 1,372 | 3.300 |
| final008 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 1,857 | 6.900 |
| final009 | final_answer | autonomous | 是 | 2 | 0 | 0.000 | succeeded | 1,563 | 5.900 |
| final009 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 2,822 | 18.400 |
| final010 | final_answer | autonomous | 是 | 2 | 0 | 0.000 | succeeded | 1,794 | 6.900 |
| final010 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 14,394 | 106.700 |
| final011 | final_answer | autonomous | 是 | 2 | 0 | 1.000 | succeeded | 2,516 | 13.800 |
| final011 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 16,022 | 76.200 |
| final012 | final_answer | autonomous | 是 | 2 | 0 | 未运行 | succeeded | 1,792 | 4.700 |
| final012 | final_answer | guided | 是 | 2 | 1 | 未运行 | stagnated | 8,754 | 72.000 |
| final013 | final_answer | autonomous | 是 | 2 | 0 | 0.000 | succeeded | 1,424 | 6.400 |
| final013 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 2,449 | 16.300 |
| final014 | final_answer | autonomous | 是 | 2 | 0 | 0.000 | succeeded | 1,530 | 3.500 |
| final014 | final_answer | guided | 是 | 2 | 0 | 1.000 | succeeded | 9,375 | 76.300 |
| final015 | final_answer | autonomous | 是 | 2 | 0 | 0.000 | succeeded | 1,452 | 4.400 |
| final015 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 3,151 | 51.000 |
| final016 | final_answer | autonomous | 是 | 0 | 0 | 1.000 | succeeded | 1,913 | 6.000 |
| final016 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 16,933 | 116.100 |
| final017 | final_answer | autonomous | 是 | 2 | 0 | 0.000 | succeeded | 2,067 | 14.000 |
| final017 | final_answer | guided | 是 | 2 | 0 | 1.000 | evidence_insufficient | 15,287 | 138.600 |
| final018 | final_answer | autonomous | 是 | 2 | 0 | 0.000 | succeeded | 1,619 | 3.600 |
| final018 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 11,323 | 115.600 |
| final019 | final_answer | autonomous | 是 | 2 | 0 | 0.667 | succeeded | 4,258 | 25.800 |
| final019 | final_answer | guided | 是 | 2 | 0 | 未运行 | succeeded | 4,909 | 54.100 |
| final020 | final_answer | autonomous | 是 | 2 | 0 | 1.000 | succeeded | 2,085 | 2.300 |
| final020 | final_answer | guided | 是 | 2 | 0 | 未运行 | evidence_insufficient | 63,050 | 425.100 |
| open001 | open_task | autonomous | 是 | 未运行 | 0 | 0.818 | succeeded | 5,784 | 44.900 |
| open001 | open_task | guided | 是 | 未运行 | 0 | 0.750 | evidence_insufficient | 29,264 | 188.700 |
| open002 | open_task | autonomous | 是 | 未运行 | 5 | 未运行 | succeeded | 8,030 | 43.200 |
| open002 | open_task | guided | 是 | 未运行 | 0 | 未运行 | evidence_insufficient | 38,574 | 140.500 |
| open003 | open_task | autonomous | 是 | 未运行 | 0 | 未运行 | succeeded | 6,300 | 32.300 |
| open003 | open_task | guided | 是 | 未运行 | 0 | 1.000 | succeeded | 30,320 | 91.900 |
| open004 | open_task | autonomous | 否 | 未运行 | 0 | 未运行 | harness_timeout | 0 | 0.000 |
| open004 | open_task | guided | 是 | 未运行 | 0 | 0.694 | evidence_insufficient | 114,742 | 282.100 |
| open005 | open_task | autonomous | 是 | 未运行 | 0 | 0.000 | succeeded | 4,573 | 24.000 |
| open005 | open_task | guided | 是 | 未运行 | 0 | 0.250 | evidence_insufficient | 74,900 | 537.700 |
| open006 | open_task | autonomous | 是 | 未运行 | 0 | 0.000 | succeeded | 5,155 | 34.700 |
| open006 | open_task | guided | 是 | 未运行 | 0 | 未运行 | succeeded | 11,089 | 32.400 |
| open007 | open_task | autonomous | 是 | 未运行 | 0 | 0.375 | succeeded | 3,016 | 14.200 |
| open007 | open_task | guided | 是 | 未运行 | 0 | 0.857 | evidence_insufficient | 43,740 | 162.100 |
| open008 | open_task | autonomous | 是 | 未运行 | 0 | 0.000 | succeeded | 3,609 | 6.100 |
| open008 | open_task | guided | 否 | 未运行 | 0 | 未运行 | harness_timeout | 0 | 0.000 |
| open009 | open_task | autonomous | 是 | 未运行 | 0 | 0.000 | succeeded | 4,545 | 20.700 |
| open009 | open_task | guided | 是 | 未运行 | 0 | 未运行 | evidence_insufficient | 144,750 | 578.000 |
| open010 | open_task | autonomous | 是 | 未运行 | 0 | 0.400 | succeeded | 12,456 | 51.500 |
| open010 | open_task | guided | 是 | 未运行 | 0 | 0.875 | evidence_insufficient | 265,605 | 574.400 |
| open011 | open_task | autonomous | 是 | 未运行 | 0 | 0.000 | succeeded | 4,051 | 15.000 |
| open011 | open_task | guided | 是 | 未运行 | 0 | 未运行 | succeeded | 8,226 | 39.000 |
| open012 | open_task | autonomous | 是 | 未运行 | 0 | 未运行 | succeeded | 3,786 | 11.500 |
| open012 | open_task | guided | 是 | 未运行 | 0 | 未运行 | succeeded | 11,785 | 83.500 |
| open013 | open_task | autonomous | 是 | 未运行 | 0 | 0.167 | succeeded | 15,728 | 71.900 |
| open013 | open_task | guided | 否 | 未运行 | 0 | 未运行 | harness_timeout | 0 | 0.000 |
| open014 | open_task | autonomous | 是 | 未运行 | 0 | 0.000 | succeeded | 4,244 | 35.100 |
| open014 | open_task | guided | 是 | 未运行 | 0 | 0.000 | stagnated | 16,929 | 47.500 |
| open015 | open_task | autonomous | 是 | 未运行 | 0 | 0.542 | succeeded | 12,018 | 37.000 |
| open015 | open_task | guided | 是 | 未运行 | 0 | 0.556 | evidence_insufficient | 156,693 | 478.700 |
| open016 | open_task | autonomous | 是 | 未运行 | 0 | 未运行 | succeeded | 7,374 | 21.000 |
| open016 | open_task | guided | 是 | 未运行 | 0 | 1.000 | evidence_insufficient | 43,309 | 142.000 |
| open017 | open_task | autonomous | 是 | 未运行 | 0 | 未运行 | succeeded | 5,360 | 13.800 |
| open017 | open_task | guided | 是 | 未运行 | 0 | 0.667 | evidence_insufficient | 32,808 | 126.800 |
| open018 | open_task | autonomous | 是 | 未运行 | 0 | 0.500 | succeeded | 10,343 | 28.700 |
| open018 | open_task | guided | 否 | 未运行 | 0 | 未运行 | harness_timeout | 0 | 0.000 |
| open019 | open_task | autonomous | 是 | 未运行 | 0 | 0.000 | succeeded | 5,323 | 33.900 |
| open019 | open_task | guided | 是 | 未运行 | 0 | 未运行 | returned | 0 | 0.000 |
| open020 | open_task | autonomous | 是 | 未运行 | 0 | 1.000 | succeeded | 21,276 | 35.800 |
| open020 | open_task | guided | 是 | 未运行 | 0 | 0.500 | evidence_insufficient | 65,331 | 362.300 |

## 7. 复现命令

```bash
# 离线重算（不发起任何网络请求）
python -m tests.quality.citation_eval --source runtime/baseline/autonomy-20260908-measured/r2 --output-file runtime/quality/20260909-historical-r2/citation_eval.json
python -m tests.quality.evidence_eval --source runtime/baseline/autonomy-20260908-measured/r2 --output-file runtime/quality/20260909-historical-r2/evidence_eval.json
python -m tests.quality.loop_eval --source runtime/baseline/autonomy-20260908-measured/r2 --output-file runtime/quality/20260909-historical-r2/loop_eval.json
python -m tests.quality.cost_eval --source runtime/baseline/autonomy-20260908-measured/r2 --output-file runtime/quality/20260909-historical-r2/cost.json
python -m tests.quality.fetch_eval --source runtime/baseline/autonomy-20260908-measured/r2 --output-file runtime/quality/20260909-historical-r2/fetch_eval.json
python -m tests.quality.analysis_eval --output-file runtime/quality/20260909-historical-r2/analysis_eval.json
python -m tests.quality.tiering_eval --output-file runtime/quality/20260909-historical-r2/evidence_eval_offline.json
python -m tests.quality.safety_eval --run runtime/quality/20260909-historical-r2 --output-file runtime/quality/20260909-historical-r2/safety.json
python -m tests.quality_report --run runtime/quality/20260909-historical-r2 --answers runtime/baseline/autonomy-20260908-measured/r2
# 采集类步骤（真实运行）见 docs/quality_evaluation_plan.md 的 [真实运行] 任务
```

## 8. 局限

- 裁判分是模型辅助分，未经人工校准的维度只能作参考；kappa < 0.6 的维度应降级为仅人工。
- 引用核验是机械核验：只确认 `[En]` 可解析且来源等级达标，不证明来源确实支持该句。
- 搜索结果随时间漂移；跨期比较需先看 `--all-providers` 的跨供应商一致性。
- 未运行 的维度不参与指数；指数只是可用组成的均值，不做加权。
