# 问题分析：对比类查询"迭代用尽"失败

> 被测查询：`Tavily Extract,Firecrawl Scrape和brightdata对应的用来获取网页内容的API有什么区别？注意不要仅仅停留于"官方宣传"要抓到实际区别`
>
> 失败会话：`cli-12d6afc88a8b`（commit `edf2250`，deepseek-v4-flash，确定性分析路径）

---

## 1. 结论速览（TL;DR）

系统**没有检索失败，而是被一个不可能满足的约束永远卡死**。

1. **检索是成功的**：24 条 search hits、32 条证据记录，最终保留 5 条（含 3 条权威/一方来源），命中了 Firecrawl 官方对比页、Firecrawl 博客、Bright Data 官方对比页，覆盖了三个产品中的两个真实成员（Tavily Extract、Firecrawl Scrape）。
2. **分析层把指令句拆成了对比成员**：确定性分析器 `_extract_comparison_members` 把用户指令"注意不要仅仅停留于官方宣传要抓到实际区别"整句，以及被截断的"brightdata对应的用来获取网页内容的API有什么"，当成两个"对比成员"塞进了 `comparison_members`。
3. **约束门要求每个成员都有证据覆盖**，这两个伪成员**永远不可能被覆盖**，于是 `constraints_missing` 永远非空。
4. **三道安全网全部失效**：强制综合（forced synthesis）依赖"约束已满足"，永不成立；LLM Judge 在搜索阶段没有草稿可评、形同虚设；迭代上限后直接判 `exhausted`，把已收集的好证据全部丢弃，只返回"迭代次数用尽，未能获得完整答案"。

**净结果**：耗时 232 秒、8 轮迭代、收集到足以回答的证据，却输出一句空话。

---

## 2. 现象与复现

```bash
conda run -n env1 python main.py \
  'Tavily Extract,Firecrawl Scrape和brightdata对应的用来获取网页内容的API有什么区别？注意不要仅仅停留于“官方宣传”要抓到实际区别'
```

终端输出：

```
迭代次数用尽，未能获得完整答案。
[conversation_id] cli-12d6afc88a8b
```

端到端耗时 `response_times.total_ms = 232290`（约 232 秒）。`final_executor = agentic_loop`，`loop_forced_synthesis = false`。

---

## 3. 运行时遥测（来自 `checkpoints/conversations.sqlite`）

### 3.1 查询分析结果（`control.query_analysis`，`analysis_source = deterministic`）

| 字段 | 值 | 是否正确 |
|---|---|---|
| `intent_shape` | `comparison` | ✅ |
| `claim_classes` | `["comparison"]` | ✅ |
| `existence_query` | `true` | ❌ 误判（被"有什么"触发，见 §4.4） |
| `constraints.comparison_required` | `true` | ✅ |
| `comparison_members` | 见下表 | ❌ **4 个里有 2 个是垃圾** |

`comparison_members` 实际内容：

| # | 成员字符串 | 来源 | 问题 |
|---|---|---|---|
| 1 | `注意不要仅仅停留于"官方宣传"要抓到实际区别` | cue 之后的 **tail 整句** | ❌ 用户指令被当成实体 |
| 2 | `Tavily Extract` | cue 之前的前缀按 `,` 切分 | ✅ |
| 3 | `Firecrawl Scrape` | 同上，按 `和` 切分 | ✅ |
| 4 | `brightdata对应的用来获取网页内容的API有什么` | 前缀残段 | ❌ 截断错位 |

`entities` 进一步把上述 4 个再加 4 个品牌 token（`Tavily`/`Extract`/`Firecrawl`/`Scrape`）。

### 3.2 循环判定（`control.loop_verdicts`，共 8 轮）

| 轮次 | new_evidence | deterministic_pass | judge_used | reason |
|---|---|---|---|---|
| 1–6 | true | false | false | `continue` |
| 7 | **false** | false | false | `final_answer_rejected`（模型尝试给答案被驳回） |
| 8 | true | false | false | `exhausted`（`hard_stop=true`） |

- 全程 `judge_used = false`。
- 第 7 轮模型确实产出了草稿答案（`final_proposed=true`），但被确定性约束门以"成员未覆盖"驳回。
- 第 8 轮 `constraints_missing` 仍挂着：`comparison:注意不要…`、`comparison:Firecrawl Scrape`、`comparison:brightdata…有什么`、`comparison`（+ 一条 `answer`）。

### 3.3 证据覆盖（`control.evidence_coverage`）

```
retained: 5   rejected: 19   merged: 8   authoritative_entries: 3
comparison_members_covered: ["Tavily Extract", "Firecrawl Scrape"]
```

保留的 3 条权威/一方来源（`evidence_coverage.decisions`）：

1. `firecrawl.dev/alternatives/firecrawl-vs-tavily`（official，覆盖 Tavily+Firecrawl）
2. `firecrawl.dev/blog/tavily-alternatives`（official，覆盖 Tavily+Firecrawl）
3. `brightdata.com/blog/comparison/bright-data-vs-firecrawl`（first_party）

**即：检索完全跑通，且命中了三方各自的官方对比资料——证据足以产出答案。**

### 3.4 约束与预算（`control.termination_policy`）

```
max_iterations = 8, max_synthesis_attempts = 2, judge_interval = 2, judge_enabled = true
web_search used 6/6, fetch_url used 2/3
```

---

## 4. 根因分析（代码级）

整条故障链分三层：**分析层**（成员提取）→ **约束门**（per-member gap）→ **安全网**（强制综合 / Judge）。每层单独看都"合理"，串联起来却把好证据饿死。

### 4.1 分析层：`_extract_comparison_members`（`utils/query_orchestration.py:412`）

```python
def _extract_comparison_members(query: str) -> List[str]:
    # ① 找最早的对比 cue（COMPARISON_CUES 含 "区别"/"对比"/…）
    for cue in COMPARISON_CUES:
        index = lowered.find(cue.casefold())
        ...
    tail = query[cue_index + cue_length :]      # cue 之后的文本
    prefix = query[:cue_index]                   # cue 之前的文本
    # ② 先按分隔符切 tail
    fragments = re.split(separator, tail, ...)
    # ③ 若 tail 切不出 ≥2 个有效片段，再切 prefix
    if len([f for f in fragments if _clean_entity_fragment(f)]) < 2:
        fragments.extend(re.split(separator, prefix, ...))
    # ④ 清洗每个片段，≥2 字符即收录
    for fragment in fragments:
        cleaned = _clean_entity_fragment(fragment)
        if cleaned and len(cleaned) >= 2:
            members.append(cleaned)
    ...
```

对本查询：

- **最早的 cue 是 "区别"**，它出现在"有什么**区别**"里（注意末尾"实际区别"里还有第二个"区别"，但代码取最早索引）。
- 于是：
  - `tail = "？注意不要仅仅停留于"官方宣传"要抓到实际区别"` ← 整句用户指令
  - `prefix = "Tavily Extract,Firecrawl Scrape和brightdata对应的用来获取网页内容的API有什么"`
- ②③：tail 只切出 1 个有效片段（指令句），< 2，于是把 prefix 也切进来。
- ④ 清洗后得到 4 个成员。

**为什么指令句能通过清洗** `_clean_entity_fragment`（`utils/query_orchestration.py:358`）？

```python
def _clean_entity_fragment(value: str) -> str:
    text = value.strip(" ...？？！!")            # 只去首尾标点
    text = re.sub(
        r"^(?:请|帮我|请问|告诉我|将|把|关于|对|和|与|and|compare|comparison)\s*",
        "", text, ...)                            # 只剥有限的前导动词
    ...
    return _bounded_text(text, 80)                # 截到 80 字符即收
```

- 前导动词剥离列表里有 `请/帮我/请问/告诉/关于/对/和/与…`，**但没有 `注意`/`不要`**。
- 指令句"注意不要…要抓到实际区别"以"注意"开头，未被剥离；长度达标；于是被当作一个成员。

**为什么"brightdata…有什么"被截断**：

- prefix 被 `separator`（`,，、/；和与`）切成 `["Tavily Extract", "Firecrawl Scrape", "brightdata对应的用来获取网页内容的API有什么"]`。
- 第三个片段在"有什么"处戛然而止（因为"区别"是 cue、被切走了），清洗后仍是一长串错位的中文。它既不是干净的 `brightdata`，也匹配不到 `Bright Data` 的真实证据文本。

> **诊断**：本函数面向"A 和 B 的区别 / compare A vs B"这类**纯列举**句式设计；一旦查询带有**附加指令句**（本例：`…区别？注意不要…要抓到实际区别`），cue 选择和句读处理就会把指令句卷入成员列表。

### 4.2 约束门：per-member gap（`utils/query_orchestration.py:878`）

```python
if "comparison_coverage" in policies:
    for member in context.comparison_members:
        if member.casefold() not in covered_entities:
            add_gap(
                f"comparison:{member}",
                "comparison_coverage_missing",
                "comparison_coverage",
                f"Evidence does not cover comparison member: {member}.",
            )
```

- `context.comparison_members` 来自 `self.analysis.comparison_members`（含 2 个伪成员）。
- `covered_entities` 是"在证据文本里能子串匹配到的成员"（`react_loop_graph.py:2202`：`member.casefold() in evidence_text.casefold()`，或经 `_comparison_member_mentioned` 归一化匹配）。
- 伪成员"注意不要…实际区别""brightdata…有什么"**在任何网页内容里都不可能出现** → 永远落入 `missing`。
- 后果：`missing` 列表**永远非空** → `deterministic_pass` 恒为 false。

### 4.3 安全网为什么全部失效

#### 4.3.1 强制综合 `late_loop_no_answer`（`react_loop_graph.py:1870`）

```python
constraints_satisfied = (not missing) and bool(state.get("had_successful_observation"))
late_loop_no_answer = bool(
    not pricing_recovery and not final_proposed
    and constraints_satisfied          # ← 关键前提：missing 必须为空
    and ... and iteration >= max(3, max_iterations - 2)
)
```

这正是 `edf2250` 为了"约束已满足但模型仍反复搜索"而加的兜底。**但它的前提 `not missing` 因伪成员而永不成立**——这条安全网只救得了"checklist 为空的存在性查询"，救不了"checklist 被污染的对比查询"。

#### 4.3.2 LLM Judge（`react_loop_graph.py:1824`）

```python
should_judge = (
    self.judge_llm is not None
    and bool(draft.strip())              # ← 必须先有草稿答案
    ...
    and (final_proposed or preliminary.hard_stop
         or iteration % judge_interval == 0)
)
```

- Judge 要评的是**草稿答案**，因此要求 `draft` 非空（即模型已 `final_proposed`）。
- 第 1–6 轮模型在调工具（search/fetch），没有草稿 → 不评。
- 第 7 轮模型给出草稿，但此时确定性路径已先把它判为 `final_answer_rejected`（成员未覆盖）。
- 结果：8 轮全程 `judge_used = false`。Judge 在"搜索期"无法介入纠偏。

#### 4.3.3 迭代上限后的硬停（`react_loop_graph.py:2456`）

```python
"exhausted": "迭代次数用尽，未能获得完整答案。",
```

- `max_iterations = 8` 用尽后直接硬停，**把已收集的 5 条保留证据、3 条权威来源全部丢弃**，只回一句空话。
- 没有任何"带 caveat 兜底综合"的降级路径。

### 4.4 附带问题：`existence_query` 误判

- `ENUMERATION_CUES`（`utils/query_orchestration.py:33`）含 `"有什么"`，本查询的"…API有**什么**区别"被命中，导致 `existence_query = true`。
- 本次副作用较轻（仅让 `_derive_checklist` 跳过了 `time_constraint`，而本查询本就不需要时效）。但它说明**短表意词 cue 容易过匹配**，会污染后续策略。

---

## 5. 问题分层（bug 分类）

| 层级 | 问题 | 严重度 | 是否本次根因 |
|---|---|---|---|
| 分析 | 指令句被当成对比成员；前缀切分截断成员 | **高** | ✅ 直接根因 |
| 分析 | `_clean_entity_fragment` 前导动词表不含 `注意/不要` 等指令词 | 高 | ✅ 放大根因 |
| 约束门 | per-member gap 用**精确子串/归一化匹配**，对成员名噪声零容忍 | **高** | ✅ 把分析错误放大为永久卡死 |
| 安全网 | `late_loop_no_answer` 依赖 `not missing`，无法应对"被污染的 checklist" | **高** | ✅ 兜底失效 |
| 安全网 | Judge 在搜索期（无草稿）完全缺席，无法纠偏 | 中 | ⚠️ 间接 |
| 安全网 | exhausted 路径丢弃全部证据、无降级综合 | **高** | ✅ 决定性坏结果 |
| 分析 | `existence_query` 被"有什么"误触发 | 低 | ⚠️ 噪声 |

可归纳为**两个相互独立但叠加致命的缺陷**：

- **缺陷 A（分析噪声）**：对比成员提取对附加指令句不鲁棒。
- **缺陷 B（循环无降级）**：任一不可满足的约束都会使整个对比查询永久无法综合，且耗尽后丢弃证据。

即便只修好其中一个，本次查询也能出答案；但两者都修才能根治。

---

## 6. 影响评估

- **直接影响**：任何"对比 + 带附加说明/指令"的中文查询（用户很常见的问法，如"…有什么区别？不要只看官方宣传""…对比，重点关注实际体验"）都会触发同样模式，**确定性复现地失败**。
- **隐性浪费**：每次失败消耗 ~230s + 6 次 web_search + 2 次 fetch + 多轮 LLM；用户只得到一句空话，无法获知系统其实已找到答案。
- **回归面**：本次查询在 `edf2250` 的"per-scenario reasoning + agentic-loop latency"改进之后仍失败，说明**延迟/推理调优没有触及这条约束链的健壮性盲区**。

---

## 7. 修复方案（按优先级）

### P0-A 分析层：让成员提取对指令句鲁棒（`utils/query_orchestration.py`）

1. **切分前先按句读断句**：以 `。？！;；` 为界，**只取包含 cue 的那一句话**做成员提取，丢弃其后的附加指令句。
   - 本例：cue "区别"在第一句"…有什么区别？"内，第二句"注意不要…实际区别"整句被排除。
2. **扩展前导指令词剥离**：`_clean_entity_fragment` 的正则加入 `注意|不要|请勿|务必|重点|实际|真正` 等，并在清洗后做一次"是否包含动词/否定句式"的启发式丢弃。
3. **成员质量门槛**：成员若**不含任何 ENTITY_TOKEN_RE（拉丁品牌/产品 token）**且为长中文串（如 > 12 汉字），视为指令噪声丢弃。
   - 本例可保 `Tavily Extract`、`Firecrawl Scrape`，并把 `brightdata…有什么` 收敛为 `brightdata`（取片段内首个拉丁 token 作为代表）。

### P0-B 约束门 + 安全网：引入"降级综合"（`react_loop_graph_graph.py` + `utils/query_orchestration.py`）

1. **放宽 force-synthesis 触发**：迭代末段（≥ `max_iterations - 2`）若满足"**已覆盖的真实成员 ≥ ⌈N/2⌉ 且 authoritative_count ≥ 1**"，即使个别成员未覆盖也强制进入综合，**把未覆盖项写进 caveat**，而非耗尽。
2. **exhausted 路径兜底综合**：硬停前若 `retained_count > 0`，走一次"尽力而为"综合（明确标注"以下成员未找到证据：…"），不再返回空话。
3. **成员覆盖匹配更宽容**：覆盖判定时对成员做**实体词提取归一化**（如 `brightdata对应的用来获取网页内容的API有什么` → `brightdata`），与证据里的 `Bright Data` 做大小写/空格无关匹配，避免因成员名噪声导致覆盖漏判。

### P1 Judge 介入窗口前移（`react_loop_graph.py`）

- 允许 Judge 在"**搜索期、连续 N 轮 new_evidence=true 但 constraints_missing 长期不变**"时，基于"证据池摘要"而非草稿进行一次**航向纠偏**（判定：继续搜无望 → 建议 force_synthesis）。当前 Judge 绑死在草稿上，搜索期完全失语。

### P1 存在性 cue 收敛（`utils/query_orchestration.py`）

- `ENUMERATION_CUES` 中的 `"有什么"`/`"有哪些"` 改为**带边界**的匹配（如要求后接非"区别/不同/差异"），避免把"有什么区别"误判为枚举查询。

---

## 8. 验证计划

1. **回归用例**：本次查询 + 若干变体（`A和B有什么区别？不要只看官方宣传`、`对比X与Y，重点看实际差异`），断言不再 `exhausted`，且答案覆盖真实成员、caveat 标注未覆盖项。
2. **单测**（建议加到 `tests/test_query_orchestration.py`）：
   - `_extract_comparison_members("…有什么区别？注意不要…")` 不含指令句成员。
   - `_clean_entity_fragment("注意不要…要抓到实际区别")` 返回空或被过滤。
3. **循环级测试**（`tests/test_react_loop_graph.py`）：构造"1 个不可满足成员 + 充足证据"的场景，断言走降级综合而非 `exhausted`。
4. **现状基线**：当前 `tests/test_react_loop_graph.py:1258 / 1328` 断言某些场景**应**返回"迭代次数用尽"——需复核这些用例的语义，确保 P0-B 的兜底不会破坏"确实无证据"时的正确硬停（区分"无证据"与"有证据但有不可满足约束"）。

---

## 9. 附：关键代码位置索引

| 关注点 | 位置 |
|---|---|
| 成员提取 | `utils/query_orchestration.py:412 _extract_comparison_members` |
| 片段清洗 | `utils/query_orchestration.py:358 _clean_entity_fragment` |
| 成员覆盖匹配 | `utils/query_orchestration.py:383 _comparison_member_mentioned` |
| per-member gap | `utils/query_orchestration.py:878` |
| existence cue | `utils/query_orchestration.py:33 ENUMERATION_CUES` / `:571` |
| checklist 派生 | `orchestrators/react_loop_graph.py:389 _derive_checklist` |
| 终止上下文 | `orchestrators/react_loop_graph.py:2177 _termination_context` |
| 强制综合条件 | `orchestrators/react_loop_graph.py:1870 late_loop_no_answer` |
| Judge 触发条件 | `orchestrators/react_loop_graph.py:1824 should_judge` |
| exhausted 文案/硬停 | `orchestrators/react_loop_graph.py:2456` |
