# 本地 RAG embedding 模型对比与 rerank 评测（2026-09-19）

按 [质量评测实施计划](../../quality_evaluation_plan.md) Q1-09b 施工。目的：回答三个问题——换 embedding 模型能带来多少差异；便宜或本地的模型加 rerank 能否追上贵的模型；absent（语料无答案）题能否用检索分数提前识别。全部为真实运行，只有 embedding 与 rerank 请求，没有 LLM 调用。登记见 [baseline §7.10.1](../../baseline.md)。

## 1. 设置

| 项 | 取值 |
|---|---|
| 模型 | `qwen3.7-text-embedding`（当前产品配置）、`qwen3.7-text-embedding-flash`（同一 MaaS 端点，1024 维）、`sentence-transformers/all-MiniLM-L6-v2`（本地，英文）、`intfloat/multilingual-e5-small`（本地，双语，query/passage 前缀，归一化） |
| 语料 1 | `tests/fixtures/local_corpus/` 16 文件，gold 38 题（absent 6、跨语言 7） |
| 扩展语料 | `tests/fixtures/local_corpus_ext/` 40 文件 / 391 chunk（openspec specs、guides、devbench、design/plan/baseline 快照），gold `dataset/local_chunk_gold_ext.csv` 46 题（absent 12、跨语言 11、多 span 3）；与语料 1 合并索引后 84 题 / absent 18 |
| 网格 | 语料 1：chunk_size {300,500,800,1000,1500} × overlap {0,50,100,150,200,300}（29 组）；合并语料：{500,800,1000,1500} × {0,100,200}（12 组）；rerank 切片：{800,1000} × {0,200} |
| rerank | `qwen3-rerank`，先召回 10 再取 k；分数记为 1 − relevance |
| 指标 | `chunk_hit_at_3` 主排序，`mrr_chunk`、`context_recall`、`cross_lingual_hit`；新增 `absent_auroc`、`absent_reject_at_answerable_recall_95` |
| 产物 | `runtime/quality/20260919-embedding-corpus1/`、`20260919-embedding-ext/`、`20260919-rerank-corpus1/`、`20260919-rerank-ext/`（各含 `local_rag_eval.json` 与 `local.log`） |
| 运行方式 | `python -m tests.quality_runner --suite local --embedding-models ... [--rerank-model qwen3-rerank] [--data-path a,b --local-dataset-file a,b] [--local-chunk-sizes ... --local-chunk-overlaps ...]` |

可重复性：qwen 基础版在语料 1 上与 2026-09-18 的结果逐点一致（最优 800/0 hit@3 0.875，默认 1000/200 0.750，第 24/29 位）。

## 2. 不加 rerank：模型 × 参数

### 2.1 语料 1（38 题，完整网格）

| 模型 | 最优参数 | hit@3 | mrr_chunk@3 | 跨语言 hit@3 | absent AUROC | hit@3 @800/0 | hit@3 @1000/200 | hit@5 @1000/200 | 默认位次 | 索引 ms @1000/200 | 查询 ms @1000/200 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| qwen | 800/0 | **0.875** | 0.750 | 0.86 | 0.859 | 0.875 | 0.750 | 0.844 | 24/29 | 40,574 | 536 |
| qwen-flash | 800/0 | 0.812 | 0.703 | 0.86 | 0.792 | 0.812 | 0.750 | 0.875 | 12/29 | 23,952 | 339 |
| MiniLM-L6 | 1500/200 | 0.688 | 0.573 | 0.29 | 0.823 | 0.625 | 0.688 | 0.750 | 2/29 | 25,432 | 19 |
| e5-small | 800/150 | 0.719 | 0.661 | 0.14 | 0.807 | 0.656 | 0.656 | 0.750 | 14/29 | 49,044 | 23 |

按 chunk_size 取各 overlap 的 hit@3 均值：qwen 300→0.675、500→0.813、800→**0.854**、1000→0.797、1500→0.812；flash 同样 800 最好（0.766）；两个本地模型偏好更大的 chunk（MiniLM 1500→0.635，e5 800/1000→0.672）。300 对所有模型都最差。

### 2.2 合并语料（84 题，紧凑网格）

| 模型 | 最优参数 | hit@3 | mrr_chunk@3 | 跨语言 hit@3 | absent AUROC | 拒答率@可答召回 95% | hit@3 @800/0 | hit@3 @1000/200 | hit@5 @1000/200 | 默认位次 | 索引 ms @1000/200 | 查询 ms @1000/200 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| qwen | 1000/0 | 0.849 | 0.722 | 0.83 | 0.782 | 0.28 | 0.818 | 0.788 | 0.864 | 11/12 | 131,658 | 743 |
| qwen-flash | 1000/100 | **0.879** | 0.758 | **0.94** | 0.759 | 0.33 | 0.803 | 0.773 | 0.894 | 10/12 | 130,013 | 678 |
| MiniLM-L6 | 1000/200 | 0.515 | 0.439 | 0.39 | 0.721 | 0.33 | 0.439 | 0.515 | 0.591 | 1/12 | 48,456 | 13 |
| e5-small | 1500/200 | 0.667 | 0.556 | 0.28 | 0.671 | 0.28 | 0.576 | 0.621 | 0.682 | 5/12 | 221,182 | 43 |

扩展语料按设计引入了干扰：语料 1 的 lc004（chunk 参数设置）在合并语料上被 design / plan 文档的同类描述挤出前 3，四个模型都丢；lc012、lc029、lc030 也从"偶尔丢"变成"常丢"。新增的中文 spec 题（le002、le004、le008 等）MiniLM 几乎全丢，e5-small 丢一半。

## 3. 加 rerank

候选 10 → `qwen3-rerank` → 取 k。下表为产品默认 1000/200；800/0 结论相同。

### 3.1 语料 1

| 模型 | hit@3 无→有 | mrr_chunk 无→有 | absent AUROC 无→有 | 跨语言 无→有 | 查询 ms 无→有 |
|---|---|---|---|---|---|
| qwen | 0.750→0.875 | 0.672→0.807 | 0.880→0.953 | 0.857→1.000 | 536→3,102 |
| qwen-flash | 0.750→**0.906** | 0.635→0.818 | 0.802→0.958 | 0.714→1.000 | 339→2,699 |
| MiniLM-L6 | 0.688→0.750 | 0.573→0.719 | 0.812→0.854 | 0.143→0.429 | 19→2,051 |
| e5-small | 0.656→0.750 | 0.589→0.698 | 0.797→0.802 | 0.000→0.143 | 23→2,203 |

### 3.2 合并语料

| 模型 | hit@3 无→有 | mrr_chunk 无→有 | absent AUROC 无→有 | 拒答率@可答召回 95% 有 | 跨语言 无→有 | 查询 ms 无→有 |
|---|---|---|---|---:|---|---|
| qwen | 0.788→0.879 | 0.689→0.838 | 0.759→0.927 | 0.67 | 0.833→0.944 | 743→2,975 |
| qwen-flash | 0.773→**0.894** | 0.679→0.851 | 0.756→0.930 | 0.67 | 0.778→0.944 | 678→2,710 |
| MiniLM-L6 | 0.515→0.636 | 0.439→0.621 | 0.721→0.758 | 0.06 | 0.389→0.500 | 13→2,493 |
| e5-small | 0.621→0.773 | 0.528→0.730 | 0.673→0.851 | 0.06 | 0.167→0.389 | 43→2,554 |

rerank 后 800/0 与 1000/200 的差距消失：flash 在语料 1 两组都是 0.906，合并语料都是 0.894；qwen 语料 1 两组都是 0.875。1,952 次 rerank 调用超时 7 次（15 s 读超时），脚本回退为向量顺序并记录在 `rerank.applied_queries`；之后已给脚本加一次重试。

## 4. absent 题的可识别性

| 条件 | qwen | qwen-flash | MiniLM | e5-small |
|---|---:|---:|---:|---:|
| 合并语料，无 rerank，AUROC | 0.782 | 0.759 | 0.721 | 0.671 |
| 合并语料，有 rerank，AUROC | 0.927 | 0.930 | 0.758 | 0.851 |
| 有 rerank，保 95% 可答题时拒掉的 absent 比例 | 12/18 | 12/18 | 1/18 | 1/18 |

不加 rerank 时，top-1 距离几乎分不开 absent 与可答题（与 §7.10 一致：absent 最小距离低于可答题最大距离）。加 rerank 后 qwen 系的相关度阈值 ≥ 0.75 可保住 95% 可答题并拒掉三分之二 absent 题，剩下的 6 题多为"实体存在但字段缺失"（le035 月配额、le037 超时秒数、le041 预算次数）——rerank 会给"谈到该实体的 chunk"高分。结论：rerank 分数可作为拒答的前置信号，但不能替代答案层的 `abstention_correct` 裁判。

## 5. 结论与建议

1. **为 `local_docs` 开 rerank，保留 1000/200。** 这是本轮最大的单项增益（hit@3 +0.09～0.16，mrr_chunk +0.15，absent AUROC 0.93），且让 chunk 参数不再敏感；改 chunk 参数（800/0）在不加 rerank 时只对 qwen 基础版在语料 1 上有效，合并语料上最优点又变成 1000/0，不稳。代价是每次本地检索多 2 到 2.5 秒。
2. **可以切到 `qwen3.7-text-embedding-flash`。** 加 rerank 后两套语料 flash 都领先 0.015～0.03，跨语言更好，查询与索引更快；不加 rerank 时在小语料上落后 0.06，所以切换要与 rerank 一起做。
3. **两个本地模型不能用于本项目的中文语料。** MiniLM 不支持中文（合并语料 0.515）；e5-small 0.667 且跨语言 0.28，CPU 索引比云端慢。如果需要离线方案，下一候选是 bge-m3（2.2 GB，CPU 上预计索引慢一个量级），本轮未跑。
4. **gold 口径待修**：lc001 / lc002 四模型全丢，是因为答案在语料内另一份近重复文档里（project_report_zh、ise_technical_documentation_zh），单 `gold_doc_id` 判定过严，应允许多个 gold 文档或按 span 判定不限文档。
5. 以上均为评测结论，**产品默认值本轮未改**（计划 Q1-09 约定）。切换 flash 与开 rerank 需要单独的 change，并在 D0 冒烟与答案层评测上复核。

## 6. 局限

- gold 全部由 agent 单人构建，未经第二标注人；absent 题 18 道，AUROC 的置信区间很宽。
- 四个进程并发共用 4 核 CPU，`index_ms` 与本地模型的 `avg_query_ms` 偏高，只能看量级；云端 `avg_query_ms` 含网络往返。
- e5-small 用了 query/passage 前缀，MiniLM 无前缀；两者向量归一化后走 FAISS L2，距离与云端模型不可直接比较，只在模型内比较。
- rerank 只测了 `qwen3-rerank`；端点上 `qwen3-vl-rerank`、`gte-rerank-v2` 也可用，未对比。
- 语料 1 的 .md 加载自 5af2d42 起走 Unstructured 解析（chunk 190 而非冻结时的 209），本轮与 0918 一致，但与 0909 报告的 chunk 数不同。

## 7. 成本

| 运行 | 云端 embedding 次数（每模型） | rerank 次数 | 索引累计（每模型） |
|---|---:|---:|---:|
| 语料 1 完整网格 | 9,800 chunk + 1,102 query | 0 | qwen 2,796 s / flash 1,206 s / MiniLM 1,017 s / e5 1,708 s |
| 合并语料紧凑网格 | 8,583 chunk + 1,008 query | 0 | qwen 1,407 s / flash 1,345 s / MiniLM 766 s / e5 2,388 s |
| 语料 1 rerank 切片 | 4 组 × 38 | 608 | — |
| 合并语料 rerank 切片 | 4 组 × 84 | 1,344 | — |

云端 embedding 合计约 4.1 万次（两模型），rerank 1,952 次；MaaS 端点未返回 credits，按 token 计费金额未知。
