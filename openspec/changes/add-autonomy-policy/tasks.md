> 每组末尾的 **退出判据** 是该组可独立发布的验证条件。P0 = 组 1–5，P1 = 组 6，P2 = 组 7，P3 = 组 8。
> 全程 `autonomy.mode` 默认 `guided`，任何一组完成后默认路径行为都必须与本 change 前一致。

## 1. 前置重构：拆分 `_evaluate`

- [x] 1.1 为 `_evaluate` 现有行为补齐特征化测试：覆盖 `pricing_recovery`、`force_synthesis`（`late_loop_no_answer`）、`degraded_synthesis_force`、`compact_next`、`invalid_tool_request`、`invalid_final_response`、`final_proposed` 被驳回、各 hard terminal 共 8 条路径，断言 `reason`、`action`、`next_action`、`termination_reason` 四个字段
- [x] 1.2 把 `_evaluate` 拆为四段：`_collect_evaluation_facts`（归一化事实）、`_run_critic`（调 `evaluate_termination` 与 judge）、`_decide_next_action`（四个互斥布尔量的裁定）、`_build_verdict`（组装 `LoopVerdict` 与 state update）
- [x] 1.3 确认拆分后 1.1 的全部用例逐字段通过，且 `python -m pytest -q` 无回归

**退出判据**：`_evaluate` 主体 ≤ 60 行；8 条路径的特征化测试全绿；本组不引入任何行为差异。

## 2. `AutonomyPolicy` 骨架与解析

- [x] 2.1 新建 `orchestrators/autonomy_policy.py`：定义不可变 `AutonomyPolicy`（`checklist_injection`、`critic_verdict`、`citation_check`、`judge_enabled`、`narration_guard`、`forced_synthesis`、`clarification_owner`、`budgets`）与 `guided` / `autonomous` 两个预设常量；不提供关闭 preflight 的取值
- [x] 2.2 实现 `resolve_autonomy_policy(config, request_mode)`：优先级为请求 > 配置 > 默认 `guided`；`autonomy.profiles.<mode>` 局部覆盖预设；未知模式名抛出明确错误
- [x] 2.3 预算解析：缺失/非正/不可解析的取值回退到预设内置默认并记录降级说明；`guided` 的预算来源仍为现有 `termination` 块
- [x] 2.4 `config.example.json` 与 `config.json` 增加 `autonomy` 块（`mode: "guided"` + 两个 profile 的预算），`utils/config_validation.py` 覆盖新块
- [x] 2.5 `_prepare_answer_context` 增加 `autonomy` 字段（`/api/answer` 与 `/api/answer/stream` 共用），非法值按 `PayloadError` 拒绝；`main.py` 增加 `--autonomy`
- [x] 2.6 前端增加模式切换控件，并在响应中回显实际生效的自主度
- [x] 2.7 单元测试：优先级、局部覆盖、未知模式、无效预算回退、缺失 `autonomy` 块时等价于 `guided`、payload 非法值被拒

**退出判据**：解析函数的 6 类用例全绿；配置中无 `autonomy` 块时解析结果与 `guided` 预设逐字段相等；请求覆盖不影响并发请求与后续默认值。

## 3. 策略接入 loop 的五个维度

- [x] 3.1 `ReactLoopGraphRunner.__init__` 接收 policy；`ReactAgentOrchestrator` 与 `langchain_orchestrator` 只透传不解析、不推断
- [x] 3.2 checklist：`_format_success_criteria` 按 `checklist_injection` 切换措辞（`enforce` 保持现状，`hint` 改为参考信息且不表述为终止条件）；checklist 在两种取值下均照常派生并参与每轮覆盖度计算
- [x] 3.3 critic：`_decide_next_action` 在 `critic_verdict=advisory` 时不驳回候选终答、不发返工反馈；缺口写入 `LoopVerdict.advisory_gaps`
- [x] 3.4 引用校验：`citation_check=advisory` 时 `_check_draft_citations` 照常执行，失败项进 `advisory_gaps` 而不阻断终答
- [x] 3.5 advisory 缺口去重注入：已注入集合进 loop state（随 checkpoint 保留），同一缺口单次执行最多注入一次；注入文本有界
- [x] 3.6 narration guard：`_process_narration_reason` 仅在 `narration_guard=on` 时生效；关闭时纯文本响应走普通评估路径
- [x] 3.7 judge：`judge_enabled=false` 时任何轮次均不发起 judge 调用，verdict 标明 judge 未参与
- [x] 3.8 强制合成：`forced_synthesis=off` 时 `late_loop_no_answer`、`degraded_synthesis_force` 与 `_pricing_fetch` 均不触发；预算耗尽仍保留已有证据并标注执行预算不足
- [x] 3.9 预算：迭代上界与各 wrapper 的 `max_calls_per_query` 改从 policy 的预算组读取；`context_compaction` 参数纳入预算组
- [x] 3.10 系统提示加入剩余迭代数与各工具剩余额度，且与实际约束一致
- [x] 3.11 验证 preflight 在两个预设下均为 binding：`autonomous` 下非法参数被拒且无 provider 调用发生
- [x] 3.12 `guided` 预算等价性验证：逐字段断言 `guided` 预算组解析结果等于现有 `termination` 与各 wrapper `max_calls_per_query` 的取值（含并入预算组的 `context_compaction` 参数），任一项不等即失败

**退出判据**：`guided` 下全量 `python -m pytest -q` 无回归；`autonomous` 下 8 个维度各有针对性用例；存在一条断言"policy 中不存在关闭 preflight 的取值"的守卫测试；3.12 的等价性断言全绿。

## 4. 可观测性与留痕

- [x] 4.1 `LoopVerdict` 增加 `autonomy_mode` 与 `advisory_gaps`（含是否已注入标记），进入 trace 与 checkpoint
- [x] 4.2 `control.autonomy` 暴露生效模式与来源（请求/配置/默认）；`control` 增加 advisory 缺口计数
- [x] 4.3 audit 记录在两个自主度下同等完整；新增断言两模式下 verdict 均写入 trace 与 audit 的回归测试
- [x] 4.4 `search-response-control` 相关字段的有界性测试：不含规则原文、提示词与模型推理
- [x] 4.5 旧 checkpoint 前向兼容：所有读取 verdict 新增字段的位置使用带默认值的取值方式；补一条用例，以不含 `autonomy_mode` / `advisory_gaps` 的历史 verdict 续跑会话且不抛错

**退出判据**：两模式各跑一条真实查询，trace 与 audit 中 verdict 数量、工具调用条数一致；control 字段有界性用例全绿；历史 verdict 续跑用例通过。

## 5. 请求级取消

- [x] 5.1 服务端有界 `run_id → threading.Event` 注册表：请求结束即移除，容量上界 + LRU 淘汰
- [x] 5.2 `/api/answer/stream` 在流开始时发出携带 `run_id` 的事件
- [x] 5.3 新增 `POST /api/answer/<run_id>/cancel`；该端点不得获取 `_conversation_lock`
- [x] 5.4 loop 在每个节点入口检查取消 event：不中断进行中的工具/模型调用，等其自然结束并登记结果后停止推进，且不再发起新调用
- [x] 5.5 取消收口：有证据时返回基于已保留证据的部分结果并显式标注被用户取消；无证据时返回取消说明
- [x] 5.6 `cancelled` 作为独立终止状态进入 control 与 trace，与预算/证据类终止原因区分；记录取消发生时的迭代序号
- [x] 5.7 取消路径写出完整 audit 与执行轨迹；会话状态保持一致且无无响应的 tool_call
- [x] 5.8 前端取消按钮与取消态呈现
- [x] 5.9 测试：执行中取消、已结束后取消、无证据取消、取消后会话可继续使用、注册表不泄漏

**退出判据**：5.9 的 5 类用例全绿；一次真实 `autonomous` 长查询中途取消后 audit 记录完整且会话可继续。

## 6. 规划文本载体（P1）

- [x] 6.1 `narration_guard=off` 时 `_act` 保留同时携带文本与 `tool_calls` 的响应中的文本，进入消息序列
- [x] 6.2 该文本不作为候选终答，该轮不判定为模型提议终答
- [x] 6.3 trace 以有界形式记录该轮存在规划文本，不含隐藏推理或完整提示词
- [x] 6.4 `narration_guard=on` 时该场景处理方式与本 change 前一致

**退出判据**：共存场景在两种 guard 取值下各有用例；`guided` 行为无差异。

## 7. 模型自主澄清（P2）

- [x] 7.1 新增 `ReActAskUserTool`：无 provider 调用、不产生 `EvidenceItem`、不参与证据增量、独立且很小的调用预算；`clarification_owner=system` 时不注册进工具面
- [x] 7.2 澄清进入"等待用户输入"非终态：保留 loop 状态待续跑，control 中与其他终态明确区分
- [x] 7.3 SSE 增加等待输入事件并携带澄清问题，不表示为失败或完成
- [x] 7.4 `clarification_owner=model` 时 `langchain_orchestrator.answer` 的 loop 前确定性澄清短路不生效；两种归属互斥
- [x] 7.5 会话语义：澄清轮不计为独立已完成问答；续跑时 `iteration` 与工具预算延续而非重置；已注入 advisory 缺口集合保留
- [x] 7.6 跨轮自主度变更即重置轨迹：会话记录最近生效自主度，不同则不续跑 checkpoint 并在 control 中说明
- [x] 7.7 前端澄清往返呈现
- [x] 7.8 测试：发起澄清、答复续跑、显式重置放弃、澄清预算耗尽、澄清不绕开迭代上界、切模式重置

**退出判据**：7.8 的 6 类用例全绿；`clarification_owner=system` 下澄清行为与本 change 前逐字段一致。

## 8. 评测与对比（P3）

- [x] 8.1 新增 `dataset/open_task_dataset.csv`（~20 条开放式/多步任务样本，含评分维度列）
- [x] 8.2 `tests/baseline_runner.py` 支持 `--autonomy {guided,autonomous}`，结果落 `runtime/baseline/<milestone>/<autonomy>/`，每条记录携带生效自主度
- [x] 8.3 `--compare` 按样本对齐产出两模式差异摘要：答案质量、P50/P95 时延、token、工具调用数、advisory 缺口计数、迭代数、压缩次数
- [ ] 8.4 在 `dataset/final_answer_dataset.csv` 上跑两模式，确认 `autonomous` 在事实型样本上不劣于 `guided`（安全性验证）
- [ ] 8.5 在 8.1 的开放式样本上跑两模式并人工评分（价值证明）；同时记录成本侧倍数
- [ ] 8.6 按跑分结果回填 `autonomous` 的预算默认值与压缩参数
- [x] 8.7 结果与口径写入 `docs/baseline.md`

2026-09-08 真实验证记录：两轮共 160 条运行 / 160 条统一模型辅助评审完成，一次修改后复测，详见 `docs/reports/autonomy_evaluation_20260908/report.md`。8.4 已执行但不劣门槛未通过（次轮核心正确性 guided 20/20、autonomous 19/20，`final016` 退化）；8.5 的真实运行与成本完成，真人评分未进行；8.6 已记录保留默认参数的决策，但样本未触发压缩，长上下文校准未完成。以上三项保持未勾选，不以辅助评分或本次报告代替原门槛。

**退出判据**：事实型子集 `autonomous` 不劣于 `guided`；开放式子集有可复述的人工评分结论；成本倍数被明确记录并接受。若开放式子集未显示优势，停在此处并记录量化结论是合法结局。

## 9. 文档与契约收口

- [x] 9.1 `docs/agentic_loop_roadmap.md` 的 M6 表补记本方向，说明与「会话与 loop 状态融合」「子 agent」的关系
- [x] 9.2 在路线图中显式记录 `autonomy.mode` 是长期能力开关而非 I5 要求删除的迁移 flag，并说明与已删除的 `engine.mode` 的区别
- [x] 9.3 `AGENTS.md` 补充 `autonomy` 配置块与 CLI `--autonomy` 的说明
- [x] 9.4 `openspec validate --strict` 通过；`python -m pytest -q` 全绿；`git diff --check` 干净

**退出判据**：9.4 三项全部通过；路线图中能查到本开关长期存在的理由。
