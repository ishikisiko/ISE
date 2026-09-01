## Why

当前 loop 的规则强度是硬编码的：成功标准由 `analyze_query` 外部注入（`_derive_checklist` / `_format_success_criteria`）、确定性 critic 可驳回模型的终答（`_evaluate` → `_rejection_message`）、每个数值必须挂 `[En]` 否则终答无效（`_check_draft_citations`）、过程性文本被判为无效输出（`_process_narration_reason`）、预算为 5 轮 / 每工具 3 次、到点由系统替模型强制合成（`late_loop_no_answer` / `degraded_synthesis` / `_pricing_fetch`）。

这套约束对可核验事实类问题（价格、数值、对比、时效）是正确的，它正是 I1–I3 的落地形态。但对开放式任务（「调研某个方向」「帮我把这件事查清楚并给出建议」）全部变成阻力：checklist 为空导致没有终止信号、没有数值可引用、模型想列提纲被判成 process narration、5 轮不够。系统当前**没有任何配置可以表达「这类问题应该让模型自己主导」**，规则强度与执行路径耦合在一起。

本 change 把规则强度从执行路径中解耦出来，成为一个可注入的策略维度，使同一个 loop 能以两种自主度运行。

**路线图归属**：M6「长期能力（方向性）」。M6 列出的五个方向不含本项，因为 M0–M5 期间的问题域是「收敛架构」，开放式任务从未进入过评测集。本 change 属于 M6 的能力扩张性质（架构已收敛，此后是能力扩张），需在 `docs/agentic_loop_roadmap.md` 的 M6 表中补记为新方向并说明与「会话与 loop 状态融合」「子 agent」两项的关系。

## What Changes

- 新增 `AutonomyPolicy`：把 loop 中七处硬编码的规则强度提为策略字段（checklist 注入强度、critic 绑定强度、引用校验强度、judge 开关、narration guard、强制合成旁路、预算组），并提供 `guided` 与 `autonomous` 两个预设。
- **单图不变**：策略注入同一个 `ReactLoopGraphRunner` 与同一张 LangGraph 图，不新增执行器、不新增停止逻辑、不新增裁判。`evaluate_termination` 仍是系统中唯一的 critic。
- 新增 **advisory 裁决语义**：`critic_verdict` / `citation_check` 取 `binding`（现状）或 `advisory`。advisory 下确定性规则照常计算、verdict 照常进 trace 与 audit，但不驳回终答；缺口以系统观察注入上下文，**每条 constraint 只注入一次**，由模型自行决定是否返工。
- `autonomous` 预设：checklist 降为提示、critic 与引用校验取 advisory、`termination.judge` 关闭、narration guard 关闭、强制合成旁路关闭、预算显著放宽但保留上界、**preflight 保持 binding**（I3 不可关）。
- 新增**规划文本载体**：`autonomous` 下允许一条 `AIMessage` 同时携带文本与 `tool_calls`，模型的计划文本进入轨迹而不被丢弃。
- 新增 `ask_user` 工具与**模型自主澄清**：`autonomous` 下歧义不再在 loop 前由 `_build_clarification_response` 短路，由模型决定是否发问；SSE 需表达「等待用户输入」这一非终态。
- 新增**请求级取消**：`/api/answer/stream` 支持中途取消，取消后按已有证据收口并写入完整 audit。轮数放开与取消能力必须同批落地。
- 新增**开关面**：`config.json` 的 `autonomy` 块（默认 `guided`）、`/api/answer` 与 `/api/answer/stream` 的 per-request 覆盖、`main.py --autonomy`、前端模式切换与生效模式回显。
- 新增**模式对比能力**：`tests/baseline_runner.py` 支持 `--autonomy`，结果落 `runtime/baseline/<milestone>/<autonomy>/`；新增 ~20 条开放式任务评测集 `dataset/open_task_dataset.csv`（现有两个数据集测不出高自主模式的价值）。
- **BREAKING**（内部契约）：`_evaluate` 的返回语义从「裁决」扩展为「裁决或标注」，`react-loop-evaluation` 的四条 requirement 需要表达绑定强度维度。对外 API 与 `guided` 下的答案格式不变。

**非目标**：不引入按 query 类型自动选择自主度的路由（那会复活已退役的分类器语义）；模式切换在本 change 中只由用户/配置显式决定。

## Capabilities

### New Capabilities
- `autonomy-policy`: 自主度策略的字段定义、`guided` / `autonomous` 预设、解析优先级（请求 > 配置 > 默认）、advisory 裁决语义与缺口去重注入、策略的可观测性与模式对比要求。
- `model-initiated-clarification`: `ask_user` 工具契约、澄清作为非终态的流式协议、澄清轮次在会话记录中的语义、与确定性澄清短路的互斥关系。
- `loop-cancellation`: 请求级取消的触发、传播到 loop 节点边界的时机、取消后的证据收口与 audit 完整性、取消与预算耗尽的状态区分。

### Modified Capabilities
- `react-agent`: 「Process narration SHALL NOT become the final answer」从无条件改为受 `narration_guard` 约束；「Tool-aware ReAct Prompt」需按生效策略组装（成功标准的强制/提示语气、预算自述）；新增文本与 `tool_calls` 共存的处理约定。
- `react-loop-evaluation`: 「规则化迭代内评估」「分级 LLM 评审」「循环终止语义」「显式成功标准注入」四条 requirement 增加绑定强度维度；`LoopVerdict` 增加 advisory 缺口与其注入状态字段。
- `react-orchestrator`: 「Response control SHALL report actual loop execution」扩展为必须报告实际生效的自主度与策略来源。
- `react-tool-wrapper`: 新增 `ask_user` 的工具契约（本地、非检索、不产生证据记录、独立预算）；「Every tool SHALL enforce its own call budget」的预算取值来源改为策略。
- `search-response-control`: control metadata 增加生效模式、策略来源、advisory 缺口计数与取消状态。
- `conversation-resume`: 增加跨轮自主度变更的处理约定（切换模式时的续跑语义）与 `ask_user` 澄清轮次的续跑语义。

## Impact

**代码**
- 新增 `orchestrators/autonomy_policy.py`（策略定义、预设、解析、校验）
- `orchestrators/react_loop_graph.py`：主要改动面。拆分当前 290 行的 `_evaluate`；`_act` 支持文本与 tool_calls 共存；`_process_narration_reason`、`_check_draft_citations`、`_format_success_criteria`、三条强制合成旁路、预算读取全部改为受策略约束；新增取消检查点
- `utils/query_orchestration.py`：`TerminationDecision` 增加 advisory 表达，`evaluate_termination` 本身的规则集不变
- `langchain/langchain_react_tools.py`：新增 `ReActAskUserTool`
- `langchain/langchain_orchestrator.py`：澄清出口在 `autonomous` 下不短路；策略透传
- `orchestrators/react_agent_orchestrator.py`、`orchestrators/conversation_store.py`：策略透传与澄清轮次语义
- `server.py`：payload 字段、SSE 取消通道与非终态事件；`main.py`：`--autonomy`
- `frontend/script.js` / `index.html` / `styles.css`：模式切换与生效模式回显、取消按钮
- `tests/baseline_runner.py`：`--autonomy` 与分模式落盘；新增 `dataset/open_task_dataset.csv`

**配置**
- 新增 `autonomy` 块：`mode`（默认 `guided`）与 `profiles.{guided,autonomous}` 覆盖；`config.example.json` 同步
- `termination` 块保持为 `guided` 的预算来源，`autonomous` 的预算在 `autonomy.profiles.autonomous.budgets` 中独立表达

**依赖**：无新增。

**不变量**
- I1：advisory 只改变规则是否绑定模型行为，不改变证据的产生与登记。`originating_tool_call` 与 `EvidenceLedger` 两模式一致；`ask_user` 不产生证据记录，不进入 ledger
- I2：两模式下 audit 记录同等完整。advisory 的 verdict 照常写入 trace 与 audit——loop 自主度提高后 audit 比现在更重要，不是更次要；取消路径也必须写出完整记录
- I3：`preflight` 在两个预设下均为 `binding` 且策略不提供关闭它的取值。外部调用的参数合法性仍由确定性代码裁决
- I4：`autonomous` 放宽的是上界数值，不是上界的存在性。轮数、每工具调用数、token 预算、时延在两模式下均可配置且可观测；取消能力与预算放开同批落地，避免出现无法中止的长 loop
- I5：`autonomy.mode` 默认 `guided`，且 `guided` 下的**决策行为**与本 change 前一致——策略字段取现状值，无新增分支被选中，`reason` / `action` / `next_action` / `termination_reason` 与终答内容不变，可独立回退。响应与 verdict 中新增的自主度、advisory 与取消元数据是**附加字段**，不改变任何既有字段的取值；SSE 新增的 `run` 事件对未识别该事件的客户端无影响。**该开关是能力开关而非迁移期 flag**：它表达的是用户可选的产品维度，不是新旧实现并存的过渡态，因此不属于 I5 要求在 M5 后删除的运行时开关，需在路线图中显式记录以免后续架构审计将其当作残留清理

**与 `proposal` 规则第 2 条的关系**：本 change 不重新引入 `engine.mode`。`engine.mode` 曾用于在两套执行器（plan 与 loop）之间选择，M4-D4 与 M5 删除它的理由是「同一运行时不能保留两个裁判」。`autonomy.mode` 选择的是同一执行器、同一裁判的绑定强度——`evaluate_termination` 在两个预设下都是唯一的判定函数，且在两个预设下都会被调用。系统中不会出现第二张图、第二套停止逻辑或第二个 orchestrator。
