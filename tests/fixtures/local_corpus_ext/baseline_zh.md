# 基线度量手册

本文件是 agentic loop 路线（[roadmap](agentic_loop_roadmap.md) §5）的度量落地说明：怎么跑、记什么、M0 的实测数字。
基线 artefacts 落在 gitignored 的 `runtime/baseline/<milestone>/`（可再生，不入库）；本文记录各里程碑退出时的摘要数字与解读，供后续里程碑对比。

## 1. 运行入口

```bash
# 完整跑（route_intent + final_answer 两个数据集），输出到 runtime/baseline/m5/
python -m tests.baseline_runner

# 仅路由数据集
python -m tests.baseline_runner --datasets route

# 冒烟（每数据集前 N 条）
python -m tests.baseline_runner --max-queries 5

# 写入指定里程碑目录（M5 起只有单一执行器）
python -m tests.baseline_runner --milestone m5

# 仅跑 finance 路由子集（M2）
python -m tests.baseline_runner --datasets route --intent-label finance --milestone m2
```

实现：`tests/baseline_runner.py`。它用与 `main.py` 默认入口一致的
`create_langchain_orchestrator` 构建单一执行器，逐问驱动 `answer()` 并采集指标。M0–M4 的
plan/loop 分目录是迁移期历史 artefact；M5 已删除 `--engine-mode`，但 `--compare` 仍可读取旧目录。

> 全量跑消耗真实搜索/LLM 配额，75 问约 60–90 分钟。建议先用 `--max-queries` 冒烟，再按里程碑退出判据全量跑。

## 2. 指标定义（对齐 roadmap §5）

| 指标 | 来源 | 计算方式 |
|---|---|---|
| 路由/工具选择正确率 | `route_intent_dataset.csv` | `infer_route(control)` 与 `expected_route` 精确匹配；混淆矩阵记入 summary |
| 答案质量 | `final_answer_dataset.csv` | `fact_coverage`：每个 must_include 子句的显著词项命中率（部分credit），跨子句取均值 |
| P50/P95 端到端时延 | `response_times.total_ms` | percentile，记录 mean/min/max |
| 每问 LLM 调用数 | `response_times.llm_calls` | 计数 |
| 每问 token | `response_times.llm_calls[*].{input,output,total}_tokens` | 求和；`token_capture_rate` 标记捕获覆盖度 |
| 每问外部 API 调用数 | `response_times.tool_calls` | 计数（当前仅记录经 `record_tool_call` 的调用） |

`infer_route` 把 orchestrator 的 `control` 投影到数据集的路由词表（`general_web` / `weather_api` / `finance_api` / `sports_api` / `chat` 等）。M0-M2 中，只有领域 API 直接回答才算结构化路由命中。M3 删除 router 后，改为以**独立 skill 的确定性 preflight 被接受且工具被实际尝试**作为工具选择命中；provider 无数据后继续 web 搜索不抹掉该路由事实。仅有领域提示、未通过 preflight 仍不算命中。这个口径变更避免把 provider 覆盖度混进工具选择指标，M3 数字不能直接当作同口径的 M0 提升。系统当前没有 calculator / time / translation / code 工具，这类问会落到 `general_web`，是真实的路由缺口，基线如实暴露。

## 3. M0 实测数字

运行：`python -m tests.baseline_runner --datasets route --max-queries 10 --milestone m0`
（取 route_intent 前 10 条，覆盖 general / weather / calculator / time / chat / summary / news / sports / finance / math 十类路由）

| 指标 | M0 值 |
|---|---|
| 路由正确率 | **0.40** (4/10) |
| P50 / P95 时延 | 42.4s / 123.8s |
| 每问 LLM 调用数（均值 / P50） | 3.2 / 4.0 |
| 每问 token（in/out/total 均值） | 489 / 393 / 882 |
| token 捕获率 | 0.80 (8/10) |

混淆矩阵（行=期望，列=推断；未列出的推断均为 0）：

| 期望 \ 推断 | general_web | finance_api |
|---|---|---|
| general_web | 3 | |
| weather_api | 1 | |
| calculator | 2 | |
| time_api | 1 | |
| chat | 1 | |
| sports_api | 1 | |
| finance_api | | 1 |

### 解读

- **路由器把几乎所有结构化问都退回 `general_web`**：weather / calculator / time / chat / sports 全部 miss。这不是评测脚本错配，而是系统现状——除 finance 外，结构化领域要么无对应工具（calculator/time/chat），要么领域判定未触发独立 API 路径（weather/sports 落到搜索）。这正是 roadmap「没有分类器、模型选工具隐式回答领域问题」要解的痛点，loop 路径的 preflight + skill 工具面应在此类问上拿回准确率。
- **finance 命中（1/1）**：M0 已抢救的 preflight 资产（symbol 抽取 + provider 调用）支撑了唯一命中的结构化路由，印证 finance 先行（M2）的选型。
- **token 捕获率 0.80**：M0 在 answer 生成与直答路径接了 `usage_metadata` 透传；未捕获的 2 成主要是领域增强回答（`_enhance_domain_answer`）与分析/分类等小型调用，M2 skill 化时随工具面统一补齐。
- **时延 P95 ~124s**：单问成本（调用数 + token + 时延）从 M0 起即入库，作为 loop 多轮检索的对照基准；M1 退出判据要求「时延与成本退化幅度被记录并接受」。

## 4. 与后续里程碑的对比约定

每个里程碑退出时复跑本脚本，写入对应 `--milestone <n>` 目录，并把摘要数字追加到本文件的同名小节。对比以**同一数据集 + 同一指标定义**为准；指标定义若变更（如 M2 新增 preflight 拒绝率），在变更里程碑的小节显式说明并向后追溯。

## 5. M2 finance 子集

以下是 M2 代码版本当时的运行命令；M5 runner 已不再接受 `--engine-mode`：

```bash
python -m tests.baseline_runner --datasets route --intent-label finance --milestone m2 --engine-mode plan --num-results 3 --max-tokens 1200
python -m tests.baseline_runner --datasets route --intent-label finance --milestone m2 --engine-mode loop --num-results 3 --max-tokens 1200
python -m tests.baseline_runner --compare runtime/baseline/m2/plan runtime/baseline/m2/loop
```

| 指标 | plan | loop |
|---|---:|---:|
| 路由/工具选择正确率 | **1.00 (5/5)** | **1.00 (5/5)** |
| P50 / P95 时延 | 0.29s / 86.26s | 71.40s / 78.28s |
| 平均时延 | 21.78s | 63.40s |
| 平均 loop 轮次 | - | 4.6 |

5 条由 4 个 `finance_api` 正例和 1 个应保持 `general_web` 的 inflation 边界例组成。M0/M1
可用的同类参考只有 route009（1/1），M2 未回归且扩大了覆盖。loop 平均时延比 plan 高
41.62s，并频繁触及 5 轮上限，这是已量化的 M1 风险，不应被路由正确率掩盖。

本轮 loop 的 token 统计为 0 是**未捕获**而非零消耗：LangGraph 节点调用还没有汇入外层
`TimingRecorder`。因此 M2 不用该数字做成本结论；时延与轮次是当前可信成本证据。

`final_answer_dataset.csv` 没有 finance 行，不能给出同口径的 finance 答案质量分。补充验证为：
真实 CLI 通过 `finance_market_data` 从 Finnhub 返回 AAPL 报价；pytest 覆盖 quote/history 格式、
部分 provider 失败、全部 provider 失败回退、preflight 拒绝反馈与 provenance。

## 6. M3 structured-skill 子集

以下是 M3 代码版本当时的运行命令：

```bash
python -m tests.baseline_runner --datasets route \
  --intent-label weather,location,transportation,sports \
  --milestone m3 --engine-mode plan --num-results 1 --max-tokens 400
```

| 指标 | M3 plan |
|---|---:|
| 路由/工具选择正确率 | **1.00 (16/16)** |
| P50 / P95 时延 | 9.13s / 29.11s |
| 平均时延 | 11.05s |
| 每问 LLM 调用数（均值） | 1.75 |
| 每问 token（均值） | 349 |
| 每问外部 API 调用数（均值 / P50 / P95） | 1.50 / 2.00 / 2.25 |
| token 捕获率 | 0.50 (8/16) |

子集包含 12 条应尝试 structured skill 的正例，以及 4 条应保持 `general_web` 的模糊地点、
模糊起点/终点和班次边界例；另含新增的显式 Routes 与 Places 正例。16 条全部命中预期工具选择。
实际 provider 验证中 Weather current、Places、Routes、TheSportsDB 明确队伍赛程均返回数据；部分
forecast/赛事无数据时按设计继续 web fallback。

skill handler 现在把 provider 调用同时写入 search timing 与 `TimingRecorder.tool_calls`；因此本轮
`external_api_calls_per_query=1.50` 是可用于后续里程碑比较的调用计数，不再把真实调用误记为零。

## 7. M4 termination-critic smoke

以下是 M4 代码版本当时的运行命令（同一 final-answer 前 5 条、小样本烟测，不外推为全量答案质量）：

```bash
python -m tests.baseline_runner --datasets answer --max-queries 5 \
  --milestone m4 --engine-mode plan --num-results 1 --max-tokens 400
python -m tests.baseline_runner --datasets answer --max-queries 5 \
  --milestone m4 --engine-mode loop --num-results 1 --max-tokens 400
python -m tests.baseline_runner --compare \
  runtime/baseline/m4/plan runtime/baseline/m4/loop
```

| 指标 | M4 plan | M4 loop |
|---|---:|---:|
| 有回答 | 5/5 | 5/5 |
| fact coverage（5 条均值） | 0.268 | 0.421 |
| 平均时延 | 17.14s | 38.84s |
| P50 / P95 时延 | 16.37s / 20.25s | 19.24s / 90.10s |
| 每问 LLM 调用数（均值） | 3.8 | 4.4 |
| 每问 token（均值） | 1,235 | 2,105 |
| 每问外部 API 调用数（均值） | 0.0 | 1.0 |
| loop 轮次（均值 / P95） | - | 2.4 / 4.8 |
| loop 终态 | - | `succeeded` 4，`exhausted` 1 |

M4 的判断不是“loop 已全面优于 plan”：样本只有 5 条，且 loop token 均值约为 plan 的 1.7 倍，
其中一条触及 5 轮上限。可信结论是统一预算确实生效，终态分布可测，loop 的 act/judge/provider
调用已汇入共享 timing；高轮次成本仍是 M5 前必须保留的风险。自动化回归另外验证每个终态 verdict
都携带 `action`、`deterministic_pass`、`rule_hits`，workflow trace 展示同一 critic 的命中规则；
正向 judge 不能越过确定性缺口，负向 judge 可以否决规则通过，judge 故障只降级为规则判定。

原始结果位于 `runtime/baseline/m4/{plan,loop}/`（gitignored），对比结果为
`runtime/baseline/m4/loop/comparison.json`。

## 8. M5 sole-executor smoke

运行（与 M4 相同的 final-answer 前 5 条）：

```bash
python -m tests.baseline_runner --datasets answer --max-queries 5 \
  --milestone m5 --num-results 1 --max-tokens 400
```

| 指标 | M5 sole loop | M4 loop 参考 |
|---|---:|---:|
| 有回答 | 5/5 | 5/5 |
| fact coverage（5 条均值） | **0.581** | 0.421 |
| 平均时延 | **52.39s** | 38.84s |
| P50 / P95 时延 | **64.04s / 101.99s** | 19.24s / 90.10s |
| 每问 LLM 调用数（均值） | **6.6** | 4.4 |
| 每问 token（均值） | **3,690** | 2,105 |
| 每问外部 API 调用数（均值） | **1.4** | 1.0 |
| loop 轮次（均值 / P95） | **3.4 / 5.0** | 2.4 / 4.8 |
| loop 终态 | `succeeded` 4，`exhausted` 1 | `succeeded` 4，`exhausted` 1 |

这仍只是 5 条烟测，不能证明总体质量提升。能成立的保守结论是：删除 plan 与运行时开关后，
同一批问题均有回答，覆盖率高于 M4 loop 小样本；但平均时延增加 13.55s、P95 增加 11.89s，
token 均值约为 M4 loop 的 1.75 倍，3 条跑到第 5 轮，其中 1 条仍 `exhausted`。这组结果明确暴露
成本退化，不能写成“性能改善”。原始结果位于 `runtime/baseline/m5/`（gitignored）。

补充真实 CLI 验证使用 `北京现在天气如何？`。首次运行暴露 `现在` 错触发多年历史恢复，造成 8 个
额外年份搜索和 145.10s 总耗时；收紧为显式多年/历史约束后，复跑不再出现年份扇出，总耗时
77.76s。该次 trace 只含 4 条实际工具调用（weather 失败、web、recovery、web），而不是按返回证据
重复记账，并完整显示每个工具的 limit/used。provider 失败后仍可能触及 loop 上限，这是保留的
运行质量风险，不应解释成 M5 已消除所有时延问题。

---

## 6. 自主度两模式对比（`autonomy.mode`：guided vs autonomous）

可注入自主度（见 [roadmap](agentic_loop_roadmap.md) M6「2026-08 已落地」）让同一张图在两种
规则强度下运行。本节是两模式定量对比的口径说明与结果登记处。

### 6.1 口径与运行入口

两模式分目录落盘，互不覆盖：`runtime/baseline/<milestone>/<autonomy>/`，每条记录携带生效自主度
（`autonomy`）与其来源。`--compare` 在两个目录都携带 `run_meta.autonomy` 时自动切到模式感知的
两模式摘要（`autonomy_comparison.json`），按 qid 对齐产出 delta。

```bash
# 事实型安全性验证（8.4）：在 final_answer_dataset 上各跑一模式
python -m tests.baseline_runner --datasets answer --autonomy guided   --milestone autonomy
python -m tests.baseline_runner --datasets answer --autonomy autonomous --milestone autonomy

# 开放式价值验证（8.5）：在 open_task_dataset 上各跑一模式（人工评分）
python -m tests.baseline_runner --datasets open --autonomy guided   --milestone autonomy
python -m tests.baseline_runner --datasets open --autonomy autonomous --milestone autonomy

# 两模式 diff（自动选模式感知摘要）
python -m tests.baseline_runner --compare runtime/baseline/autonomy/guided runtime/baseline/autonomy/autonomous
```

### 6.2 记录的指标

- **答案质量（原计划）**：事实型用自动 fact-coverage（`final_answer_dataset`）；开放式用人工评分，rubric 为
  各样本的 `scoring_dimensions` 列（如 `breadth_of_tradeoffs`、`citation_of_real_world_examples`）。
- **成本侧**：P50/P95 时延、每问 token、LLM 调用数、外部 API 调用数。
- **行为侧**：迭代数、压缩次数（`compactions`）、峰值上下文占比、**advisory 缺口计数**
  （`autonomous` 下 critic/引用 advisory 仍记录但不绑定，该计数是“模型忽略了多少条 critic 提醒”
  的代理指标，是将来是否收紧的唯一数据来源）。

### 6.3 退出判据

- 事实型子集：原计划比较 fact-coverage；2026-09-08 实测发现关键词重叠不验证事实正确性，安全性判断必须同时检查逐题核心正确性，不能只凭该指标通过。
- 开放式子集：有可复述的人工评分结论（价值证明）。
- 成本倍数被明确记录；原先预期 `autonomous` 每问 token / P95 时延约为 `guided` 的 3–4 倍，这是待验证假设，非实测结果或用户接受记录。
- **若开放式子集未显示优势，停在此处并记录量化结论是合法结局**（roadmap §6「允许中途改判」）。

### 6.4 结果

2026-09-08 完成两轮真实比较，研究编号 `autonomy-20260908-measured`：每轮 20 事实题 + 20 开放题 × 两模式，**160 条运行及统一模型辅助评审全部完成**。第一轮分析后仅做一组修改，第二轮重新冻结、反转模式调度顺序，保留全部失败。详见[完整报告](reports/autonomy_evaluation_20260908/report.md)、[逐题附表](reports/autonomy_evaluation_20260908/cases.md)和[冻结协议](reports/autonomy_evaluation_20260908/protocol.md)。

| 指标 | 首轮 guided | 首轮 autonomous | 次轮 guided | 次轮 autonomous |
|---|---:|---:|---:|---:|
| 事实核心全对 /20 | 18 | 19 | 20 | 19 |
| 开放完整交付 /20 | 13 | 13 | 14 | 18 |
| 开放辅助分 /100 | 62.50 | 61.88 | 68.75 | 84.38 |
| 开放 token 均值（HTTP） | ≥32,198 | 7,176 | ≥59,351 | ≥13,091 |
| 开放 P95 秒 | 660.28 | 187.47 | 660.27 | 104.86 |
| 开放硬超时 /20 | 4 | 0 | 3 | 1 |

产品修改为工具协议归一化/交付边界、shim usage 保留、模型拥有澄清权时的循环内归属修正；主模型、预算和默认 guided 未改。辅助评审不是人工评分。次轮 autonomous 开放题较 guided 8 胜 / 8 平 / 4 负，平均高 15.625 分，但 `final016` 两轮都出现核心事实错误，不能宣告事实安全性不劣。`open017` 两轮均只给计划却为 succeeded，仍未解决。

两轮成本以独立 HTTP usage 为准，主评测已知 ≥2,677,496 token，裁判与废弃评审额外消耗单列于报告，USD 未知。应用已注册调用 usage 完整率从 0/75 到 75/75，但次轮仍有两题漏记嵌套调用；入口请求 `max_tokens=4000/temperature=0.2` 未传进 loop，主模型对象配置实际为 5000/0.7，两轮相同。详见[运行审计](reports/autonomy_evaluation_20260908/runtime_audit.md)。

预算决策：保留原默认值，不无依据扩大调用预算或调整压缩阈值。两轮各 75 条有效 loop 状态都没有压缩，次轮最高上下文比例仅约 0.301，不足以完成长上下文校准。8.4 不劣门槛未通过、8.5 真人评分未进行、8.6 长上下文校准未完成；8.7 的结果登记完成。不把本次两轮报告交付等同于 OpenSpec 全部验收闭合。

---

## 7. 质量评测（[quality_evaluation_plan.md](quality_evaluation_plan.md)）

本章是设计文档 D0–D11 指标的登记处。产物落 gitignored 的 `runtime/quality/<date>-<tag>/`，报告进 `docs/reports/quality_evaluation_<date>/`。2026-09-09 首轮**没有授权真实运行**，登记的全部是离线回归、历史真实产物重算与禁网探针的数字；详见 [首份报告](reports/quality_evaluation_20260909/report.md)。2026-09-18 起按授权补跑真实运行，数字逐节以"2026-09-18"列登记。

### 7.1 运行入口

```bash
env1/bin/python -m pytest -q -m quality_offline                        # 零成本回归（≈10 秒）
env1/bin/python -m tests.quality_runner --suite offline                 # 同上 + 各离线评测 JSON，断言 0 次网络请求
env1/bin/python -m tests.quality_runner --suite validity --max-queries 5   # [真实运行] D0 冒烟
env1/bin/python -m tests.quality_runner --suite search|local|answer|reliability  # [真实运行]
env1/bin/python -m tests.quality_report --run runtime/quality/<run>     # scorecard.json + report.md
env1/bin/python -m tests.quality_report --compare <run_a> <run_b>       # 回归门（设计 §5.2）
```

裁判模型与 runner 默认参数取自入库的 `config.quality.json`（`runner` / `judge` 两块，不含凭据）；显式 CLI 参数优先，`--quality-config <file>` 或 `ISE_QUALITY_CONFIG` 可整体换一份。该文件的哈希写入 `run_meta.json` 与 `review-manifest-v4.json`。

### 7.2 质量评测 · D0

| 指标 | 2026-09-09 | 2026-09-18 | 门槛 | 说明 |
|---|---|---|---|---|
| `param_forwarding_pass` | 0/3 严格断言（xfail，QD-20260909-01/02） | **1.0（6/6）** | == 1.0 | QD-01/02 于 2026-09-18 修复：线上请求体 4000/0.2、`web_search` 请求 3 条，`control.request_parameters` 回显 |
| `token_capture_rate` | 未运行 | **1.0** | == 1.0 | 5/5 题应用记账 token 与 transport 旁观一致 |
| `usage_reconciliation_gap` | 未运行 | **0.0** | ≤ 0.05 | |
| `tool_call_capture_ratio` | 未运行 | **1.0** | ≥ 0.95 | 分母为 TransportObserver 观察到的全部非 LLM 请求 |
| `search_call_capture_ratio` | 未运行 | **1.0** | ≥ 0.95 | |
| `trace_completeness` | 未运行 | **1.0** | ≥ 0.95 | 0 题 trace 截断 |
| `audit_truncation_rate` | 未运行 | **0.0** | ≤ 0.05 | 5 条 audit 记录，无截断字段 |

2026-09-18 冒烟（`runtime/quality/20260918-validity-20260918`，final001–005，guided，deepseek-v4-flash，入口 4000/0.2/5）：退出门三项全部通过，`passed=true`。同批 5 题的循环终态为 succeeded 1、evidence_insufficient 2（各只发起 1 次搜索）、stagnated 2（未发起搜索），这是 D7/D8 的问题，不影响 D0 结论，在同日报告里跟进。

### 7.3 质量评测 · D1（`dataset/query_analysis_gold.csv`，65 题，确定性层）

| 指标 | 值 | 门槛 |
|---|---:|---|
| intent_shape 准确率 | 0.846 | — |
| 对比类成员 P / R / F1 | 0.867 / 0.605 / 0.712 | F1 ≥ 0.9 未达 |
| 对比类 `noise_member_rate` | **0.133** | 0（未达，QD-20260909-04） |
| 实体 P / R | 0.474 / 0.846 | — |
| claim_classes micro-F1 | 0.747 | — |
| `critical_ambiguity` P / R | 0.80 / 0.73 | P ≥ 0.9 未达 |
| `existence_query` 精确率 | 0.60 | — |
| time_scope 准确率 | 0.908 | — |
| `false_temporal_fanout_rate` | **0.0** | 0（通过） |

### 7.4 质量评测 · D2 preflight（`skills/*/evals/cases.jsonl`，161 例）

precision / recall：finance 0.72 / 1.00，weather 0.66 / 0.95，location 0.68 / 1.00，transportation 0.54 / 1.00，sports 0.50 / 1.00；53 例 known_gap（QD-20260909-05）。门槛 precision ≥ 0.95 全部未达。

### 7.5 质量评测 · D6（离线 + 历史台账）

分级 167 URL：official_precision **1.000**、official_recall 0.457、strict 准确率 0.671、权威折叠 0.856、denylist_compliance **1.0**、non_evidence_exclusion **1.0**、仿冒 TLD 误判 first_party 4/30。解析器回放 78 实体：accuracy 0.282（pinned 15/15）、none_rate 0.654、缓存错误 official 4。历史 r2 台账：retained 34.1% / limited 14.0% / rejected 51.8%，权威占 retained 12.4%，`aggregator_leak_rate` 0.063（authority_required 题 0）。

### 7.6 质量评测 · D7 引用（历史 r2，80 题，离线重跑 `check_citations`）

`hallucinated_citation_count` **7 处 / 3 题**（硬门槛未通过）；`citation_recall` 0.486（guided 事实 1.0 / autonomous 事实 0.255）；`authority_compliance` 0.215；`pricing_source_compliance` 0.947；`recency_compliance` 0/3；37/80 题零引用。v3 裁判：core_correct=2 39/40（final016 错）。r1：幻觉引用 7 处 / 2 题，`citation_recall` 0.285。

### 7.7 质量评测 · D8（历史）

r2：`false_exhaustion_rate` **0**（0/3）；`premature_success_rate` 0.019（final016）；forced / degraded synthesis 0.267 / 0.253；judge_invocation 0.410、**judge_error_rate 0.138**（≤ 0.05 未达；r1 0.072）；invalid_tool_request 0.005；narration guard 0；no_progress P95 2；首答平均 1.47 轮；advisory 忽略率 0.923（r1 0.957）；压缩 0。按组迭代数 3.20 / 1.05 / 4.75 / 1.42，与自主度报告 §4.4 一致。

### 7.8 质量评测 · D9（历史 r2）

token 均值 18,963（P50 5,239 / P95 76,892；75/80 取 transport 旁观）；时延 P50 34.3 s / P95 438.5 s；阶段均值 act 45.2 s、judge 25.7 s、synthesize 5.9 s、search 5.2 s；非成功终态 23 题占总 token **81.5%**（≤ 10% 未达）；每正确答案 7,333 token（事实题 guided 12,452 / autonomous 1,944）；credits 与 USD 未知。

### 7.9 质量评测 · D10 / D11

故障注入 8/8 通过。历史 r2 产物凭据泄漏 0；注入集 22 页结构检查全部通过；`injection_resistance`、`consistency_at_3` 未运行。

### 7.10 质量评测 · 本地 RAG 参数扫描（2026-09-18 真实运行，`runtime/quality/20260918-quality-20260918/local_rag_eval.json`）

语料 `tests/fixtures/local_corpus/`（16 文件），gold 38 题（可答 32、absent 6、跨语言 7）；embedding 固定 `qwen3.7-text-embedding`（openai_compatible），29 组 chunk_size × overlap（overlap ≥ chunk_size 的组合跳过），k∈{3,5}，只发 embedding 请求。排序以 `chunk_hit_at_3` 为主、`mrr_chunk@3` 为次。

| 组 | chunk 数 | hit@3 | mrr_chunk@3 | doc_hit@3 | 跨语言 hit@3 | hit@5 | index_ms | avg_query_ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **800 / 0（最优）** | 231 | **0.875** | 0.750 | 0.969 | 0.86 | 0.875 | 22,663 | 464 |
| 800 / 100 | 242 | 0.875 | 0.724 | 0.906 | 0.86 | 0.875 | 20,298 | 327 |
| 800 / 300 | 275 | 0.844 | 0.771 | 0.906 | 0.86 | 0.875 | 18,341 | 306 |
| 1500 / 200 | 119 | 0.844 | 0.724 | 0.906 | **1.00** | 0.875 | **10,636** | 330 |
| **1000 / 200（当前默认，第 24 位）** | 191 | **0.750** | 0.672 | 0.906 | 0.86 | 0.844 | 13,454 | 319 |
| 300 / 50（最差） | 709 | 0.594 | 0.469 | 0.875 | 0.29 | 0.750 | 45,323 | 317 |

- 默认 1000/200 在 k=3 与 k=5 下都排 **24 / 29**；k=5 最优为 1500/50（hit@5 0.906）。默认组比最优组多丢 4 题（lc003 / lc010 / lc012 / lc030），最优组丢的 4 题（lc001 / lc002 / lc029 / lc032）默认组同样丢；即最优组在逐题上严格不差于默认组。
- 规律：chunk_size 800 的 6 组全部进前 11；300 的 5 组全部垫底，跨语言 hit 0.29–0.57；overlap 对结果影响远小于 chunk_size。1500 系列跨语言 hit 全部 1.00 且索引最快（10.6–15.3 s），但 hit@3 仅 0.78–0.84。
- absent 题：6 题 top-1 L2 距离中位 0.965（最优组）/ 0.917（默认组），可答题 top-1 中位 0.642 / 0.627；但 absent 最小距离 0.556（最优组），低于可答题最大距离 1.07，**单一距离阈值无法分离 absent 与可答**，abstention 仍要靠后续 judge。`score_margin_negative_share` 0.75（最优）/ 0.70（默认）。
- 成本：29 组索引累计 735 s，embedding 查询 1,102 次；无 LLM 调用。
- 按计划 Q1-09 **不改默认值**；调整 `localRag.chunk_size / chunk_overlap` 需先补第二个 embedding 模型对照：本轮只覆盖单模型，无法区分"参数差"与"模型差"。

### 7.11 数据集规模与标注人

见首份报告 §6；全部由 agent 单人构建（`gold_verified_by` 注明），时效类 gold 除 final037/039/041/059 外未对照官方页核实。
