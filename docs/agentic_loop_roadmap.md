# Agentic Loop 演进路线

本文是跳出 OpenSpec 的长线规划。`openspec/specs` 记录的是**当前**（plan-first）架构的既有约定，
其中相当部分会在本路线中被删除；本文描述的是**目标**架构和到达路径。两者冲突时以本文为准，
OpenSpec 中被废弃的条目在对应里程碑退出时清理。

近期里程碑（M0–M2）给到可直接执行的粒度，远期（M3–M6）只给目标、边界和退出判据。

## 0. 与 OpenSpec 的关系

**不做一次性大清理。** specs 描述的是仍在生产运行的路径（plan 路径到 M5 才退役），
在 loop 未验证前删除它们等于销毁"不许回归"的书面依据，直接违反 I5。清理挂在各里程碑的退出判据上，
用实际做完的工作换取，量与实际变化成正比。

代替清理的是**标注**。22 份 spec / 1826 行按本路线分为三类，标在各自 `spec.md` 顶部的 `Status` 行：

| Status | 份数 | 行数 | 含义 |
|---|---|---|---|
| `superseded by roadmap M<n>` | 4 | 365 (20%) | 能力将在该里程碑删除。**不新增 requirement、不加固、不写新 change**，仅接受阻断性缺陷的最小修复 |
| `reframing at M<n>` | 8 | 707 (39%) | 能力存续但立论框架要改（ReAct 系列现以 "fallback" 立论；另有 4 份带 plan 耦合的 requirement）。可修补，不在现框架上做大投入 |
| `active` | 10 | 754 (41%) | 当前契约，且在目标架构中存续 |

同一分类与不变量 I1–I5 已写入 [`openspec/config.yaml`](../openspec/config.yaml) 的 `context` 字段，
任何创建 openspec artifact 的 agent 都会读到，不必依赖人工提醒。

`changes/archive/` 永不清理——它是"为什么长成这样"的唯一记录，append-only，成本为零。

失效模式的根因是 **specs 被当成架构描述在用**，而架构描述没有过期机制。今后 openspec 只管
change proposal 与能力契约，架构方向归本文。

---

## 1. 终局形态

```
入口 (CLI / Flask)
  │
  ├─ 预处理     闲聊短路 · 时间约束解析 · 会话恢复
  ├─ 分析       analyze_query → 成功标准 checklist
  │
  ├─ Agentic Loop  act → observe → evaluate
  │     工具面    由 Skill Registry 在启动时派生
  │     状态      EvidenceLedger 跨轮次累积
  │     停止      确定性 critic + LLM judge + 预算上界
  │
  ├─ 组装       从 ledger 的 retained evidence 生成答案
  └─ 留痕       QueryExecutionTrace · audit
```

**没有分类器，没有静态计划。** 「这个问题属于哪个领域」这个问题不再被显式回答——模型通过选工具
隐式回答，选错由工具的确定性 preflight 挡回来。

### Skill 包结构

```
skills/<name>/
  skill.yaml        工具名 · 入参 schema · 预算 · 依赖的 provider · 可用性条件
  SKILL.md          面向模型：何时适用、正例/反例、答案格式
  handler.py        preflight(args) -> Ok | Reject(reason)
                    run(args) -> list[EvidenceItem]
  prompts/          该领域专属的抽取/格式化提示词
  evals/cases.jsonl 路由与行为回归用例
```

两个约定是这套结构的全部价值来源：

1. **`SKILL.md` 与 `evals/cases.jsonl` 是同一份知识的两种形态。** 散文进模型上下文，用例进 pytest。
   今天写在分类器提示词里的「反例：公司产品的定价……属于 general」这类回归修复，从此有测试锁住。
2. **`preflight` 是确定性的，且 LLM 无权绕过。** 这是保住可审计性的承重墙——模型的意图不能单独
   决定一次外部调用的参数是否合法。

Skill Registry 在启动时构建工具面：只有配置齐备（API key 存在等）的 skill 才注册。这替代目前散落
在各处的 `if domain in {...} and not self.google_api_key` 式检查。

---

## 2. 全程不变量

这些在**每个**里程碑都必须成立，是判断某次改动是否可以合入的硬条件：

| # | 不变量 |
|---|---|
| I1 | 每条被引用的证据可追溯到一次具体工具调用，provenance 链不断 |
| I2 | 任何执行路径下 audit 记录完整（loop 的非确定性使这条比现在更重要，不是更次要） |
| I3 | 外部调用的参数合法性由确定性代码裁决，LLM 判断不具备最终裁决权 |
| I4 | 工具调用次数、token、时延均有上界，且上界可配置、可观测 |
| I5 | 每个里程碑自身可发布、可回退；新旧路径由 feature flag 并存，而不是靠长命分支 |

I5 决定了这条路线的实施形态：**全程 flag 切换，不开长命分支。**

---

## 3. 资产去留

| 组件 | 归宿 |
|---|---|
| `_classify_with_keywords` / `_classify_with_llm` / `select_sources` / `generate_domain_specific_query` | 删 |
| `_make_routing_decision` + `DECISION_SYSTEM_PROMPT` | 删（loop 里「要不要检索」由选不选工具表达） |
| `_generate_keywords` + `KEYWORD_SYSTEM_PROMPT` | 删（查询改写变成模型的工具入参决策） |
| `build_query_plan` / `QueryPlan` / `PlanController` | 删，被 loop 实际轨迹取代 |
| `ReActDomainTool` / `DomainEvidenceSource` | 拆成 N 个 skill 工具 |
| `search/source_selector.py`（2910 行） | 解体到各 skill |
| `EvidenceLedger` | **留，且变重要**——loop 的跨轮次状态 |
| `verify_evidence_plan` | **留，换角色**——成为 loop 的确定性停止判据 |
| `QueryExecutionTrace` / `utils/audit_log.py` | 留 |
| `evidence/official_domain_resolver.py` + `source_tiering.py` | 留，成为工具 + tier 过滤器 |
| `analyze_query` | 留，成为 loop 的初始成功标准 |
| `EvidenceSource` ABC（[source_layer.py:172](../evidence/source_layer.py#L172)） | 留，是 skill handler 的挂载接口 |
| 闲聊短路 | 留，"你好" 不该开 loop |

**删除量大于新增量。** 若某个里程碑做下来净增代码，说明方向跑偏了，停下来复盘。

### 关于提示词层的修正

早期设想过「先建提示词注册表、消除重复」作为零风险的第一步。**放弃这个想法**：
`search_routing.py` 与 `langchain_orchestrator.py` 之间那对重复的 decision/keyword 提示词，
两份都随路由一起死。为将死的代码建注册表是净亏损。

真正需要治理的是**活下来的**提示词：loop 系统提示词、judge 提示词、各 skill 的抽取/格式化提示词。
它们随 M2 的 skill 包结构自然落位，不需要单独的里程碑。

---

## 4. 里程碑

### M0 — 止损与地基

**目标**：停止对将死模块的投入，建立可信基线，装好切换开关。

| 状态 | 工作项 | 说明 |
|---|---|---|
| ✅ | 冻结 router 加固 | `harden-finance-domain-routing` 已归档（含 `PARTIAL-REVERT.md`）；其 delta spec **未**同步进 `openspec/specs/`，避免新增一份出生即失效的 spec |
| ✅ | 抢救 preflight 资产 | symbol 抽取收紧（大写才认 ticker、`$AAPL` 语法、LLM 只校验歧义候选）与 provider 错误过滤保留在 `search/source_selector.py`，测试见 `tests/test_source_selector_finance_symbols.py` |
| ✅ | 丢弃分类器补丁 | 回退 LLM→keyword 交叉校验与分类提示词正反例；连带移除无调用方的 `allow_intelligent` 参数。知识转为数据存于 `skills/finance/evals/cases.jsonl` |
| ✅ | openspec 标注 | 22 份 spec 打 `Status` 行；`config.yaml` 的 `context` 与 `rules` 填入方向、分类与不变量（见 §0） |
| ☐ | 建立基线 | 用 [route_intent_dataset.csv](../dataset/route_intent_dataset.csv) 与 [final_answer_dataset.csv](../dataset/final_answer_dataset.csv) 跑现路径，结果落 `runtime/baseline/`，记录：路由准确率、答案质量、P50/P95 时延、每问 LLM 调用数与 token |
| ☐ | 装开关 | 引入 `engine.mode: plan \| loop`，默认 `plan`，行为零变化 |

**退出判据**：基线数字入库且可复跑；flag 存在且默认路径行为与改动前一致。

**风险**：基线数据集覆盖不足，导致后续对比失去意义。若 route_intent 用例少于 ~50 条，M0 内补齐。

---

### M1 — Loop 通电

**目标**：让已经写好但在默认配置下是死代码的 loop 成为可切换的主路径，并拿到与 plan 路径的对比数据。

现状：[react_loop_graph.py](../orchestrators/react_loop_graph.py) 是完整的 act/observe/evaluate 图，
带 judge、no-progress 检测、强制终止、trace。但唯一入口是 `_apply_postcheck` → `react_fallback`，
而 config 中 `postcheck.enabled=false`、`react_fallback.enabled=false`。

| 工作项 | 说明 |
|---|---|
| 提升为一等路径 | loop 从 postcheck fallback 提升为 `engine.mode=loop` 时的主执行器，不再依赖 postcheck 开关 |
| 统一成功标准 | loop 的 `_derive_checklist` 与 `analyze_query` 是同一件事的两份实现；改为 loop 消费 `analyze_query` 的产物 |
| 接通 ledger | loop 每轮的证据进 `EvidenceLedger`，答案从 retained evidence 组装（对齐 I1） |
| 接通 audit | 确认 loop 路径下 audit 记录与 plan 路径同样完整（对齐 I2） |
| 对比跑分 | 同一批 query 跑两条路径，产出对比报告 |

**退出判据**：loop 路径在基线集上答案质量不劣于 plan 路径；**或**明确量化出差距来源（哪类 query、
差在检索还是组装）。时延与成本的退化幅度被记录并接受。

**风险（本路线最可能停摆的地方）**：多轮 loop 的时延与 token 成本相对单次 plan 必然上升。
若 P95 时延或每问成本超出可接受范围，先调 `max_iterations` 与 judge 频率，而不是回退架构方向。

**注**：此时工具面仍是旧的 4 个工具，router 藏在 `domain_api` 内部，还删不掉。这是预期的。

---

### M2 — Skill 骨架 + finance 先行

**目标**：确立 skill 契约，用最痛的一个领域验证，跑通「一个 skill 从头到尾」。

选 finance 打头阵的理由：痛点最集中（三处硬编码补丁都围绕它）、已有 [测试](../tests/test_source_selector_finance_routing.py)、
M0 已抢救出它的 preflight 资产。

| 工作项 | 说明 |
|---|---|
| 定义契约 | `Skill` 协议、`skill.yaml` schema、registry、`preflight` 返回类型；handler 挂在 `EvidenceSource` ABC 上 |
| 可用性门 | registry 启动时按配置齐备性决定注册哪些工具 |
| finance skill | 从 `source_selector.py` 抽出：symbol 抽取（preflight）、quote/history 调用、答案格式化 |
| 拆工具 | `domain_api` 中的 finance 分支移出；两者并存到 finance 完全迁移 |
| 收编散落知识 | [langchain_orchestrator.py:1325-1344](../langchain/langchain_orchestrator.py#L1325-L1344) 那张硬编码 `finance_keywords` 表进 finance skill；三份重复的答案格式化实现（`ReActDomainTool._enhance_answer` / `_enhance_domain_answer` / `_format_*_answer`）合成一份 |
| 建 evals | `SKILL.md` 的正例/反例同步落成 `evals/cases.jsonl`，接入 pytest |

**退出判据**：finance 类 query 全部经由新 skill；`source_selector.py` 中 finance 相关分支删除；
evals 通过；基线集上 finance 子集不劣于 M1。

**待决策（M2 内定，不要现在定）**：
- **D1** preflight 拒绝时，是把 reason 返回给模型让它自我修正，还是静默降级到通用搜索？倾向前者，M2 量化。
- **D2** 一个 skill 暴露一个工具还是多个？finance 可能需要 quote / history 两个。以模型选对率为准。

---

### M3 — 其余 skill 迁移

**目标**：weather / location / transportation / sports 按 M2 确立的模式迁移，`source_selector.py` 解体。

顺序建议：weather（结构最简单）→ location → transportation → sports。每个 skill 独立可发布。

**退出判据**：`ReActDomainTool`、`select_sources`、`generate_domain_specific_query`、
`classify_domain` 全部删除；`search/source_selector.py` 文件消失。**router 在此里程碑真正死亡。**

**风险**：sports 与 location 的现有实现质量未经审视，迁移时可能发现是重写而非搬运。若某个领域
迁移成本远超其价值，允许直接降级为「不提供该 skill，落回通用搜索」——这是可接受的结局，
应显式记录而不是硬扛。

---

### M4 — 停止判据合并

**目标**：把两套互不知情的停止机制合并成一套。

现状是 loop 的 LLM judge 与 `verify_evidence_plan` 各判各的。合并后形态：
**确定性 critic（覆盖度/来源等级/约束满足）+ LLM judge（语义充分性）+ 预算上界**，三者构成终止条件。

这是本项目相对通用 agent 框架的真正差异点——多数框架的停止判据只有「模型说完了」或「到轮数上限」。

**退出判据**：系统中只存在一套停止逻辑；确定性 critic 可单独解释每次终止的原因并进入 trace。

**待决策**：judge 用哪个模型、调用频率、与确定性 critic 的优先级关系（谁能否决谁）。

---

### M5 — 拆除 plan 机制

**目标**：删除静态预规划。

`build_query_plan` / `QueryPlan` / `PlanController` 删除；step 级预算迁移为 per-tool 预算；
`QueryExecutionTrace` 与 `EvidenceLedger` 保留并成为唯一的执行记录来源；`engine.mode` flag 移除。

**退出判据**：`utils/query_orchestration.py` 只剩分析（`analyze_query`）、账本（`EvidenceLedger`）、
判据（critic）、留痕（trace）四块；plan 相关的 OpenSpec 条目清理。

**前置条件**：M4 完成。plan 不能在 loop 的停止判据可信之前拆——否则失去唯一的兜底。

---

### M6 — 长期能力（方向性，不排期）

到此架构收敛，后续是能力扩张。按预期价值排序：

| 方向 | 说明 |
|---|---|
| 会话与 loop 状态融合 | 已有 `conversation_store` + LangGraph checkpointer，多轮追问直接续跑 loop 轨迹而非重新开始 |
| 从 audit 自动挖回归用例 | audit 已记录完整轨迹；失败案例可半自动转成 `evals/cases.jsonl` 条目，让回归集自增长 |
| 并行工具调用 | 对比类问题天然可并行（「A 和 B 哪个便宜」应同时查两边） |
| 子 agent | 深度调研类问题下放给独立预算的子 loop |
| Skill 热加载 / 第三方 skill | 只有当 skill 契约稳定数月后才值得做 |

---

## 5. 度量

全程用同一套指标，每个里程碑退出时复跑，写入 `runtime/baseline/<milestone>/`：

| 指标 | 来源 |
|---|---|
| 路由/工具选择正确率 | `dataset/route_intent_dataset.csv` |
| 答案质量 | `dataset/final_answer_dataset.csv` + `tests/search_quality_pipeline.py` |
| P50 / P95 端到端时延 | `TimingRecorder` |
| 每问 LLM 调用数与 token | `TimingRecorder` + audit |
| 每问外部 API 调用数 | audit |
| skill preflight 拒绝率 | M2 起新增 |

**成本与时延是本路线的主要负债项**，从 M0 就开始记，不要等到发现问题才补埋点。

---

## 6. 执行纪律

- **一次一个里程碑。** 不要在 M1 未退出时开始 M2 的重构。
- **净代码量应为负。** 某里程碑做完净增代码，先复盘再继续。
- **flag 而非分支。** 违反 I5 的实施方式一律不采纳。
- **允许中途改判。** 每个里程碑的退出判据里都有「或明确量化出差距」这一支——数据说方向不对时，
  停在当前里程碑是合法结局，此时系统仍处于可发布状态。
