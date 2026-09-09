# ISE 质量评测报告（20260909-offline）

生成时间 2026-09-08T21:10:22.260743+00:00。产物目录 `runtime/quality/20260909-offline`；答案记录来源 `runtime/quality/20260909-offline`。本报告由 `python -m tests.quality_report` 从运行产物生成，缺失的维度标为 **未运行**，不会写成 0。

## 1. 范围与证据等级

- 题数 0；有裁判/人工判定的题数 0。
- 冻结信息：commit `b25356d17eb4e4f143777ab12d0153ed2b6d0a8e`，工作树有未提交改动；配置摘要 `6cb563302f3ee29efa0440f65167f49c577da4f14fecd4b7d47796adf37a5c0d`；模型 `deepseek-v4-flash`；裁判 `deepseek-v4-flash`；rubric `v4`。
- 证据等级：真实运行产物（answer_details / result.json）→ 确定性离线重算（citation / evidence / loop / cost / fetch）→ 模型辅助评审（reviews）→ 人工标注（dataset/annotations）。裁判分不是人工分；核心正确性单列且拥有一票否决。

## 2. D0 度量有效性

- validity.json 未运行；本报告的数字未经 D0 冒烟确认，按设计 §4 D0 规则应视为**数据可信度未验证**。

## 3. 硬门槛（任一非零即未通过）

| 门槛 | 计数 | 题号 |
|---|---:|---|
| 核心事实错误（core_correct=0） | 未运行 | — |
| 幻觉引用（citation_unresolved） | 未运行 | — |
| 未交付（delivered=false） | 未运行 | — |
| 假性耗尽 | 未运行 | — |
| 凭据泄漏 | 未运行 | — |
| 触顶预算调用 | 未运行 | — |

硬门槛结论：未运行；未运行的门槛：core_correct_zero_count, hallucinated_citation_count, undelivered_count, false_exhaustion_count, credential_leak_count。

## 4. 六个指数

| 指数 | 值 | 可用组成/总组成 | 组成 |
|---|---:|---:|---|
| 检索指数 | 未运行 | 0/6 | hit_at_3=未运行；gold_doc_recall_at_5=未运行；ndcg_at_5=未运行；gold_span_containment=未运行；chunk_hit_at_3=未运行；mrr_chunk=未运行 |
| 证据指数 | 0.928 | 2/6 | tier_accuracy=0.856；official_precision=1.000；member_coverage_f1=未运行；citation_recall=未运行；citation_precision=未运行；authority_compliance=未运行 |
| 答案指数 | 未运行 | 0/5 | core_correct=未运行；request_completeness=未运行；grounding=未运行；open_task_aux=未运行；abstention_quality=未运行 |
| 过程指数 | 0.712 | 1/5 | members_f1=0.712；route_accuracy=未运行；critic_block_precision=未运行；judge_agreement=未运行；not_premature=未运行 |
| 可靠指数 | 1.000 | 1/3 | consistency_at_3=未运行；not_hard_timeout=未运行；fault_injection_pass_rate=1.000 |
| 成本指数（只报比值） | 无基线 | — | tokens 均值 未运行；P95 时延 未运行 ms；每正确答案 token 未运行 |

样本 < 20，不报置信区间。

## 5. 各维度要点与三条失败

### D1 查询理解（analysis_eval.json）

- intent_shape 准确率 0.846；对比类成员 F1 0.712；noise_member_rate 0.133；false_temporal_fanout_rate 0.000
- 三条失败：noise: the three largest cloud providers on egress（qa015）；noise: Zig for systems programming（qa019）；noise: 前者, 后者（qa022）

### D3 网页搜索（search_report.json）

- 未运行
- 三条失败：无（或未运行）

### D4 抓取与抽取（fetch_eval.json）

- 未运行
- 三条失败：无（或未运行）

### D6 证据与权威（evidence_eval*.json）

- tier_accuracy(collapsed) 0.856；official_precision 1.000；resolver_accuracy 0.282；aggregator_leak_rate 未运行
- 三条失败：lookalike 被判权威：https://openai.zip/；lookalike 被判权威：https://deepseek.top/；lookalike 被判权威：https://kimi.vip/

### D7 答案质量（reviews + citation_eval.json）

- 未运行
- 三条失败：无（或未运行）

### D8 循环与终止（loop_eval.json）

- 未运行
- 三条失败：无（或未运行）

### D9 成本（cost.json）

- 未运行
- 三条失败：无（或未运行）

### D10 可靠性（reliability.json + fault injection）

- 未运行（故障注入见 offline_results.json）
- 三条失败：无（或未运行）

### D11 安全（safety.json）

- 未运行
- 三条失败：无（或未运行）

## 6. 逐题附表

单元格：交付；core_correct /2；幻觉引用；citation_recall；loop 终态；token；秒。

| 题号 | 数据集 | 模式 | 交付 | core | 幻觉引用 | citation_recall | 终态 | token | 秒 |
|---|---|---|---|---|---:|---:|---|---:|---:|

## 7. 复现命令

```bash
# 离线重算（不发起任何网络请求）
python -m tests.quality.citation_eval --source runtime/quality/20260909-offline --output-file runtime/quality/20260909-offline/citation_eval.json
python -m tests.quality.evidence_eval --source runtime/quality/20260909-offline --output-file runtime/quality/20260909-offline/evidence_eval.json
python -m tests.quality.loop_eval --source runtime/quality/20260909-offline --output-file runtime/quality/20260909-offline/loop_eval.json
python -m tests.quality.cost_eval --source runtime/quality/20260909-offline --output-file runtime/quality/20260909-offline/cost.json
python -m tests.quality.fetch_eval --source runtime/quality/20260909-offline --output-file runtime/quality/20260909-offline/fetch_eval.json
python -m tests.quality.analysis_eval --output-file runtime/quality/20260909-offline/analysis_eval.json
python -m tests.quality.tiering_eval --output-file runtime/quality/20260909-offline/evidence_eval_offline.json
python -m tests.quality.safety_eval --run runtime/quality/20260909-offline --output-file runtime/quality/20260909-offline/safety.json
python -m tests.quality_report --run runtime/quality/20260909-offline
# 采集类步骤（真实运行）见 docs/quality_evaluation_plan.md 的 [真实运行] 任务
```

## 8. 局限

- 裁判分是模型辅助分，未经人工校准的维度只能作参考；kappa < 0.6 的维度应降级为仅人工。
- 引用核验是机械核验：只确认 `[En]` 可解析且来源等级达标，不证明来源确实支持该句。
- 搜索结果随时间漂移；跨期比较需先看 `--all-providers` 的跨供应商一致性。
- 未运行 的维度不参与指数；指数只是可用组成的均值，不做加权。
