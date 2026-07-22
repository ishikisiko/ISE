# add-conversation-resume Proposal

## Why

当前系统是无状态单次问答架构：每次 `/api/answer` 请求都重建 pipeline，编排器不接收对话历史，LangGraph ReAct 循环每次从空 state 开始。用户在一次交付后提出修改意见时，系统无法基于上一轮的答案、证据池和工具调用历史继续调整，只能让用户手工粘贴上下文重新提问。

值得注意的是，现有 post-check → ReAct fallback 机制（机器裁判判定 → `fallback_context` → ReAct 修复）与"人类裁判（用户）提意见 → 继续修复"在结构上完全同构。本变更把这一循环从单请求内部延伸到跨请求，用 LangGraph checkpointer 实现真正的断点续跑。

## What Changes

- 新增会话标识 `conversation_id`：由前端生成/持有，随 answer 请求提交，服务端将其映射为 LangGraph `thread_id`。
- ReAct 循环图编译时挂载持久化 checkpointer（SQLite，单用户本地部署），`ReactLoopState`（messages、evidence_pool、verdicts 等）随 thread 跨请求保留。
- 编排器 `answer()` 接受可选会话上下文：同一 `conversation_id` 的后续请求恢复上次 state，将用户反馈作为新一轮输入注入 ReAct 循环，复用已有证据池与工具历史，按需增量检索。
- 新增反馈意图判别：同一会话内的后续查询先判定为「改写 / 补充修正 / 全新问题」，改写类走轻量重写（不检索），补充修正类走 ReAct 续跑，全新问题清空无关上下文后新跑。
- 时间约束继承：反馈轮中未显式重述的时间约束以首轮解析结果为锚，避免锚点漂移。
- 前端：payload 携带 `conversation_id`，提供"新会话"操作重置；不在 UI 显示"正在基于上轮调整"之类文案，执行过程照常展示。
- 上下文预算控制：历史增长时按滑动窗口/摘要压缩注入，避免 token 成本随轮数失控。

## Capabilities

### New Capabilities
- `conversation-session`: 会话标识的生成、提交、映射与生命周期（新建、续跑、重置），以及单用户本地部署下的会话隔离语义。
- `conversation-resume`: 基于 LangGraph checkpointer 的跨请求状态恢复，反馈意图判别与分流，证据池/时间锚点继承，上下文预算控制。

### Modified Capabilities
- `react-orchestrator`: `answer()` 接口新增可选会话上下文参数，支持从 checkpointer 恢复 state 续跑。
- `query-postcheck-fallback`: fallback 上下文的来源从"仅机器 post-check verdict"扩展为"机器 verdict 或人类反馈"，字段相应扩展。

## Impact

- **代码**：`orchestrators/react_loop_graph.py`（checkpointer 挂载）、`orchestrators/react_agent_orchestrator.py`（会话参数透传）、`langchain/langchain_orchestrator.py`（意图判别、反馈上下文构造）、`server.py`（payload 解析 conversation_id）、`frontend/script.js`（会话 id 生成与携带）。
- **依赖**：新增 `langgraph-checkpoint-sqlite`（或同等 checkpointer 实现）依赖。
- **数据**：本地新增 SQLite checkpoint 数据库文件（路径可配置，默认项目目录下），需在部署/清理文档中说明。
- **API**：`/api/answer` 与 SSE 接口请求体新增可选 `conversation_id` 字段，向后兼容（缺省即无状态单轮，行为与现状一致）。
- **配置**：`config.json` 新增会话相关配置项（checkpoint 路径、历史窗口大小、是否默认启用）。
