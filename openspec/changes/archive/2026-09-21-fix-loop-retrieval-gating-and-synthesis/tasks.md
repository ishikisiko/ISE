# Tasks

> 每组末尾的 **退出判据** 是该组可独立验证的条件。真实运行验证（第 6 组）在离线验收全部通过后由用户指定模型再执行。

## 1. QD-04 前缀幂等（最小、独立）

- [x] 1.1 限定前缀与终态模板提为模块常量；新增 `_strip_qualification` / `_is_stub_answer`
- [x] 1.2 `_best_effort_answer` 先剥离再加前缀；模板文本不加前缀
- [x] 1.3 测试：两次调用结果与一次相同；中英文前缀各覆盖；`authority_unverified` 前缀不与预算前缀叠加

**退出判据**：`_best_effort_answer(_best_effort_answer(x, r), r) == _best_effort_answer(x, r)`。

## 2. QD-03 强制合成总是接地

- [x] 2.1 `_synthesize`：台账有已保留证据即走 `_generate_grounded_synthesis`；无证据分支与接地失败分支改用 `_last_candidate_text`
- [x] 2.2 `_collect_evaluation_facts` 把终态模板视为空草稿
- [x] 2.3 测试：`late_loop_no_answer` 触发的综合产出含 `[E1]` 的接地答案而非模板；judge 失败时模板不能被判 `constraints_satisfied`

**退出判据**：有证据时 `synthesize` 节点的 detail 为"已基于已核证据生成……"，最终答案不等于任何终态模板。

## 3. QD-02 不调工具的轮次分开计数 + 候选答案保留

- [x] 3.1 `DEFAULT_TERMINATION_CONFIG` 增 `tool_free_threshold=3`；`TerminationContext` 增 `tool_free_streak` / `tool_free_threshold`
- [x] 3.2 loop state 增 `tool_free_streak`（初始 / 续跑 / 澄清续跑 / observe 归零 / synthesize 归零）；`_collect_evaluation_facts` 分别维护两个计数
- [x] 3.3 `evaluate_termination`：无效动作分支与 final_proposed 分支用 `tool_free_streak >= tool_free_threshold` 判停滞；`authority_provisional_fallback` 取两个计数的最大值
- [x] 3.4 `_rejection_message`：台账为空或 `search_required` 时 closing 改为点名 `web_search` 的检索指令
- [x] 3.5 `_build_verdict` 终止分支：草稿为空时回退 `_last_candidate_text`
- [x] 3.6 测试：直答被拒 → 下一轮调 `web_search` → 成功；连续三轮不调工具 → `stagnated` 且答案含第一轮候选与一次前缀；调整现有停滞用例的阈值或预期
- [x] 3.7 `config.example.json` 登记 `tool_free_threshold`

**退出判据**：脚本化模型"直答 → 叙述 → 搜索 → 答"在默认阈值下以 `succeeded` 结束；"直答 ×3"以 `stagnated` 结束且 `answer` 包含直答正文。

## 4. QD-01 `force_search` 生效

- [x] 4.1 `ReactAgentOrchestrator.answer` → `_answer_with_langgraph(force_search=…)` → `ReactLoopGraphRunner(force_search=…)`；搜索禁用或无搜索工具时归零
- [x] 4.2 `_observe` 统计 `search_calls`（`web_search` / `search_recovery`），进 loop state 与 `run()` 结果
- [x] 4.3 `_termination_context.search_required`；critic 在 final_proposed 且 `search_required` 时记缺口 `search_required`；`_RETRIEVAL_FIXABLE_FAILURES` 收录
- [x] 4.4 系统提示在 `force_search` 时追加成功标准；`control.request_parameters.force_search` 与 `control.loop_search_calls` 回显
- [x] 4.5 测试：`force_search=True` 且模型直答 → 第一轮 `final_answer_rejected` 且 rule_hits 含 `search_required`；模型随后搜索 → 通过；`force_search=False` 行为不变；`allow_search=False` 时不生效

**退出判据**：`tests/quality/test_param_forwarding.py` 风格的透传断言：入口 `force_search=True` 时 `control.request_parameters.force_search is True` 且 `loop_search_calls >= 1`（脚本化模型直答后被迫搜索）。

## 5. QD-05 指代歧义误报

- [x] 5.1 `analyze_query`：英文 `this` / `that` 仅独立代词位置计数；歧义条件改为 `not entities or (has_comparison and len(comparison_members) < 2)`
- [x] 5.2 `_build_clarification_response` 按歧义类型措辞（中英文）
- [x] 5.3 测试：open019 原句不再判歧义；"Compare it with the other one"、"它的价格是多少"、"帮我对比一下" 仍判歧义；"GLM-5.2 的价格和它的上下文长度" 不判
- [x] 5.4 `python -m tests.quality.analysis_eval` 离线回归，`critical_ambiguity` 的 P/R/F1 不低于 0918 报告值（2026-09-21：P 0.800→0.889、R 0.727 不变、F1 0.762→0.800；误报 qa047 消除，qa056 仍误报，漏报 qa022/043/045 不变）

**退出判据**：`query_analysis_gold.csv` 上 gold=1 的题全部仍判 1，qa047 翻为 0。

## 6. 汇总与真实运行（待用户指定模型）

- [x] 6.1 全量 `python -m pytest -q tests --ignore=tests/quality` 无回归；`tests/quality` 单测无回归（2026-09-21：全树 742 passed, 1 xfailed；调整 3 条既有用例的停滞预期，见实施记录）
- [x] 6.2 报告 §9 登记本 change 路径；`docs/baseline.md` 相关行注明"待重测"（2026-09-21 完成，另在 quality_evaluation_plan Q1-04 括注）
- [x] 6.3 **[真实运行]** 用用户指定模型重跑 final025、open019、final020、open002 与搜索采集 `--force-search`（minimal 组），确认零搜索题数为 0、四题终态与答案符合预期（2026-09-21，模型 opencode-go/deepseek-v4.1-flash，主模型与循环内裁判同款，产物 `runtime/quality/20260921-qd-fix-verify/`：final025 第 1 轮直答被拒后第 2 轮即搜索，终答含 100 °C 且前缀一次，终态 evidence_insufficient（来源皆 unknown 层级）；open019 不再澄清，10 轮 2 次搜索 succeeded；final020 强制合成给出 11 月 19 日并引用官方站，前缀一次无模板；open002 强制合成 5.6k 字综述，无模板，终态 evidence_insufficient；minimal 采集零搜索 4/12 → 1/12，剩下 1 题“文档里这个参数的默认值”是澄清短路，0918 亦如此。零搜索为 0 的判据未完全达成，原因是该题按设计不进循环）

## 实施记录（2026-09-21）

- 代码：`utils/query_orchestration.py`（`tool_free_threshold`、`TerminationContext.tool_free_streak / search_required`、critic 停滞分支、`analyze_query` 指代规则）；`orchestrators/react_loop_graph.py`（前缀常量与 `_strip_qualification` / `_is_stub_answer`、`_last_candidate_text`、`_synthesize` 分支、双计数器、`force_search`、`search_calls`）；`orchestrators/react_agent_orchestrator.py`（透传与 `control.request_parameters.force_search` / `control.loop_search_calls`）；`langchain/langchain_orchestrator.py`（澄清文案）；`config.example.json` / `config.json` 登记 `termination.tool_free_threshold: 3`（与代码默认相同，无行为差异）。
- 设计偏差：D6 额外加了中文规则——`这个` / `那个` 紧跟在拉丁字母 token 之后（"Kubernetes 这个名字"）不算未解析指代；`it` 的句内先行词仍不处理。
- 既有用例调整：`test_shim_unknown_tool_treated_as_text` 显式设 `tool_free_threshold=2`；`test_invalid_json_tool_arguments_stagnate_without_leaking_markup` 的停滞轮次 2→3；`test_blocked_compaction_returns_through_act_before_synthesis` 原本断言无证据无候选时的模板被判 `succeeded`，这正是 QD-03 的症状，改为断言 `evidence_insufficient` 与模板文本。
- 6.3 真实运行已于同日完成，见上。
