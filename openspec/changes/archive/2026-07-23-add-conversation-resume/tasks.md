# add-conversation-resume Tasks

## 1. 依赖与基础设施

- [x] 1.1 安装 `langgraph-checkpoint-sqlite`（与 langgraph 1.2.9 配套版本）并更新 `requirements.txt`
- [x] 1.2 新增 `orchestrators/conversation_store.py`：SQLite 会话记录表（`conversation_turns`：conversation_id、turn_index、query、answer、time_constraint、created_at），进程级单例，自动建表
- [x] 1.3 `config.json` / `config.example.json` 新增配置项：`CONVERSATION_ENABLED`（默认 true）、`CONVERSATION_CHECKPOINT_PATH`（默认 `./checkpoints/conversations.sqlite`）、`CONVERSATION_HISTORY_WINDOW`（默认 5）、`CONVERSATION_MAX_THREADS`（默认 200）
- [x] 1.4 实现会话存储治理：超 `CONVERSATION_MAX_THREADS` 时按 LRU 清理最旧 checkpoint 与会话记录；`checkpoints/` 加入 `.gitignore`

## 2. ReAct 图 checkpointer 接入

- [x] 2.1 `react_loop_graph.py` `build_graph()` 挂载 SqliteSaver checkpointer（惰性单例，初始化失败 try/except 降级为无 checkpointer 并记日志）
- [x] 2.2 `run()` 拆分两种模式：新会话构造完整 `initial_state`；续跑构造部分输入（重置 iteration/streaks/终止标志，缺席 evidence_pool 与 verdicts，messages 追加反馈消息），带 `config={"configurable": {"thread_id": ...}}` 调用
- [x] 2.3 实现消息预算裁剪：续跑前统计 checkpoint 消息，超窗口时以 RemoveMessage 移除最旧工具/观察消息（保留用户消息与 final answer 消息），窗口取 `CONVERSATION_HISTORY_WINDOW`
- [x] 2.4 单测：mock 图执行验证续跑输入构造——evidence_pool 缺席、计数字段重置、verdicts 缺席；验证裁剪只作用于工具/观察消息

## 3. 编排器会话接入

- [x] 3.1 `ReactAgentOrchestrator.answer()` 新增可选 `conversation_id=None` 参数并透传至 `run()`；`control` 输出 `conversation_resumed` 标记；缺省路径零 checkpoint 读写
- [x] 3.2 `LangChainOrchestrator` 扩展路由链：对携带已存在 `conversation_id` 的请求先判定「延续 / 新话题」，判别异常默认延续；新话题忽略旧 checkpoint 走现有路由
- [x] 3.3 实现续跑上下文构造：复用 `_format_evidence_summary` 思路，拼装上一轮答案摘要 + 继承时间约束 + 用户反馈原文，注入续跑 HumanMessage；元数据标记裁判来源为人类反馈
- [x] 3.4 时间约束继承：续跑反馈无时间表达式时从会话记录取最近一轮结构化约束注入 `constraints_missing` 与上下文；有新表达式则以当前时间解析覆盖并随本轮落库
- [x] 3.5 单测：延续/新话题判别分流、改写类反馈零工具调用路径、时间锚点继承与覆盖

## 4. Server 与前端接入

- [x] 4.1 `server.py` `_prepare_answer_context` 解析可选 `conversation_id`（JSON 与 SSE 两接口一致），`_execute_answer` 透传至 pipeline
- [x] 4.2 `server.py` 按 `conversation_id` 持 asyncio 锁串行化同会话请求；每轮应答完成后写入会话记录（全路径）
- [x] 4.3 `frontend/script.js`：localStorage 生成/持有 `conversationId`，`buildPayload` 携带；新增"新会话"按钮重置 id；不新增任何续跑阶段文案
- [x] 4.4 `main.py` CLI：新增可选 `--conversation-id`；未提供时生成新 id 并输出在结果 JSON 中

## 5. 验证

- [x] 5.1 `python -m pytest` 全量通过，新增用例覆盖 2.4 / 3.5 及会话记录读写
- [x] 5.2 手动验证：`python main.py "..." --pretty` 记录返回的 conversation_id，再带 `--conversation-id` 提反馈（"精简一点"），确认答案基于上轮且未重新检索
- [x] 5.3 手动验证：Web UI 连续提问 → 提修改意见 → 确认答案基于上轮调整、证据复用、SSE 阶段展示正常；点"新会话"后确认上下文隔离
- [x] 5.4 手动验证：带"上周"时间约束首轮 + "那后来呢"反馈轮，确认时间锚点继承；checkpointer 降级（移除依赖）时主流程可用
- [x] 5.5 更新 `AGENTS.md`/`README`：会话功能说明、新配置项、checkpoint 目录清理方式
