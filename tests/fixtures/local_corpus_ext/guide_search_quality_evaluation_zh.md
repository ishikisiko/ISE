# Search Quality Evaluation

检索质量评测的两个步骤，以及配套的回归脚本。

仓库内含：

- `tests/search_quality_minimal_dataset.csv`：20 条分组的起步数据集
- `tests/search_quality_minimal_queries.txt`：按数据集顺序的完整查询列表
- `tests/search_quality_minimal_search_queries.txt`：用于 `collect` 的搜索导向子集
- `tests/search_quality_local_chunk_template.csv`：本地 RAG 分块标注模板

可直接检索单个类别或单条样例：

```bash
env1/bin/python tests/search_quality_pipeline.py dataset --list-categories
env1/bin/python tests/search_quality_pipeline.py dataset --category local_rag
env1/bin/python tests/search_quality_pipeline.py dataset --query-id Q018
env1/bin/python tests/search_quality_pipeline.py dataset --category web_search_fulltext --queries-only
```

也可把 `dataset/` 下的分层 CSV 基准合并成一个统一文件：

```bash
env1/bin/python tests/search_quality_pipeline.py map-external \
  --dataset-dir dataset \
  --output-file tests/search_quality_external_merged.csv \
  --queries-output-file tests/search_quality_external_search_queries.txt
```

## 1. 收集搜索结果

为一批查询收集 top 搜索结果：

```bash
env1/bin/python tests/search_quality_pipeline.py collect \
  --queries-file tests/search_quality_minimal_search_queries.txt \
  --output-file tests/search_quality_annotations.json \
  --num-results 5 \
  --force-search
```

## 2. 标注

打开生成的 JSON，为每条查询填写 `judgment` 字段。

详细模式：

- 将 `annotation_complete` 置为 `true`
- 保持 `judgment_mode` 为 `"detailed"`
- 填写 `relevant_ranks` 和/或 `relevant_urls`

轻量模式：

- 将 `annotation_complete` 置为 `true`
- 将 `judgment_mode` 设为 `"top3_only"`
- 填写 `top3_has_answer_evidence`

## 3. 计算指标

```bash
env1/bin/python tests/search_quality_pipeline.py evaluate \
  --annotations-file tests/search_quality_annotations.json \
  --output-file tests/search_quality_report.json
```

报告包含：

- `route_correct`
- `fulltext_decision_correct`
- `Hit@3`
- `Hit@5`
- `chunk_hit_at_5`
- `MRR`
- `avg_unique_useful_results`
- `answer_correctness`
- `answer_completeness`
- `answer_groundedness`
- `abstention_quality`

## 快速回归

同一个 `search_quality_pipeline.py` 入口可跑混合路由回归与纯搜索判定。

跨 small talk / domain API / web search / 本地 RAG 的混合路由回归：

```bash
env1/bin/python tests/search_quality_pipeline.py collect \
  --queries-file tests/search_quality_minimal_queries.txt \
  --output-file tests/search_quality_regression_run.json \
  --num-results 5 \
  --show-timings
```

强制检索的搜索判定集：

```bash
env1/bin/python tests/search_quality_pipeline.py collect \
  --queries-file tests/search_quality_minimal_search_queries.txt \
  --output-file tests/search_quality_annotations.json \
  --num-results 5 \
  --force-search \
  --show-timings
```

更大规模的 web 导向回归，复用合并后的外部查询列表：

```bash
env1/bin/python tests/search_quality_pipeline.py collect \
  --queries-file tests/search_quality_external_search_queries.txt \
  --output-file tests/search_quality_external_run.json \
  --num-results 5 \
  --force-search
```

## 4. 2026-09-09 起的新字段与指标（质量评测计划 Q1）

`collect` 现在记录当前 loop 的 `control`（`query_analysis`、`execution_trace`、`evidence_coverage`、`loop_verdicts`、`loop_fetch_outcomes`、`tool_budgets`、`autonomy`、`loop_status` / `termination_reason`）、`search_api_calls`（含 provider、状态、时延、`fallback`、`credits`）、带 `metadata.eid` 的 `evidence_records`（截断到 2000 字）与答案；`selected_sources` 与恒空的 `keywords` 已删除。新参数：

```bash
# 用 CSV 数据集（qid/query/category）并预填 gold 命中
env1/bin/python tests/search_quality_pipeline.py collect \
  --dataset-file dataset/gold_doc_dataset.csv --gold-doc-file dataset/gold_doc_dataset.csv \
  --output-file runtime/quality/<run>/search_collect_gold_doc.json --num-results 5 --force-search --show-timings

# 全供应商采集：每个 provider 独立请求同一查询，不进 loop、不调用 LLM
env1/bin/python tests/search_quality_pipeline.py collect --all-providers \
  --dataset-file dataset/gold_doc_dataset.csv --output-file runtime/quality/<run>/search_collect_all_providers.json

# 评测：gold 直通、离线 tier 分类（只用 pins / 拒绝表，不联网）、把标注过的单链文件作为全供应商相关性来源
env1/bin/python tests/search_quality_pipeline.py evaluate --annotations-file dataset/annotations/search_<date>.json \
  --gold-doc-file dataset/gold_doc_dataset.csv --output-file runtime/quality/<run>/search_report.json
env1/bin/python tests/search_quality_pipeline.py evaluate --annotations-file runtime/quality/<run>/search_collect_all_providers.json \
  --relevance-annotations dataset/annotations/search_<date>.json --output-file runtime/quality/<run>/search_report_all_providers.json
```

标注新增字段：`relevance_grades`（逐 rank 0/1/2，启用 nDCG@5 与 `answer_hit_at_k`）、`gold_doc_urls`（脚本预填）、`core_correct`（0/1/2）、`annotator`、`annotated_at`。判据见 [quality_annotation_guide.md](quality_annotation_guide.md)。

`evaluate` 新增指标：`ndcg_at_5`、`answer_hit_at_3`、`gold_doc_recall_at_3/5`（host 去 `www.`、去 query/fragment、路径前缀匹配）、`authoritative_at_3/5`、`aggregator_at_3/5`、`domain_diversity_at_5`、`empty_result_rate`、`core_correct`、`by_category`（样本 < 5 的类别只报计数）与 `providers` 记分卡（`availability`、`error_rate_by_type`、`empty_rate`、`latency_p50/p95`、`fallback_share`、`retained_contribution`、`authoritative_yield`、`credits_known`、`cost_per_retained`；官方域名发现搜索单列在 `official_domain_discovery`）。全供应商报告给出 `relevant_contribution`、`unique_yield`、`gold_doc_hit_rate` 与跨供应商一致性（`pairwise_jaccard`、`top1_consensus_mean`）。旧的 `relevant_ranks` / `top3_only` 标注文件仍可评测，MRR / Hit@k 口径不变。
