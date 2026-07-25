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
