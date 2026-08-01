# Agentic Loop 演进路线

本文是跳出 OpenSpec 的长线规划。`openspec/specs` 记录当前能力契约，本文描述目标架构和到达路径；
两者冲突时以本文为准，OpenSpec 中被废弃的条目在对应里程碑退出时清理。

M0–M5 已落地并保留可复现实现与测量记录；M6 是不排期的长期能力方向。

## 0. 与 OpenSpec 的关系

**不做一次性大清理。** M0–M4 期间 specs 仍描述生产 plan 路径；M5 在 loop 完成验证后才删除
plan 契约。清理始终挂在里程碑退出判据上，用实际完成的替代能力换取。

M0 的 22 份 spec / 1826 行曾按下表标注；这是迁移起点快照，不是当前计数：

| Status | 份数 | 行数 | 含义 |
|---|---|---|---|
| `superseded by roadmap M<n>` | 4 | 365 (20%) | 能力将在该里程碑删除。**不新增 requirement、不加固、不写新 change**，仅接受阻断性缺陷的最小修复 |
| `reframing at M<n>` | 8 | 707 (39%) | 能力存续但立论框架要改（ReAct 系列现以 "fallback" 立论；另有 4 份带 plan 耦合的 requirement）。可修补，不在现框架上做大投入 |
| `active` | 10 | 754 (41%) | 当前契约，且在目标架构中存续 |

M5 退出时共有 19 份 active spec；`query-plan-orchestration`、
`query-postcheck-fallback`、`search-routing-core` 三份现行 spec 已删除，其余受影响契约已改写为
单一 loop、实际工具调用、ledger 和 critic 语义。归档 change 仍保持 append-only。

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
| I5 | 每个里程碑自身可发布、可回退；迁移期用 feature flag 并存，M5 收敛后删除已完成使命的运行时开关 |

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
| `verify_evidence_plan` | 删其 plan 适配器；确定性规则收敛为 `evaluate_termination` |
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
| ✅ | 抢救 preflight 资产 | symbol 抽取收紧（大写才认 ticker、`$AAPL` 语法、LLM 只校验歧义候选）与 provider 错误过滤已在 M2 迁入 `skills/finance/handler.py`，回归测试保留在 `tests/test_source_selector_finance_symbols.py` |
| ✅ | 丢弃分类器补丁 | 回退 LLM→keyword 交叉校验与分类提示词正反例；连带移除无调用方的 `allow_intelligent` 参数。知识转为数据存于 `skills/finance/evals/cases.jsonl` |
| ✅ | openspec 标注 | 22 份 spec 打 `Status` 行；`config.yaml` 的 `context` 与 `rules` 填入方向、分类与不变量（见 §0） |
| ☑ | 建立基线 | 用 [route_intent_dataset.csv](../dataset/route_intent_dataset.csv) 与 [final_answer_dataset.csv](../dataset/final_answer_dataset.csv) 跑现路径，结果落 `runtime/baseline/`，记录：路由准确率、答案质量、P50/P95 时延、每问 LLM 调用数与 token。运行入口 `python -m tests.baseline_runner`，度量定义与 M0 实测数字见 [docs/baseline.md](baseline.md) |
| ☑ | 装开关 | 引入 `engine.mode: plan \| loop`，默认 `plan`，行为零变化（解析后写入 `control.engine_mode`，`loop` 在 M1 才接通执行器） |

**退出判据**：基线数字入库且可复跑；flag 存在且默认路径行为与改动前一致。

**风险**：基线数据集覆盖不足，导致后续对比失去意义。route_intent 已在 M0 内从 30 条补齐至 55 条（覆盖 weather/finance/sports/transportation/location 等结构化领域与 calculator/time/chat 等系统当前缺失工具的路由），覆盖度风险已解除。

---

### M1 — Loop 通电

**目标**：让已经写好但在默认配置下是死代码的 loop 成为可切换的主路径，并拿到与 plan 路径的对比数据。

M1 开始前的现状：[react_loop_graph.py](../orchestrators/react_loop_graph.py) 是完整的 act/observe/evaluate 图，
带 judge、no-progress 检测、强制终止、trace。但唯一入口是 `_apply_postcheck` → `react_fallback`，
而 config 中 `postcheck.enabled=false`、`react_fallback.enabled=false`。

| 状态 | 工作项 | 说明 |
|---|---|---|
| ☑ | 提升为一等路径 | loop 从 postcheck fallback 提升为 `engine.mode=loop` 时的主执行器，不再依赖 postcheck 开关 |
| ☑ | 统一成功标准 | loop 的 `_derive_checklist` 与 `analyze_query` 是同一件事的两份实现；改为 loop 消费 `analyze_query` 的产物 |
| ☑ | 接通 ledger | loop 每轮的证据进 `EvidenceLedger`：`_observe` 累积 `evidence_records`，`_ingest_loop_evidence` 转成 `EvidenceItem`（带 `react_tool_*` provenance）走 `_prepare_query_plan` 建的 ledger，`retained_items()` 回填 `result["evidence_items"]`（对齐 I1） |
| ☑ | 接通 audit | loop 结果统一过 `_finalize_response`（audit/conversation 唯一收口），loop 的 `react_*` 步骤写入共享 tracer 供 audit 读取（对齐 I2） |
| ☑ | 对比跑分 | `python -m tests.baseline_runner --engine-mode {plan,loop}` 分模式落 `runtime/baseline/<milestone>/<mode>/`，`--compare PLAN_DIR LOOP_DIR` 按 qid 对齐产出 `comparison.json`（路由/覆盖/时延/token/调用数 delta + loop 迭代数 + route_gap 归类） |

**退出判据**：loop 路径在基线集上答案质量不劣于 plan 路径；**或**明确量化出差距来源（哪类 query、
差在检索还是组装）。时延与成本的退化幅度被记录并接受。

**风险（本路线最可能停摆的地方）**：多轮 loop 的时延与 token 成本相对单次 plan 必然上升。
若 P95 时延或每问成本超出可接受范围，先调 `max_iterations` 与 judge 频率，而不是回退架构方向。

**注**：此时工具面仍是旧的 4 个工具，router 藏在 `domain_api` 内部，还删不掉。这是预期的。

---

### M2 — Skill 骨架 + finance 先行

**目标**：确立 skill 契约，用最痛的一个领域验证，跑通「一个 skill 从头到尾」。

选 finance 打头阵的理由：痛点最集中（三处硬编码补丁都围绕它）、已有 finance preflight 回归测试，
M0 已抢救出它的 preflight 资产。

| 状态 | 工作项 | 说明 |
|---|---|---|
| ✅ | 定义契约 | `skills/contracts.py` 定义 `Skill`、`SkillManifest`、`PreflightResult`、`SkillRunResult`；manifest 启动时严格校验，handler 直接继承 `EvidenceSource` |
| ✅ | 可用性门 | `SkillRegistry` 按 Python provider / config key / disabled 配置构建工具面；不可用 skill 不注册给模型 |
| ✅ | finance skill | `skills/finance/handler.py` 承接确定性 symbol/时间窗 preflight、quote/history provider 链与唯一答案格式化器 |
| ✅ | 拆工具 | loop 暴露独立 `finance_market_data`；plan 与 legacy 入口也先匹配 registry；`domain_api` 不再声明或处理 finance |
| ✅ | 收编散落知识 | orchestrator 的 `finance_keywords` 与两处 LLM finance 增强已删除；finance 路由知识、provider 选择和格式化只在 skill 包中存在 |
| ✅ | 建 evals | `SKILL.md` 正反例由 `tests/test_agentic_loop_m2.py` 参数化读取 `evals/cases.jsonl`；契约、可用性门、拒绝反馈、provenance 均有回归测试 |

**退出判据**：finance 类 query 全部经由新 skill；`source_selector.py` 中 finance 相关分支删除；
evals 通过；基线集上 finance 子集不劣于 M1。

**M2 实测**：route_intent 的 finance 标注子集（4 条 finance_api + 1 条 finance/general 边界）在
plan 与 loop 两条路径均为 **5/5**；M0/M1 参考样本为 **1/1**，未回归。plan P50/P95 为
0.29s/86.26s，loop 为 71.40s/78.28s、平均 4.6 轮；loop 的成本明显偏高，按 M1 风险项带入
M4 停止判据合并。`final_answer_dataset.csv` 没有 finance 样本，因此这里不虚构答案质量分；
真实 Finnhub CLI 查询与确定性 quote/history 格式回归作为补充证据。可再生原始结果在
`runtime/baseline/m2/{plan,loop}/`（gitignored），摘要见 [baseline.md](baseline.md)。

**M2 决策**：
- **D1** 采用「reason 返回模型」：loop 收到结构化 `rejected` + reason，可依据用户显式输入修正；
  plan/legacy 在拒绝后确定性降级通用搜索。eval 中 finance 正例 preflight 拒绝 1/5（缺失公司实体的
  「这家公司最新财报如何」），拒绝是防猜测而非路由漏判。
- **D2** finance 暴露一个 `finance_market_data` 工具，由确定性 preflight 在 quote/history 间选择。
  finance 子集工具路由 5/5，没有证据支持拆成两个工具增加模型选择面。

---

### M3 — 其余 skill 迁移

**目标**：weather / location / transportation / sports 按 M2 确立的模式迁移，`source_selector.py` 解体。

顺序建议：weather（结构最简单）→ location → transportation → sports。每个 skill 独立可发布。

| 状态 | 工作项 | 说明 |
|---|---|---|
| ✅ | weather skill | 显式地点 preflight；Google Weather current/forecast 与 Air Quality 接口；缺地点拒绝后允许通用搜索 |
| ✅ | location skill | 显式参照地点 + 目标类型 preflight；Places Text Search (New)；`near me` 不猜当前位置 |
| ✅ | transportation skill | 显式起终点与交通方式 preflight；Google Routes；模糊机场/当前位置及班次问题回落通用搜索 |
| ✅ | sports skill | 明确队伍/赛事 preflight；TheSportsDB 赛程/结果接口；无结构化赛程时返回 `no_data` 并回落通用搜索 |
| ✅ | 独立工具面 | registry 向 loop 暴露 `weather_conditions`、`nearby_places`、`route_directions`、`sports_schedule`，plan/legacy 复用相同 handler |
| ✅ | router 删除 | `ReActDomainTool`、`DomainEvidenceSource` 与 selector API 全部删除，`search/source_selector.py` 文件删除 |
| ✅ | eval 与审计 | 每个 skill 有 `SKILL.md` + JSONL eval；provider、tool、plan step provenance 进入统一 `EvidenceItem`；provider 错误 URL 去 query/credential |

**退出判据**：`ReActDomainTool`、`select_sources`、`generate_domain_specific_query`、
`classify_domain` 全部删除；`search/source_selector.py` 文件消失。**router 在此里程碑真正死亡。**

**风险**：sports 与 location 的现有实现质量未经审视，迁移时可能发现是重写而非搬运。若某个领域
迁移成本远超其价值，允许直接降级为「不提供该 skill，落回通用搜索」——这是可接受的结局，
应显式记录而不是硬扛。

**M3 实测边界**：当前运行配置下 Weather current、Places、Routes 与 TheSportsDB 的明确队伍赛程
真实请求均成功；Beijing forecast 样本与部分 NBA/Wimbledon 样本无结构化数据。因此 weather/sports
在 provider 无数据时明确降级通用搜索，不把空响应伪装成结构化答案。后者是覆盖度/套餐的数据可用性
边界，不是通过测试替代的成功声明。

**M3 跑分**：weather/location/transportation/sports 的 route 子集在 plan 路径为 **16/16**；
其中 12 条结构化正例均通过确定性 preflight 选择独立 skill，4 条模糊参数/班次边界保持
`general_web`。plan step、RAG 与 legacy control 中的 `domain_api` 兼容命名也已删除，统一为
registry skill。P50/P95 为 9.13s/29.11s，外部 API 每问均值 1.50；详细口径见
[baseline.md](baseline.md)。

---

### M4 — 停止判据合并

**目标**：把两套互不知情的停止机制合并成一套。

M4 前 loop 的 LLM judge 与 `verify_evidence_plan` 各判各的。合并后形态：
**确定性 critic（覆盖度/来源等级/约束满足）+ LLM judge（语义充分性）+ 预算上界**，三者构成终止条件。

这是本项目相对通用 agent 框架的真正差异点——多数框架的停止判据只有「模型说完了」或「到轮数上限」。

| 状态 | 工作项 | 说明 |
|---|---|---|
| ✅ | 单一 critic | `evaluate_termination` 是 plan verification、postcheck 与 LangGraph loop 唯一的判定函数；三个入口只负责归一化事实与映射兼容状态 |
| ✅ | 确定性约束 | 同一规则集检查歧义、证据有无、来源等级、比较成员、官方目标、时间覆盖、回答覆盖、unsupported detail、工具错误、停滞和预算 |
| ✅ | judge 合并 | `termination.judge` 仅判断语义充分性；否定结果可以新增缺口，肯定结果不能清除确定性缺口、逆转 hard stop 或延长预算 |
| ✅ | 单一预算配置 | 顶层 `termination` 是 max iterations、judge 频率、停滞/错误阈值和 judge 模型的唯一配置块；plan 恢复预算作为当前执行事实输入 critic |
| ✅ | 单一循环 | legacy LangChain `AgentExecutor` 及构造器已删除；不支持的 engine 和缺失 LangGraph 依赖明确失败，不再切到第二套停止机制 |
| ✅ | 可审计终态 | 每个 verdict 输出 `action`、`deterministic_pass`、`hard_stop`、`failure_types`、`missing_constraints` 与 `rule_hits`，并进入 workflow trace/control 元数据 |

**退出判据**：系统中只存在一套停止逻辑；确定性 critic 可单独解释每次终止的原因并进入 trace。

**M4 决策**：
- **D1** judge 使用唯一的 `termination.judge` 配置；当前默认 provider/model 为
  `opencode-go/deepseek-v4-flash`。启用该 judge 会让 plan 候选终答进入统一 critic，即使兼容的
  `postcheck.enabled=false`；ReAct fallback 仍保持 opt-in。judge 不可用或输出不可解析时记录错误并
  降级为纯确定性规则，不能阻断响应。
- **D2** judge 在每个候选终答、每 `judge_interval` 轮，以及非歧义 hard terminal 前调用；歧义澄清、无效工具格式与过程叙述不浪费 judge 调用。
- **D3** 优先级固定为「hard stop / 预算 > 确定性缺口 > judge」。judge 可以否决规则通过，不能批准规则未通过；任何调用方都不能用后处理改写 critic 的终态。
- **D4** 删除 legacy `AgentExecutor`，而不是为“回滚方便”保留第二套停止逻辑。回滚只能回滚版本，不能在同一运行时保留两个裁判。

**M4 验证**：共享 critic、judge 正反优先级、预算 hard stop、错误降级、三个适配器静态收敛、
trace/control 与单一配置均有自动化回归；plan/loop 实测与完整验证见 [baseline.md](baseline.md)。

---

### M5 — 拆除 plan 机制

**目标**：删除静态预规划。

`build_query_plan` / `QueryPlan` / `PlanController` 删除；step 级预算迁移为 per-tool 预算；
`QueryExecutionTrace` 与 `EvidenceLedger` 保留并成为唯一的执行记录来源；`engine.mode` flag 移除。

| 状态 | 工作项 | 说明 |
|---|---|---|
| ✅ | 删除静态 plan | `build_query_plan`、`QueryPlan`、`PlanStep`、`PlanController`、plan verification 适配器及默认路径中的 decision/keyword chain 全部删除 |
| ✅ | 单一默认执行器 | 除闲聊、视觉和关键歧义澄清外，CLI/API 全部进入同一个 LangGraph `act → observe → evaluate` loop；`engine.mode` 与 postcheck/fallback 切换配置删除 |
| ✅ | 预算迁移 | `web_search`、`search_recovery`、`local_docs` 和每个 registry skill 独立执行 `max_calls_per_query`；实际使用量进入 `control.termination_policy.tool_budgets` |
| ✅ | 账本与留痕收口 | 每条 evidence 绑定 `originating_tool_call`；`QueryExecutionTrace` 每次实际调用只记一次，包括失败/零结果，随后记录 ledger 决策和最终 critic verdict |
| ✅ | 契约清理 | 删除三份退役 spec，改写相关能力契约；当前 19 份 spec 严格校验全部通过 |
| ✅ | 配置与跑分 | 示例和实时配置均无 engine/plan/postcheck 块；基线 runner 只运行单一执行器，M5 同样本烟测见 [baseline.md](baseline.md) |

**退出判据**：`utils/query_orchestration.py` 只剩分析（`analyze_query`）、账本（`EvidenceLedger`）、
判据（critic）、留痕（trace）四块；plan 相关的 OpenSpec 条目清理。

**前置条件**：M4 完成。plan 不能在 loop 的停止判据可信之前拆——否则失去唯一的兜底。

**M5 验证**：`python -m pytest -q` 为 **311 passed**；`openspec validate --all --strict` 为
**19 passed / 0 failed**；配置 JSON、compileall 和 `git diff --check` 均通过。真实默认 CLI 的当前天气
用例还发现并修复了一个迁移边界：`现在` 曾错误触发 8 年粒度补查，修复后只有显式多年/历史请求才
允许该恢复，实际工具 trace 也由逐 evidence 重复记录收敛为每次调用一条。

**M5 后置对齐审计**发现四处偏差，已在同一批改动中修复：

| 偏差 | 与哪条设计冲突 | 处置 |
|---|---|---|
| `--use-legacy` / `SmartSearchOrchestrator`（1288 行）是第二套运行时：自带 `DECISION_SYSTEM_PROMPT`/`KEYWORD_SYSTEM_PROMPT`，`evaluate_termination` 引用数 0（自带停止逻辑），`audit` 引用数 0 | §3 资产去留、M4-D4「不能在同一运行时保留两个裁判」、I2「任何执行路径下 audit 记录完整」 | 删除文件、CLI flag 与两个仅其使用的 LLM client builder；缺少 LangChain 依赖时 CLI 明确失败而不降级。理由与 M4-D4 处理 `AgentExecutor` 相同 |
| `utils/search_routing.py` 仍留 `build_decision_prompt` / `build_keyword_prompt` | §3「两份都随路由一起死」 | 随 legacy 一并删除 |
| 三个退役 spec 的目录空壳仍在 | M5「契约清理」 | 删除目录，`ls` 与 validator 现在都是 19 |
| `orchestrator_mode` 残留键仍被 main/server 读取并出现在 `/health` 响应 | I5「迁移期 flag 在 M5 收敛后必须删除」 | 从两份配置与全部读取点移除 |

**审计同时确认成立的**：router 与静态 plan 在运行时零残留（仅存于文档与断言其不存在的 guard 测试）；
`answer()` 恰好 4 个出口（视觉/闲聊/澄清/loop），loop 前的 `match_query` 只清歧义不执行 skill；
5 个 skill 的 `preflight` 无一处调用 LLM（I3）；`originating_tool_call`（I1）与 per-tool
`max_calls_per_query`（I4）均有实际机制；**运行时 Python 行数 27836 → 24782，净 −3054**，
「删除量大于新增量」的纪律成立。

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

#### 2026-07 已落地：ReAct 上下文压缩（属于「会话与 loop 状态融合」）

不变量 I4 在 token 维度上的缺口已补齐。`orchestrators/context_compaction.py` + `react_loop_graph.py` 的 `compact` 节点实现了分级压缩：tier-1 把超出保留窗口的工具观察折叠成 ledger 头部指针（确定性、零 LLM），不足以回落时 tier-2 用一次廉价摘要把中间区间收敛成结构化轨迹与答案草稿（不含工具原文）。跨轮不再按条数删消息，`_compute_message_removals` 已退役，续跑直接继承 checkpoint 中已压缩的序列；`recall_evidence` 工具按 `[En]` 回灌被折叠的全文。`orchestration.context_compaction.enabled` 默认 true，节点在图中但关闭态永不被路由选中。

**基线结论（`runtime/baseline/context-compaction/`）**：在 `dataset/final_answer_dataset.csv` 上，峰值上下文占比 p95 ≈ 0.07，比 0.75 阈值低一个数量级——压缩路径在真实语料 + 当前模型窗口（128k）下从未被触发，是一个针对长会话 / 小窗口模型的兜底安全网，而非日常路径。因此 10.4 退出判据里「token 峰值下降」在本工作负载上不可观测（无路径可压），「答案质量不劣于变更前」由代码层保证成立：`enabled` 仅在 `_can_compact`（`enabled` 且 `ratio ≥ threshold`）一处起作用，阈值以下的查询无论开关状态都字节一致。压缩路径的行为正确性由 `tests/test_context_compaction.py` 的 15 个用例、一次人为小窗口的 stress 跑分，以及一份开启态全量跑分（`enabled/`，20 行，`compactions` 全 0）覆盖。详见 `openspec/changes/archive/2026-08-01-add-context-compaction/design.md` 的基线复核与场景覆盖矩阵。

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
