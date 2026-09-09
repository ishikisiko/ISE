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
