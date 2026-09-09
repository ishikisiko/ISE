# Tasks

- [ ] 1.1 `ReactAgentOrchestrator.answer()` 透传 `max_tokens` / `temperature` 到 loop；act / synthesize / degraded synthesis 的 `invoke` 使用它们（QD-20260909-01）
- [ ] 1.2 `ReActSearchTool` / `ReActSearchRecoveryTool` 使用请求级 `num_search_results`（QD-20260909-02）
- [ ] 1.3 `control.request_parameters` 回显生效值
- [ ] 1.4 删除 `tests/quality/test_param_forwarding.py` 的 xfail 标记，全绿；`python -m tests.quality.validity --max-queries 5` 的 `param_forwarding_pass = 1.0`

退出判据：入口传入的三项参数与线上请求体 / provider 请求条数逐项相等；默认 guided 路径在不传参时行为不变。
