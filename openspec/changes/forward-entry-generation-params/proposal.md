# forward-entry-generation-params

状态：缺陷登记（由质量评测 Q0-01 参数透传断言发现，2026-09-09）。缺陷编号 **QD-20260909-01**（`max_tokens` / `temperature`）与 **QD-20260909-02**（`num_search_results`）。本 change 只登记与界定，不在评测计划内顺手修改产品（[quality_evaluation_plan.md](../../../docs/quality_evaluation_plan.md) §0.1）。

## Why

`LangChainOrchestrator.answer()` 接收 `max_tokens`、`temperature`、`num_search_results` 并转交 `ReactAgentOrchestrator.answer()`，但后者调用 `_answer_with_langgraph()` 时不再传递生成参数；`ReactLoopGraphRunner` 的 act / synthesize 调用 `self.llm.invoke(messages, reasoning=...)` 只带 reasoning，所以线上请求体使用的是模型对象默认值（`UniversalChatModel.max_tokens=5000`、`temperature=0.7`）。`ReActSearchTool._run` 固定 `RetrievalOptions(num_results=5)`，入口 `num_search_results` 只影响 ledger 的 `apply_limits`，不影响 provider 请求条数。

运行审计 [runtime_audit.md](../../../docs/reports/autonomy_evaluation_20260908/runtime_audit.md) 已定性描述该缺口；`tests/quality/test_param_forwarding.py` 现在把它固定为严格 xfail 断言（禁网、真实 builder、捕获真实请求体），修复后断言会由 xfail 转为通过并要求删除 xfail 标记。

路线图归属：M6 能力扩张期的可观测性/契约修补，不改变架构方向。不变量：I1–I3 不涉及；I4（token 上界可配置可观测）正是本缺陷的违背点；I5 修复应可独立发布。

## What Changes（待实施）

- `ReactAgentOrchestrator.answer()` 把 `max_tokens` / `temperature` 传入 `_answer_with_langgraph()`，`ReactLoopGraphRunner` 在 act / synthesize / degraded synthesis 调用时以 `invoke(..., max_tokens=..., temperature=...)` 覆盖模型默认值；judge 与压缩摘要保持各自角色配置。
- `ReActSearchTool` 与 `ReActSearchRecoveryTool` 接收请求级 `num_search_results`（经 `set_request_options` 或等价钩子），provider 请求条数与入口一致；`search_recovery` 内嵌生成同样使用入口参数。
- `control.request_parameters` 回显实际生效的 `max_tokens` / `temperature` / `num_search_results`，供 D0 `param_forwarding_pass` 在运行期对账。

## Impact

- `orchestrators/react_agent_orchestrator.py`、`orchestrators/react_loop_graph.py`、`langchain/langchain_react_tools.py`。
- 验收：`tests/quality/test_param_forwarding.py` 去掉 xfail 后全绿；`python -m tests.quality.validity` 的 `param_forwarding_pass = 1.0`。
