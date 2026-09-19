# 质量评测人工标注指南

配套 [质量分析设计](../quality_evaluation_design.md) 附录 B/C 与 [实施计划](../quality_evaluation_plan.md) Q1-04 / Q4-02 / Q4-05。标注文件放 `dataset/annotations/`，命名 `search_<date>.json`、`answer_<date>.csv`、`loop_<date>.csv`；模板见同目录 `*_template.csv`。标注者与数据集构建者尽量不是同一人；做不到时在文件的 `annotator` 列写明。

## 0. 通用规则

- 先读题、看 gold（含 `dataset/annotations/gold_errata.json` 的勘误），再看系统答案；不要反过来。
- 只依据题目本身的必需信息打分，不因参考答案的附加细节缺失扣分（protocol.md 的做法）。
- 不确定时写 1 分并在 `notes` 说明；不要为了"凑整"改成 0 或 2。
- 每行必填 `annotator`（姓名或稳定代号）与 `annotated_at`（YYYY-MM-DD）。
- 双标：随机抽 20%（每批至少 8 题）由第二人独立标注，产出后用 `python -m tests.quality.agreement` 算 kappa；kappa < 0.6 的维度在报告里只报人工分。

## 1. 搜索结果标注（`search_<date>.json`，D3）

由 `tests/search_quality_pipeline.py collect` 产出的 JSON 直接填写 `judgment`：

### 轻量档（每题 < 1 分钟）

| 字段 | 取值 | 判据 |
|---|---|---|
| `judgment_mode` | `top3_only` | 固定 |
| `top3_has_answer_evidence` | true / false | 前 3 条结果的标题+摘要里，至少一条**直接包含**回答问题所需的事实（不是"看起来相关"） |
| `core_correct` | 0 / 1 / 2 | 系统最终答案的核心事实：2 与 gold 一致；1 方向对但关键数值/日期/名称有偏差或含糊；0 错或没答 |
| `annotation_complete` | true | 填完后置 true |

### 详细档（用于 nDCG 与 rerank 实验）

| 字段 | 取值 | 判据 |
|---|---|---|
| `judgment_mode` | `detailed` | 固定 |
| `relevance_grades` | 与 `search_hits` 等长的 0/1/2 列表 | 0 无关；1 相关但不足以回答；2 直接含答案证据 |
| `relevant_urls` | URL 列表 | 可替代逐 rank 打分；gold 命中已预填，只需复核不要照抄 |
| `core_correct` | 0 / 1 / 2 | 同上 |

`gold_doc_urls` 由脚本从 gold_doc 数据集预填，不需要修改。

## 2. 答案标注（`answer_<date>.csv`，D7）

列：`qid, query, core_correct, request_completeness, evidence_support, abstention, delivered, factual_concerns, annotator, annotated_at, notes`。

| 维度 | 2 | 1 | 0 |
|---|---|---|---|
| `core_correct` | 核心事实与 gold（含勘误）一致 | 方向对但关键数值/日期偏差；或在正误之间摇摆 | 错、未答、只给计划、错误来源支撑的错误答案 |
| `request_completeness` | 问题的每个子需求都被回应 | 部分回应 | 未回应 |
| `evidence_support` | 有来源的实质性断言都被其引用的证据支持（只看台账里的记录，不联网核实） | 部分支持 | 无可用支持；`[En]` 无对应记录 |
| `abstention`（仅拒答题） | 明确说明未找到/不可答，且不编造 | 含糊 | 编造 |
| `delivered` | true / false：非空、非错误信息、非"迭代用尽"模板、非"我将去查"的计划 | | |

`factual_concerns` 写具体问题（如"日期答成 9 月 7 日"），多个用 `;` 分隔；没有写空。

### gold 勘误流程

1. 标注前发现 gold 与官方来源冲突：在 `dataset/annotations/gold_errata.json` 增加 `{"qid": {"core_fact": ..., "note": ..., "fixed_on": ...}}`，附来源 URL。
2. 原 CSV 不改写；裁判 v4 与人工都以勘误后的 `core_fact` 为准。
3. 看过系统答案之后不得再改勘误（避免按答案改 gold）。

### 双标一致性验收（Q4-02）

两名标注者按本指南独立标同一批 5 题；任一维度分歧 > 1 题即修订指南后重标。记录放 `dataset/annotations/pilot_<date>.csv`。

## 3. 循环判定抽样（`loop_<date>.csv`，D8）

列：`qid, mode, iteration, verdict_reason, deterministic_pass, block_necessary, judge_correct, clarification_necessary, annotator, annotated_at, notes`。

- `block_necessary`（critic 阻断是否必要）：看该轮被驳回的草稿——若草稿已能正确、有据地回答问题则 0（误拦），否则 1。
- `judge_correct`（judge 拒绝是否正确）：judge 拒绝的草稿最终证明确有问题则 1，否则 0。
- `clarification_necessary`：系统发起澄清的题，问题本身是否真的无法在不澄清的情况下作答（1 是 / 0 否）。
- 采样：`deterministic_pass=false` 轮次 30 条、judge 拒绝 10 条、澄清题全量。

## 4. 本地 gold chunk（`dataset/local_chunk_gold.csv`，D5）

`gold_span` 必须是语料原文的子串（空白归一化后），多段用 ` || ` 分隔；`is_absent=1` 的题不填 span。提交前跑 `python -m tests.quality.local_gold_check`。

## 5. 网页 gold（`dataset/web_gold_zh.csv`，D3/D4）

`gold_doc_url` 必须是当天可直接抓取的具体页面（不是站点首页），`gold_span` 是正文中的原句；提交前跑 `python -m tests.quality.web_gold_check --fetch`。`authority_required=1` 的题 gold 必须是官方页面。
