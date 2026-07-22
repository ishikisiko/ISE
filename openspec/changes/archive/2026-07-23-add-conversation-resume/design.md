# add-conversation-resume Design

## Context

系统当前为无状态单次问答：`server.py` 每请求重建 pipeline；`LangChainOrchestrator.answer(query)` 无历史参数；`react_loop_graph.py` 的 `run()`（:551-574）每次从空 `initial_state` 开始，`build_graph()`（:232）编译时未挂 checkpointer。底层 `LLMClient.chat(extra_messages=...)` 支持历史注入但未接线。

已确认的基础事实：

- `ReactLoopState.messages` 使用 `add_messages` reducer（追加语义，支持 `RemoveMessage` 删除）；其余字段为"输入中出现则覆盖、缺席则保留 checkpoint 值"。
- LangGraph 1.2.9 已固定，`langgraph-checkpoint-sqlite` 未安装。
- 对已正常结束（到达 END）的 run，用同一 `thread_id` 再次 `invoke(partial_input)` 会从 START 开始新一轮，但 state 以最后 checkpoint 为基座与输入合并——这正是"续跑"所需语义。
- 现有 post-check fallback（`langchain_orchestrator.py:1418-1449`）的 `fallback_context` 结构与用户反馈轮同构，可直接复用其证据摘要格式化逻辑。
- 单用户本地部署，无需多用户隔离。

## Goals / Non-Goals

**Goals:**

- 同一 `conversation_id` 的后续请求恢复 ReAct 完整状态（messages、evidence_pool、verdicts），用户反馈作为新一轮输入注入循环，证据与工具历史自然复用。
- 三类反馈意图（改写 / 补充修正 / 全新话题）合理分流；改写类不产生多余检索，补充修正类按需增量检索。
- 时间约束跨轮继承，锚点不漂移。
- 上下文成本有界：历史窗口与 checkpoint 消息裁剪。
- 完全向后兼容：不带 `conversation_id` 的请求行为与现状一致。

**Non-Goals:**

- 多用户隔离、认证、会话权限。
- 跨进程/分布式会话存储（Redis 等）。
- 中途中断恢复（interrupt/resume 于 run 内部断点）——续跑粒度为"整轮"。
- UI 上的会话管理界面（列表、重命名、删除），仅提供"新会话"重置。
- 改写路径的独立 LLM 重写管线（见 Decision 2，改写由图内模型自主决策完成）。

## Decisions

### Decision 1: SqliteSaver 作为 checkpointer，单库文件本地持久化

挂载点：`react_loop_graph.py` 的 `build_graph()`，`builder.compile(checkpointer=saver)`。新增依赖 `langgraph-checkpoint-sqlite`（与 langgraph 1.2.9 配套）。数据库路径走配置 `CONVERSATION_CHECKPOINT_PATH`，默认 `./checkpoints/conversations.sqlite`。

备选方案：

- `MemorySaver`：重启即丢，交付后用户隔天提意见是真实场景，否决。
- 自研 JSON 快照：重复造轮子，且丢失 LangGraph 自带的 run 版本管理，否决。

单用户场景下 SqliteSaver 的并发能力足够；saver 实例进程级单例，随 pipeline 构建惰性创建。

### Decision 2: 续跑一律走 ReAct 图，意图判别只分「延续 / 新话题」两路

不做独立的三路分流管线。`conversation_id` 存在且能恢复 checkpoint → 作为续跑进入 ReAct 图；否则走现有路由（small talk / direct / 搜索主链路）。

「改写 vs 补充修正」不由外部分类器决定，而是交给图内模型：续跑输入包含完整历史与证据池，模型对"精简一下""换成表格"这类反馈会自然选择不调用工具直接产出 final，对"再深入一点"会自然增量检索。ReAct 循环的 evaluate 对无工具调用的轮次成本极低。

理由：

- 外置改写管线会导致该轮对话不经过 checkpoint，造成历史分叉（改写轮丢失，下一轮基于旧答案）。
- 所有轮次统一过图，checkpoint 永远是完整事实源，无需第二套同步逻辑。
- 意图误判风险消失，代价仅是续跑固定走 ReAct（比 direct 略贵的系统提示与一次 evaluate）。

备选方案：外置三路分类器（revise/deepen/new）——因上述历史分叉问题否决。「新话题」判别保留：见 Decision 4。

### Decision 3: 续跑输入的构造——保留 evidence_pool，重置循环计数，裁剪消息

续跑时 `run()` 不再构造完整 `initial_state`，而是构造部分输入：

```python
followup_input = {
    "messages": [HumanMessage(content=feedback_with_context)],  # add_messages 追加
    "iteration": 0,                    # 迭代预算按轮重新计算
    "constraints_missing": [...],      # 新一轮成功标准（含继承的时间约束）
    "last_fingerprint": None,
    "fingerprint_streak": 0,
    "no_progress_streak": 0,
    "tool_error_streak": 0,
    "had_successful_observation": False,
    "last_round_new_evidence": False,
    "last_round_observations": [],
    "final_proposed": False,
    "termination_reason": None,
    "final_answer": None,
    "judge_error": None,
}
graph.invoke(followup_input, config={"configurable": {"thread_id": conversation_id}, ...})
```

关键取舍：

- `evidence_pool` **刻意缺席** → 从 checkpoint 原样保留，供模型复用与 evaluate 去重。
- `verdicts` **刻意缺席** → 保留历史判定轨迹，可审计。
- `messages` 中的反馈消息前拼接一段续跑上下文（上一轮 final answer 摘要 + 继承的时间约束 + 用户反馈原文），复用 `_format_evidence_summary` 的思路但面向消息而非 fallback dict。

**消息裁剪**：`add_messages` 使 checkpoint 消息单调增长，而 `_act` 每次全量送入 LLM。续跑前计算消息预算，超出时用 `RemoveMessage` 操作清除最旧的工具/观察消息，保留：首轮 HumanMessage、各轮 final answer 消息、最近 N 条交互。窗口大小走配置 `CONVERSATION_HISTORY_WINDOW`（默认 5 轮交互）。

### Decision 4: 「新话题」判别与轻量会话记录

续跑恢复的是 ReAct 状态，但首答可能走的是 direct/small talk 路径（无 checkpoint）。为覆盖这类对话且不污染 ReAct state：

- 服务端维护一张轻量会话记录表（同一 SQLite 库内，`conversation_turns`：`conversation_id, turn_index, query, answer, time_constraint, created_at`），每轮请求/应答无论走哪条路径都写入。它同时承担审计与调试职能。
- 续跑请求到达时，先用规则+轻量判别（复用现有 router 链，扩展一个 followup 分支）判定「延续 / 新话题」：
  - **延续**：按 Decision 3 续跑（有 checkpoint）或以会话记录为上下文新跑（无 checkpoint，如首答是 direct）。
  - **新话题**：忽略旧 checkpoint，按新 thread 处理（旧 checkpoint 保留不删，便于回溯）。
- 判别失败默认「延续」，倾向保留上下文。

备选方案：让用户在前端手动点"新会话"——保留此操作作为显式覆盖，但自动判别仍是默认体验，因为用户不会记得点。

### Decision 5: 时间约束继承锚定

`time_parser` 解析结果（结构化时间约束）随会话记录表落库。续跑轮：

- 用户反馈含新时间表达式 → 以当前时间重新解析，覆盖继承值。
- 不含 → 将首轮解析出的时间约束注入续跑输入的 `constraints_missing` 与反馈上下文消息，防止"那后来呢"被锚定到新一轮运行时刻。

### Decision 6: 接口与配置

- `server.py`：`_prepare_answer_context` 解析可选 `conversation_id`（字符串，不做格式强校验）；`_execute_answer` 透传 `pipeline.answer(..., conversation_id=...)`。SSE 与 JSON 两接口一致。
- `ReactAgentOrchestrator.answer()` / `LangChainOrchestrator.answer()` 增加可选关键字参数 `conversation_id=None`，缺省完全走现状路径。
- `frontend/script.js`：localStorage 持有 `conversationId`（uuid），`buildPayload` 携带；"新会话"按钮重新生成。不新增任何"基于上轮调整"类 UI 文案，流式阶段展示沿用现有 tracer。
- 配置项（`config.json`，均有默认值，缺省不破坏现有部署）：
  - `CONVERSATION_ENABLED`（默认 `true`）
  - `CONVERSATION_CHECKPOINT_PATH`（默认 `./checkpoints/conversations.sqlite`）
  - `CONVERSATION_HISTORY_WINDOW`（默认 `5`）
  - `CONVERSATION_MAX_THREADS`（默认 `200`，超出 LRU 清理）
- CLI：可选 `--conversation-id`；未提供时每次运行生成新 id 并打印在结果 JSON 中，供脚本化续跑使用。不做交互式 REPL。

### Decision 7: 并发与故障降级

- 同一 `conversation_id` 的并发请求会交错污染 checkpoint：服务端按 thread 持 `asyncio.Lock` 串行化。
- checkpointer 初始化失败（缺依赖、DB 损坏）→ 记日志并降级为无状态模式，不影响主流程可用性。
- checkpoint schema 演进：state 中写入 `schema_version`，不匹配时旧 thread 按新话题处理（单用户可接受，不做迁移）。

## Risks / Trade-offs

- [续跑固定走 ReAct，比 direct 路径贵（系统提示更长、多一次 evaluate）] → 反馈轮本身期望"调整"，ReAct 的 verify 能力是特性不是浪费；改写类反馈图内零工具调用，成本可控。
- [证据池跨天复用，时效性证据可能陈旧] → 证据条目带时间戳，续跑上下文注明收集时间；模型判断需要新数据时会增量检索。接受此近似，不建自动失效机制。
- [LangGraph checkpointer API 版本耦合] → langgraph 已 pin 1.2.9；checkpointer 初始化包在 try/except 中，失败降级无状态。
- [消息裁剪误删关键工具结论] → 裁剪只针对工具/观察消息，final answer 与各轮 HumanMessage 永不删；证据池本身另有保留。
- [SQLite 线程积累无上限] → `CONVERSATION_MAX_THREADS` LRU 清理；单用户量级下风险低。
- [「新话题」误判导致旧上下文被错误忽略] → 默认倾向延续；显式"新会话"按钮兜底。
- [同一 SQLite 文件同时承载 checkpointer 与会话记录表，写竞争] → saver 与记录表共用连接串但各自表；按 thread 锁已串行化写路径。

## Migration Plan

1. `pip install langgraph-checkpoint-sqlite` 并更新 `requirements.txt`。
2. 部署时首次运行自动建库建表，无需数据迁移（此前无会话概念）。
3. 前端旧版本不带 `conversation_id` → 服务端按无状态处理，双向兼容，可任意顺序发布。
4. 回滚：将 `CONVERSATION_ENABLED=false` 即恢复纯无状态行为；checkpoint 数据保留不删，可再次开启恢复。

## Open Questions

- 会话记录表是否暴露查询接口（`GET /api/conversations/<id>/turns`）供调试？倾向 v1 不做，日志已覆盖。
- CLI 是否需要 `--new-conversation` 显式语义？当前用"不传 id 即新会话"隐式表达，待定。
