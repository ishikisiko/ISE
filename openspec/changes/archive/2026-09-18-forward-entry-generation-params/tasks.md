# Tasks

- [x] 1.1 `ReactAgentOrchestrator.answer()` 透传 `max_tokens` / `temperature` 到 loop；act / synthesize / degraded synthesis 的 `invoke` 使用它们（QD-20260909-01）
- [x] 1.2 `ReActSearchTool` / `ReActSearchRecoveryTool` 使用请求级 `num_search_results`（QD-20260909-02）
- [x] 1.3 `control.request_parameters` 回显生效值
- [x] 1.4 删除 `tests/quality/test_param_forwarding.py` 的 xfail 标记，全绿（2026-09-18：6 passed，含新增的 `control.request_parameters` 回显断言）；`python -m tests.quality.validity --max-queries 5` 的 `param_forwarding_pass = 1.0` 见 D0 冒烟登记

退出判据：入口传入的三项参数与线上请求体 / provider 请求条数逐项相等；默认 guided 路径在不传参时行为不变。

2026-09-18 实施记录：`ReactLoopGraphRunner(generation_params=...)` 在 act（原生工具与 shim 两条路径）与降级综合的 `invoke` 上覆盖 `max_tokens` / `temperature`，judge 与压缩摘要保持角色配置；`ReActSearchTool` / `ReActSearchRecoveryTool` 新增 `set_request_options(num_search_results, per_source_limit)`，由 `ReactAgentOrchestrator._answer_with_langgraph` 每次请求绑定，未绑定时保持原默认 5；`control.request_parameters` 回显四项生效值。`search_recovery` 内嵌生成仍用其自身的 1200 / 0.2（摘要用途，不随入口放大），与提案"内嵌生成同样使用入口参数"一句的差异在此记录。
