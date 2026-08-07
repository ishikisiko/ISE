# 架构改进方案：把"门控"建立在可验证的信号上

> 关联文档：`docs/failure-analysis-tavily-firecrawl-brightdata-comparison.md`（症状级分析）
>
> 本文档是对该故障分析的**架构级**改进方案，取代其中 §7 的 P0/P1 表面修补。
>
> **实施状态**：✅ Phase 1 已落地（M-RC3 advisory coverage + M-RC2 degraded
> synthesis），详见文末「附录 A·Phase 1 实施记录」。

---

## 0. 核心洞察（一句话）

> 系统用**同一套"单调、不可放宽"的硬门控**同时约束两类截然不同的信号：
> **可验证的 grounding 信号**（证据是否存在、是否权威、claim 是否可追溯到 ledger）和
> **解析器的猜测**（`comparison_members`、`entities`）。
> 三类故障（RC1/RC2/RC3）都是这一个错位的症状。
>
> **改进 = 把二者拆开：可验证的才硬门控、才单调；解析的只建议、可纠正、可被证据推翻。**

---

## 1. 现有架构的隐性设计原则及其错位

### 1.1 已存在、且本意正确的设计：单调性（M4 契约）

`evaluate_termination` 的 docstring（`utils/query_orchestration.py:819`）明确：

> "The optional judge may add a semantic gap or veto a deterministic pass. **It can never clear a deterministic gap, override a hard stop, or extend a budget.**"

`merge_optional_analysis`（`:624`）同样贯彻：

> "Merge optional LLM suggestions **without relaxing deterministic safeguards**." —— 且实现是**纯追加**：只能 `+entities/+claims/+ambiguities`，**绝不触碰 `comparison_members`，绝不删除任何东西**。

这条单调性的**目的是反幻觉**——防止 LLM 把自己从 grounding 要求里"说"出去。本意完全正确。

### 1.2 错位：单调性被无差别地应用到了解析器输出上

`comparison:{member}` 这种约束的来源是 `_extract_comparison_members`——一个**正则**（`:412`）。它产出的成员名，一旦被"信任"，就和"引用了不存在的来源""缺权威来源"享受**同等硬度的阻塞**（都进 blocking `missing`，都受单调契约保护，judge 都不能清除）。

于是：

- 解析器把指令句"注意不要…实际区别"误判为成员 → 这条 `comparison:注意不要…` 成为**永远不可满足、且不可被任何机制清除**的约束 → `not missing` 恒假 → 永久卡死。
- 这不是"某个正则 bug"，而是**架构层面的类型混淆**：把"猜测"当成了"事实"来保护。

### 1.3 两个本可救场、却被错位设计废掉的机制

- **`evidence_sufficiency` 已分三档却不参与决策**：`insufficient`（retained=0）/ `partial`（有 structural missing）/ `sufficient`（`:990-994`）。它被计算、被塞进**每一个** `TerminationDecision`，但**没有任何分支读它**——真正的门是裸 `not missing`。即系统**已经有了分级信号，却没接线**。
- **`merge_optional_analysis` 是断线的**：生产路径（`langchain_orchestrator.py:137`）调 `analyze_query` 时**根本不传 LLM 候选**，`analysis_source` 永远是纯 `deterministic`；且即便接上，它也**只能加不能改**，无法纠正被污染的 `comparison_members`。

### 1.4 正确范式其实已在仓里：pricing 路径

`_pricing_requirements` / `_pricing_fact_sets` / `pricing_ready` / `pricing_recovery`（`react_loop_graph.py:1565/1573/1846/1850`）门控在**证据内容**（fact sets 是否齐备）上，而非解析 token；并有显式的降级阶梯（`_next_pricing_source`）和"齐备就停"的 `pricing_ready`。**这才是 comparison 路径应当长成的样子。**

---

## 2. 目标架构：三条原则

| | 原则 | 含义 |
|---|---|---|
| **P1** | **门控建立在可验证信号上** | blocking 门只用：证据是否存在、是否满足 authority 策略、claim 是否通过引用核验。解析出的 members/entities 只用于**播种检索**和**建议覆盖**，不进 blocking 门。 |
| **P2** | **单调性只保护可验证信号** | 引用核验、authority、显式日期的 temporal 仍单调、不可被 LLM 放宽。但解析输出（members/entities）**可被纠正**：可被 LLM reconcile 替换，也可被已检索证据推翻。 |
| **P3** | **永远存在降级阶梯** | `sufficient`→全答；`partial` 且有权威→**带 caveat 答**；`insufficient`（零证据）→继续或硬停。**绝不"有证据却返回空话"。** |

这三条共同把 `edf2250` 的反幻觉目标（`3ab634f`/`7e87b35` 的 cited evidence ledger）完整保留，只是把"门控的对象"从"解析猜测"换成"可验证 grounding"。

---

## 3. 机制设计（映射到三个 RC，给 before/after 契约 + 代码位置）

### 3.1 M-RC3（最高杠杆、最低风险）：拆分 `comparison_coverage` 的两种语义

**问题**：当前 `comparison_coverage` 策略同时承担两件事，且都进 blocking `missing`：

```python
# utils/query_orchestration.py:878
if "comparison_coverage" in policies:
    for member in context.comparison_members:
        if member.casefold() not in covered_entities:
            add_gap(f"comparison:{member}", ...)        # ← 完备性，硬阻塞
    if context.answer:
        for member in context.comparison_members:
            if not _comparison_member_mentioned(context.answer, member):
                add_gap(f"answer_comparison:{member}", ...)  # ← 完备性，硬阻塞
```

**改法**：把它拆成两个语义不同的东西。

| 语义 | 归属 | 硬度 | 实现 |
|---|---|---|---|
| **Grounding**：草稿里每个 claim 必须可追溯到 ledger 一条记录 | 已有的 `_check_draft_citations`→`check_citations`（`react_loop_graph.py:2127`） | **硬、单调、不变** | 反幻觉保证不动 |
| **Completeness**：每个 comparison 话题是否都有证据/是否都被回答 | 新增 `coverage_gaps`（advisory） | **软、建议性** | 不进 blocking `missing` |

**决策影响**：grounding 通过 + authority 通过 ⇒ 即使 `coverage_gaps` 非空，也允许 RETURN；`coverage_gaps` 只在"预算未到顶"时作为"继续尝试补"的**动力**，预算到顶则转入带 caveat 综合。

> **不削弱反幻觉**：claim 仍必须可追溯；只是不再因为"成员名子串没命中证据池"而否决一个有据答案。第 7 轮模型给出答案被驳回的场景，正是这条要修的。

### 3.2 M-RC2：激活已有的 `evidence_sufficiency` 分级 + 降级综合

**问题**：决策基于裸 `not missing`；`evidence_sufficiency` 三档白算。

**改法**：把决策主输入从 `not missing` 切到 `evidence_sufficiency`（接线那个已有信号）：

```
sufficient              → RETURN（全答）
partial + auth≥1 + 到顶  → RETURN_WITH_CAVEATS（带缺口声明综合）   ← 新路径
partial + 预算未到顶      → CONTINUE（尝试补 coverage_gaps）
insufficient(retained=0)→ CONTINUE / EXHAUSTED（绝不空答）
```

- `RETURN_WITH_CAVEATS` 可复用 `RETURN` 动作 + 新增 `caveats: [...]` 字段 + `reason="best_effort"`，最小侵入。
- 复用 §1.4 的 pricing 范式：**门控在证据内容，不在解析 token**。
- `exhausted` 路径（`:2456`）增加前置：`retained > 0` 时**强制**走降级综合，不再返回空话。

### 3.3 M-RC1：解析器降级为"一等候选" + 接通并放开 LLM 纠错

**问题**：解析输出被当圣旨；唯一的纠错钩子既断线、又被硬性限制为 additive-only。

**改法**：

1. **给 parser 输出打 provenance/confidence**：`comparison_members` 每项标注来源（`regex_member` / `explicit_list` / `llm`）与置信度。`regex_member` 且"含 CJK 长串 / 无拉丁 token / 以指令动词（注意/不要/请/…）开头"→ 标**低置信**。
2. **接通已断线的 LLM 分析**，但用**新的 `reconcile_analysis(deterministic, llm)`** 取代 additive-only 的 merge：
   - LLM **可以替换**低置信的 `comparison_members`/`entities`；
   - LLM **不能**移除**可验证**约束（显式日期的 temporal、authority）、不能重开被禁 search。
3. **触发条件 noise-gated**：仅当 parse 被标低置信才跑 reconcile —— 与 `edf2250` 降延迟目标兼容（只在不确定时付一次 LLM 调用，且可用低 reasoning 档）。

> 这是对 M4 契约的**精化**而非废除：单调性的保护范围从"所有 deterministic 输出"收窄到"**可验证**的 deterministic 输出"。契约精神（防 LLM 逃脱 grounding）完整保留。

---

## 4. 分阶段迁移（配置开关、风险递增、每阶段独立可回滚）

| 阶段 | 内容 | 风险 | 开关 | 对本查询 |
|---|---|---|---|---|
| **Phase 1** | M-RC3（coverage 拆分、advisory）+ M-RC2（激活 sufficiency、降级综合、exhausted 兜底） | **最低**（纯放宽，grounding 仍由 citation audit 硬保） | `termination.coverage_mode = advisory`、`termination.degraded_synthesis = true` | **立即出答案**（带 caveat） |
| **Phase 2** | M-RC1（provenance + 接通 reconcile，noise-gated） | 中（新增 LLM 调用、改分析语义） | `analysis.llm_reconcile = true` | 成员干净、caveat 消失、质量更好 |
| **Phase 3（可选）** | 把 comparison 路径整体改造成 pricing 那样的 evidence-content-gated 范式（topic 覆盖判定基于证据内容而非 token 子串） | 中高 | `termination.coverage_semantics = evidence_content` | 根除"成员名噪声"整类问题 |

> **Phase 1 单独即可让本次查询从"空话"变成"带 caveat 的正确答案"，且不碰反幻觉保证。** Phase 2/3 是把"还能更好"做扎实。建议先合 Phase 1 验证，再推进。

---

## 5. 明确不做（防止退回表面修补）

- ❌ **把"扩 `_clean_entity_fragment` 动词表 / 加断句"当核心方案**——那是规则军备竞赛，下一种问法就复发。可做 Phase 1 的**输入卫生**（顺手降低 noise-gated 触发率），但绝不是根治。
- ❌ **给 `comparison_members` 加特例白名单/黑名单**修本查询。
- ❌ **在 exhausted 前再叠一层"接近上限就综合"的窄 escape hatch**——`edf2250` 的 `late_loop_no_answer` 已是这种思路且证明不够（它依赖 `not missing`）。要用**分级 sufficiency** 系统性解决，不要继续堆条件。
- ❌ **为了修本查询而放宽 citation audit / authority 策略**——那会破坏反幻觉保证。本方案的每条放宽都**只针对解析输出**，绝不碰 grounding/authority 的单调性。

---

## 6. 对现有测试的影响（必须处理，否则会假绿）

`tests/test_react_loop_graph.py:1258 / 1328` 等用例断言"应返回 `迭代次数用尽`"。逐条复核语义：

- **若场景是 `retained == 0`（真无证据）** → 保持硬停（走 `insufficient`→EXHAUSTED），断言不变。
- **若场景是"有证据但某 member 未覆盖"** → 期望值改为 `RETURN_WITH_CAVEATS`（带缺口声明），原断言需更新。

新增测试（建议）：

1. `test_coverage_advisory_does_not_block_grounded_answer`：有权威证据、coverage_gaps 非空 → 不再 EXHAUSTED，走 RETURN_WITH_CAVEATS。
2. `test_degraded_synthesis_on_exhausted_with_evidence`：迭代到顶但 retained>0 → 不返回空话。
3. `test_parser_noise_flags_low_confidence_member`：指令句成员被标低置信。
4. `test_reconcile_replaces_low_confidence_members`（Phase 2）：LLM 能替换低置信 member，但不能移除显式 temporal/authority。
5. 回归：`test_react_loop_graph` 中真无证据场景仍 EXHAUSTED（防过度放宽）。

---

## 7. 权衡与开放问题

- **延迟**：Phase 2 的 reconcile 增一次 LLM 调用。缓解：noise-gated（仅低置信触发）+ 低 reasoning 档（复用 `edf2250` 的 per-call `reasoning` 控制）。可量化"触发率"避免普遍变慢。
- **caveat 的可信度**：synthesis prompt 须加约束——"未覆盖话题须**显式声明证据不足**，不得编造"。这恰好强化而非削弱 grounding。
- **覆盖判定改 evidence-content 的脆弱性**（Phase 3）：从 `brightdata…有什么` 抽 `brightdata` 的"topic 提取器"本身可能退化成新的脆弱规则。优先依赖 Phase 2 reconcile 产出的**干净 members**，而非再写一套启发式。
- **M4 契约改写的范围**：要在 docstring 与测试里把"单调保护对象"从"deterministic 输出"明确改成"**可验证**的 deterministic 输出"，避免后人误解为整体放宽。

---

## 8. 一页验证计划

1. **回归基线**：本查询 + 变体（`A和B有什么区别？不要只看官方宣传`、`对比X与Y，重点看实际差异`）→ 断言非 EXHAUSTED、答案覆盖真实成员、未覆盖项进 caveat。
2. **反幻觉不回归**：构造"模型编造未检索 claim"场景 → 断言 citation audit 仍硬阻断（证明 grounding 未被削弱）。
3. **真无证据仍硬停**：构造零证据场景 → 断言仍 EXHAUSTED（证明未过度放宽）。
4. **延迟**：Phase 2 测 reconcile 触发率与端到端时延，确认不普遍劣化。
5. **单测**：见 §6。

---

## 9. 关键代码位置索引（落地参照）

| 关注点 | 位置 |
|---|---|
| 单调契约 docstring | `utils/query_orchestration.py:819 evaluate_termination` |
| comparison 完备性硬阻塞（待拆分） | `utils/query_orchestration.py:878` / `:884` |
| 已有但未接线的 sufficiency 分级 | `utils/query_orchestration.py:990-994` |
| 决策主分支 `not missing`/RETURN | `utils/query_orchestration.py:1144` |
| exhausted 文案/硬停 | `orchestrators/react_loop_graph.py:2456` |
| 引用核验（grounding，保持硬） | `orchestrators/react_loop_graph.py:2127 _check_draft_citations` |
| late_loop escape（不要再堆） | `orchestrators/react_loop_graph.py:1870` |
| 断线的 LLM 分析入口 | `langchain/langchain_orchestrator.py:137`（未传 candidate） |
| additive-only merge（待改可纠正） | `utils/query_orchestration.py:624 merge_optional_analysis` |
| 成员提取正则（parser，待降级） | `utils/query_orchestration.py:412 _extract_comparison_members` |
| 范式参照：pricing evidence-content 门控 | `orchestrators/react_loop_graph.py:1573 _pricing_fact_sets` / `:1846 pricing_ready` |

---

## 附录 A · Phase 1 实施记录（已完成）

**改动范围**（commit 待提交；6 文件，约 +250 行）：

- `utils/query_orchestration.py`
  - `DEFAULT_TERMINATION_CONFIG` 新增 `coverage_mode="blocking"`、`degraded_synthesis=False`（默认向后兼容）。
  - `normalize_termination_config` 支持非整型键（`coverage_mode` 取 `blocking|advisory`，`degraded_synthesis` 取 bool，非法值回退默认）。
  - `TerminationContext` 新增 `coverage_mode`（输入）与 `coverage_gaps`（输出）。
  - `evaluate_termination`：`coverage_mode=="advisory"` 时，`comparison:{member}` / `answer_comparison:{member}` 路由进 `context.coverage_gaps`，**不进 blocking `missing`**；grounding（引用核验）/authority/temporal 的单调性完全不变。
- `orchestrators/react_loop_graph.py`
  - `LoopVerdict` + state schema 新增 `coverage_gaps` / `degraded_synthesis` / `degraded_caveats`。
  - `_evaluate`：终端态（EXHAUSTED/STAGNATED/RETURN_INSUFFICIENT）+ `evidence_sufficiency ∈ {partial,sufficient}` + 非定价 + 仍有综合次数 → `degraded_synthesis_force`，置 `force_synthesis`、`reason="degraded_synthesis"`，并把 advisory 缺口写入 `degraded_caveats`。
  - `_synthesize`：命中 `degraded_synthesis` 且有保留证据时，调用新方法 `_generate_grounded_synthesis`（不绑工具的 LLM 调用，按已核证据生成带 `[En]` 引用的答案，并把未覆盖项作为显式 caveat 写进指令），失败回退 `_best_effort_answer`。
  - `_termination_context` 透传 `coverage_mode`。
- `config.json` / `config.example.json`：`termination` 块开启 `coverage_mode="advisory"`、`degraded_synthesis=true`。
- 测试：`tests/test_query_orchestration.py`（advisory 路由 + 配置归一化，2 例）、`tests/test_react_loop_graph.py`（有证据→降级出答案 / 无证据→不降级，2 例）。

**验证**：

- 全量 pytest：425 passed（+4 新增），唯一 1 例失败 `test_model_api_config::test_example_placeholders…` 为**改动前既有失败**（在 `edf2250` 干净树上同样失败，与本改动无关）。
- 复跑原始故障查询：原先返回「迭代次数用尽，未能获得完整答案。」；现返回带 10+ 条 `[En]` 引用、逐维度对比、且显式标注「官方宣传 vs 实际」与「证据不足需自行验证」的完整答案。控制数据显示 `degraded_synthesis` 判定触发，`retained=5 / authoritative_entries=4`。

**未做（留给后续阶段）**：

- **Phase 2（M-RC1）**：parser 降级为一等候选 + 接通并放开 LLM 纠错（修 `_extract_comparison_members` 仍会把指令句误判为成员的根因）。本次复跑的 `comparison_members` 仍被污染，只是 advisory 让它不再致命。
- 降级答案当前最终常以 `evidence_insufficient` 收尾（引用核验未全过）但保留了完整草稿；让降级答案更干净地 RETURN（引用核验对降级答案更宽容，或模型引用更准）可作为 Phase 1.5 打磨项。

---

## 附录 B · Phase 2 实施记录（已完成）

**目标**：M-RC1——把确定性解析器从"不可纠错的地面真值"降级为"一等候选"。清除 Phase 1 之后仍残留的 `comparison_members` 污染（指令句、被截断的长串）。

**改动范围**（约 +180 行）：

- `utils/query_orchestration.py`（纯逻辑，无 LangChain 依赖）：
  - `_member_is_unambiguous_noise` / `_member_is_noisy`：确定性噪声判定（指令动词开头 / 纯 CJK 长串无品牌 token / 含疑问词 / 混合长串截断）。
  - `sanitize_comparison_members`：始终开启，丢弃"明确噪声"成员（如「注意不要…」）。
  - `analysis_needs_reconcile`：sanitize 后是否仍有可疑成员→是否需要 LLM。
  - `reconcile_prompt` / `apply_reconcile` / `prepare_analysis`：noise-gated 的 LLM 纠错。`apply_reconcile` **只允许替换** `comparison_members`/`entities`，**绝不触碰** constraints / claim_classes / search 标志（可验证信号的单调性不变）；非法/空输出则保持 sanitize 后结果。
- `langchain/langchain_orchestrator.py`：
  - `_normalize_orchestration_config` 新增 `reconcile_analysis`（默认 true）。
  - `_begin_turn` 在 `analyze_query` 后调 `prepare_analysis`。
  - `_invoke_reconcile_llm`：用便宜的 `termination_judge_llm`（flash + reasoning=disabled）做单次纠错调用；任何异常回退到 sanitize 结果，绝不打断会话。
- `config.json` / `config.example.json`：`orchestration.reconcile_analysis = true`。
- 测试：`tests/test_query_orchestration.py` +6（sanitize 丢指令句、reconcile 替换且保约束、非法输出忽略、noise-gated 触发、无 LLM 仅 sanitize、干净查询零调用）。

**设计要点**：

- **双层**：确定性 sanitize（零延迟、始终开）+ LLM reconcile（仅 noise-gated 触发，干净查询零开销，兼容 `edf2250` 降延迟目标）。
- **精化而非废除 M4 契约**：单调性保护范围从"所有 deterministic 输出"收窄到"**可验证**的 deterministic 输出"（temporal/authority/search）；解析猜测（members/entities）可被纠正。
- **失败安全**：reconcile 任何环节失败 → 回退到 sanitize 后的确定性分析；Phase 1 的 advisory coverage + degraded synthesis 仍兜底。

**验证**：

- 全量 pytest：431 passed（Phase 1 的 425 + Phase 2 的 6）；唯一失败 `test_model_api_config::test_example_placeholders…` 仍为改动前既有失败。
- 复跑原始故障查询：`analysis_source = deterministic+reconciled_llm`，`comparison_members` 由污染的 4 项清洗为 `["Tavily Extract","Firecrawl Scrape","brightdata"]`，三项**全部**被证据覆盖；答案从"参数级实际差异"切入，并正确指出"三者并非同一层级的网页内容 API"、对 Bright Data 证据不足处如实标注。

**剩余打磨项（Phase 1.5，可选）**：降级/正常综合答案最终常以 `evidence_insufficient` 收尾（引用核验未全过）但保留完整草稿；可让综合答案在引用核验上更宽容或模型引用更准，以更干净地 RETURN。
