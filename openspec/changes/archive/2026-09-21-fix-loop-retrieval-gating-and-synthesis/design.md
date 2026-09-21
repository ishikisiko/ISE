## Context

五个缺陷都落在 `act → observe → evaluate → synthesize` 单图内部与它的入口分析器上。相关代码位置：

| 缺陷 | 位置 |
|---|---|
| QD-01 | `ReactAgentOrchestrator.answer` 只接收 `force_search`；`_answer_with_langgraph` / `ReactLoopGraphRunner.__init__` 无对应参数 |
| QD-02 | `_collect_evaluation_facts` 的 `no_progress_streak`、`evaluate_termination` 的 `no_progress` 规则、`_rejection_message` 的 closing、`_build_verdict` 终止分支的 `_best_effort_answer(facts.draft, …)` |
| QD-03 | `_synthesize` 的三分支 `elif state.get("degraded_synthesis") and …` |
| QD-04 | `_best_effort_answer` 无条件拼前缀 |
| QD-05 | `analyze_query` 的 `AMBIGUOUS_REFERENCE_CUES` 匹配与 `has_pronoun and (not entities or len(comparison_members) < 2)`；`_build_clarification_response` |

## Goals / Non-Goals

**Goals**
- 请求级 `force_search` 成为 critic 可裁决的确定性约束，而不是提示词建议。
- 不调工具的轮次与检索无进展的轮次分开计数；前者给模型明确的检索指令，后者维持原停滞语义。
- 任何终止路径都优先返回模型已经产出的候选答案（带一次限定前缀），模板只在确实没有候选时出现。
- 强制合成在有证据时一定生成接地答案。
- 指代歧义判定不再把关系代词、限定词与已点名实体当成未解析指代。

**Non-Goals**
- 不改 judge 协议、不改证据台账、不改 provider 链。
- 不把 `force_search` 变成配置项：它是请求级参数（评测采集与 API 传入），默认 false。
- 不用 LLM 判定歧义；`analyze_query` 仍是确定性解析（I3）。

## Decisions

### D1 `search_required` 作为 critic 缺口，而不是 evaluate 节点里的旁路

`ReactLoopGraphRunner` 把 `force_search and search_calls == 0 and 工具面含 web_search/search_recovery` 归一化成 `TerminationContext.search_required`；`evaluate_termination` 在 final_proposed 时把它记为缺口 `search_required`（failure `search_required`，rule `search_required`）。这样拒收仍走 `final_answer_rejected` 一条路，reason / rule_hits / trace 全部沿用，不新增第二套继续/终止规则（spec "规则化迭代内评估"）。有界性由 D2 的 `tool_free_threshold` 保证：模型三轮都不搜，循环按停滞收口，不会无限拒收。

**备选**：在 `_act` 里检测到零搜索就直接替模型注入一次 `web_search` 调用。**否决**：模型选工具是 M0–M5 收敛的架构方向（"工具面由 skill 派生，模型选工具"），替模型调工具等于复活静态 plan。

### D2 两个计数器：`no_progress_streak`（检索无进展）与 `tool_free_streak`（不调工具）

现状把"调了工具但没新证据"和"根本没调工具"混在一个计数器里，阈值 2 意味着"直答被拒 + 一轮叙述"就停滞。拆开后：

- `no_progress_streak`：只在发起了工具调用的轮次累加，新证据归零；阈值仍是 `no_progress_threshold`。
- `tool_free_streak`：不调工具的轮次（直答被拒、过程叙述、无效工具请求）累加，任何工具调用轮归零；阈值 `tool_free_threshold`（默认 3，进 `DEFAULT_TERMINATION_CONFIG`）。达到阈值时 critic 判 `stagnated`（规则 `no_progress`，detail 区分"tool-free"），与 spec 的停滞语义一致。
- `authority_provisional_fallback` 原本依赖 `no_progress_streak`（拒收草稿也计数），改为取两者的最大值，保持"反复尝试仍未验证"的触发条件不变。

默认 3 而不是 2：拒收文案第一次点名 `web_search`（第 2 轮），若模型第 2 轮只是叙述"我将搜索"，narration guard 再推一次（第 3 轮），第 3 轮仍不调工具才收口。

### D3 候选答案回退：`_last_candidate_text`

终止分支、`_synthesize` 的无证据分支和接地综合失败分支都改用 `_last_candidate_text(messages)`：倒序扫 `AIMessage`，跳过带 tool_calls 的、正文为空的、工具协议文本与终态模板，返回剥掉限定前缀后的正文。`_act` 把过程叙述替换成空 `AIMessage` 的行为不变，所以第一轮的候选仍在历史里可以被找回。

### D4 `_synthesize` 的分支顺序

```
pricing fact sets 齐 → 确定性价格答案
pricing 需求但不齐    → 价格证据不足说明
台账有已保留证据     → 接地综合（degraded 标志只决定 caveat 行）
否则                → 候选答案 + 一次限定前缀
```

同时 `_collect_evaluation_facts` 把模板文本视为空草稿：critic 因 `empty_answer` 不会把模板判成 `constraints_satisfied`（open002 的情况），综合尝试用尽后终止原因仍是 `evidence_insufficient` / `exhausted`。

### D5 幂等前缀

限定前缀（中英文 × 预算不足 / 权威未验证）与终态模板提到模块级常量；`_best_effort_answer` 先 `_strip_qualification`，再按 reason 加一次。模板本身不加前缀。

### D6 指代线索的最小修正

- 英文 `this` / `that`：只有后面不紧跟字母单词时才算（`what is that?` 算；`a system that deploys` / `this model` 不算）。`it` 保持整词匹配。中文线索不变。
- 歧义条件改为 `has_pronoun and (not entities or (has_comparison and len(comparison_members) < 2))`：非对比问题里已有实体即视为可解析；对比问题仍要求两个成员。
- `_build_clarification_response` 按 `comparison_members_unresolved` / `unresolved_entity_reference` 两类各给中英文文案。

用 `dataset/query_analysis_gold.csv`（66 题，含 `gold_critical_ambiguity`）离线回归：修正前 qa047（"GLM-5.2 的价格和它的上下文长度"，gold 0）误判，修正后应通过；其余 gold=1 的题不得翻转。

## Risks / Trade-offs

- `tool_free_threshold=3` 让顽固直答的模型多消耗一轮 act（含 judge）；上界仍由 `max_iterations` 保证。
- 强制合成改为总是接地综合会在 `late_loop_no_answer` 路径多一次 LLM 调用（原来是零成本拼模板）；这正是缺陷本身——原路径的产出无法交付。
- `it` 仍可能误报（"before it is flagged"），本 change 不处理句内先行词；留待 Jev 接入分析环节时再评估。
