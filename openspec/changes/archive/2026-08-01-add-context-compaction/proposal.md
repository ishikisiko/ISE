## Why

不变量 I4 要求「工具调用次数、token、时延均有上界，且上界可配置、可观测」。工具调用次数有 `max_calls_per_query`、迭代有 `max_iterations`、时延有 `TimingRecorder`，**唯独 token 没有上界**：`react_loop_graph.py` 的 `messages` 在单轮内经 `add_messages` 单调累加，每次 `_observe` 把工具原文整段写入 `ToolMessage`（单条 ledger 条目上限 8000 字符、`fetch_url` 单页上限 8000 字符），轮内没有任何裁剪。目前只靠 `max_iterations` 间接兜住规模，这是副作用而非机制。

跨轮侧现有的 `_compute_message_removals` 只按**条数**（`history_window * 2`）删除最旧的 `ToolMessage`，且删除时不动持有 `tool_calls` 的 `AIMessage` —— 原生工具模式下会留下无响应的 tool_call，下一次 `_act` 会被 provider 拒绝。同时 `evidence_pool` 跨轮永不收缩，每次 `_observe` 都全量 `join` 计算增量比，checkpoint 随会话线性膨胀。

本 change 服务于 I4 在 token 维度上的补齐，并落在路线图 M6「会话与 loop 状态融合」方向：让多轮追问续跑一条**被压缩过**的 loop 轨迹，而不是无界累积的原始消息流。

## What Changes

- 新增确定性的**证据指针化压缩**（tier-1）：把超出保留窗口的 `ToolMessage` 正文折叠为 ledger 头部（`[E3] official · Kimi · 已抓全文 1081 字` + URL），正文仍由 `EvidenceLedger` / `evidence_records` 持有。折叠通过同 `id` upsert 完成，不产生孤儿 tool_call。
- 新增 `recall_evidence` 工具：模型可按 `[En]` 编号回灌被折叠的证据全文，受独立调用预算约束。
- 新增确定性的**决策轨迹重建**：把 `verdicts`、`fetch_outcomes`、历史 `tool_calls` 渲染成结构化轨迹注入上下文。当前这些字段全部存在于 state 却从不进入模型上下文，而系统提示禁止模型输出推理文本、`_process_narration_reason` 又会清空过程性叙述 —— 折叠证据后模型将无从得知「哪个 URL 抓过且失败」「上一轮被 critic 以什么理由驳回」。
- 新增 **token 会计**：以 `usage_metadata.input_tokens` 为实测基线、`count_tokens_approximately` 估算增量，并按实测/估算比值自校准（纯本地估算对中文低估 2-3 倍，不能单独作为阈值依据）。
- 新增 **token 预算触发的自动 compact**（tier-2）：图中增加 `compact` 节点，挂在 `evaluate → act` 边上；越过阈值且 tier-1 不足以回落时，用一次廉价 LLM 调用把中间区间收敛成结构化摘要。摘要输入是上述确定性轨迹与历次答案草稿，**不含工具返回原文**。
- 新增手动压缩入口 `POST /api/conversation/<id>/compact`。
- **BREAKING**（内部契约）：`conversation-resume` 的「上下文预算控制」由按条数删除改为分级压缩，`_compute_message_removals` 退役 —— 压缩结果经 checkpoint 持久化后天然覆盖跨轮场景。对外 API 与答案格式不变。
- 修复：`evidence_pool` 增加上界，消除 checkpoint 无界增长与 `_observe` 中 O(n²) 的全量拼接。

## Capabilities

### New Capabilities
- `context-compaction`: ReAct 循环的上下文预算控制与分级压缩 —— token 会计与阈值判定、证据指针化、决策轨迹重建、LLM 摘要降级链、压缩防抖与可观测性。

### Modified Capabilities
- `conversation-resume`: 「上下文预算控制」requirement 从「按 `CONVERSATION_HISTORY_WINDOW` 条数裁剪工具/观察消息」改为「委托 `context-compaction` 的分级压缩，并保证压缩后消息序列的 tool_call 配对完整」。
- `react-agent`: 图结构新增 `compact` 节点与 `evaluate → compact → act` 路径；`recall_evidence` 进入工具面。
- `react-tool-wrapper`: 新增 `recall_evidence` 工具的预算与契约约定。

## Impact

**代码**
- 新增 `orchestrators/context_compaction.py`（token 会计、三区切分、轨迹渲染、摘要降级链）
- `orchestrators/react_loop_graph.py`：新增 `_compact` 节点与路由分支、`_safe_cut_index`；`_observe` 折叠旧证据；删除 `_compute_message_removals`；`evidence_pool` 加界
- `langchain/langchain_react_tools.py`：新增 `ReActRecallEvidenceTool`
- `orchestrators/react_agent_orchestrator.py`：透传压缩指标到 workflow metadata
- `server.py`：手动压缩端点
- `evidence/ledger.py`：暴露按 eid 渲染头部（不含正文）的接口

**配置**
- 新增 `orchestration.context_compaction` 块（`enabled` / `context_window` / `per_model_window` / `threshold` / `keep_recent_rounds` / `max_compactions_per_run` / `summary_max_tokens` / `use_judge_llm`）；`config.example.json` 同步
- `conversation.history_window` 不再用于消息裁剪，仅保留其「历史轮次读取窗口」语义

**依赖**：无新增。`REMOVE_ALL_MESSAGES`、`trim_messages`、`count_tokens_approximately` 均由现有 `langgraph==1.2.9` / `langchain-core` 提供。

**不变量**
- I1：压缩不触碰 `evidence_records` / `EvidenceLedger`，`[En]` 仍解析到原始工具调用，provenance 链不断；`recall_evidence` 从同一 ledger 回灌，不引入新来源
- I2：`compact` 是显式节点，经 tracer 与 audit 记录压缩前后消息数、预算占比、摘要来源（llm / deterministic / truncate）
- I3：阈值判定、三区切分、轨迹渲染全部为确定性代码；LLM 只负责收敛草稿语义，且被约束为「只能引用已有 `[En]`，不得引入新数值」，其输出不参与任何终止或证据裁决
- I4：本 change 正是为补齐 I4 的 token 维度；上界可配置（`context_window` × `threshold`）、可观测（`compactions` / `peak_context_ratio` 进入 workflow metadata）
- I5：`enabled: false` 时 `compact` 节点在图中但路由永不选中，行为退回当前语义，可独立回退。该开关是能力开关而非迁移期 flag，不属于 I5 要求 M5 后删除的运行时开关
