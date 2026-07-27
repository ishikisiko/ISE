## Context

`ReactLoopGraphRunner` 的 `messages` 由 `add_messages` 单调累加。`_act` 每轮追加一条 `AIMessage`，`_observe` 每轮追加 N 条 `ToolMessage`，正文是工具返回原文：`orchestration.fetch_url.max_chars` = 8000、`evidence_ledger.max_entry_chars` = 8000、`web_search` 单次最多 5 条命中、`max_calls_per_query` 分别为 2~3。轮内没有任何裁剪，规模只被 `max_iterations` 间接兜住。

跨轮侧只有 `_compute_message_removals`：按条数（`history_window * 2`）删除最旧的 `ToolMessage` 与 shim 模式下以 `[工具` 开头的 `HumanMessage`。三个问题：

1. `trimmable` 不含携带 `tool_calls` 的 `AIMessage`。原生工具模式下删掉工具结果、留下工具请求，下一次 `_act` 会被 provider 以孤儿 tool_call 拒绝。当前长会话少，尚未暴露。
2. 纯删除，不做任何信息保留。
3. `evidence_pool` 跨轮永不收缩，且 `_observe` 每轮 `"\n".join(state["evidence_pool"])` 全量拼接算增量比，成本随会话平方增长，checkpoint 行同步膨胀。

一个反直觉但决定设计形态的事实：**这个循环的 `messages` 里几乎没有模型的推理**。系统提示明令「不要输出检索计划、下一步说明或自我对话」，`_process_narration_reason` 命中过程性叙述时 `_act` 直接把内容置空。`AIMessage` 实际只剩 `tool_calls` 与答案草稿。真正的推理轨迹在 `messages` 之外：`verdicts`（`TerminationDecision.to_dict()`，含 `rule_hits` / `failure_types` / `missing_constraints` / `evidence_sufficiency`）、`fetch_outcomes`、`constraints_met/missing`。

依赖侧已核实：`langgraph==1.2.9` + `langchain-core 1.4.9` 提供 `REMOVE_ALL_MESSAGES`、`trim_messages`、`count_tokens_approximately`；`UniversalChatModel._generate` 已把 `usage_metadata` 写到返回的 `AIMessage` 上。无需新增依赖。

## Goals / Non-Goals

**Goals:**
- 让 token 具备可配置、可观测的上界，补齐不变量 I4 在 token 维度上的缺口
- 压缩优先走确定性路径，LLM 只在确定性手段不足时介入，且不参与任何证据或终止裁决
- 压缩后 `[En]` 引用链不断，`citation_check` 与 provenance 保持可用
- 同一套机制覆盖轮内与跨轮，`_compute_message_removals` 退役
- 修掉孤儿 tool_call 与 `evidence_pool` 无界增长

**Non-Goals:**
- 不重构 `evidence_pool` / `evidence_records` 的数据模型（三层状态分层是后续方向，本 change 只加上界）
- 不改变答案格式、引用纪律或任何对外 API 契约
- 不引入 tokenizer 依赖（tiktoken 对本项目的多 provider 适配层本就不准）
- 不做跨会话的长期记忆或摘要归档

## Decisions

### D1：token 会计用「实测基线 + 增量估算 + 自校准」，不用纯估算也不用纯 usage

`count_tokens_approximately` 本质是 `chars / 4`，对中文低估 2-3 倍，单独用会让阈值形同虚设。provider 的 `usage_metadata.input_tokens` 精确但滞后 —— 决策必须发生在发送前。

```
budget = measured_baseline + k * approx(新增消息) + reserve
reserve = approx(system_prompt) + llm.max_tokens
```

`k` 为会话内滑动平均的校准系数，每次 `_act` 后用 `measured / approx(当时完整消息列表)` 更新。跑一两轮即收敛，无外部依赖。

**Alternatives**：(a) 引 tiktoken —— 对 anthropic / google / glm / zai 不准，且增依赖；(b) 每轮额外调一次 provider 的 count_tokens 端点 —— 多一次网络往返，且并非所有 provider 提供。

### D2：阈值取 0.75，不取 Claude Code 量级的 0.9+

单轮 `web_search`（5 条）+ `fetch_url`（8000 字符全文）就能让预算跳一大截。阈值必须留出能吃下一轮最坏情况的余量，否则会在压缩触发前先撞上下文墙。

### D3：`compact` 作为独立节点挂在 `evaluate → act` 边上，不内联进 `_act`

独立节点可被 tracer 记录、被 checkpoint、被单测，且不与 `_act` 的异常处理纠缠。

路由优先级在 `_route_after_evaluate` 内为：`end` > `pricing_fetch` > `synthesize` > `compact` > `act`。终止优先保证已能结束时不浪费一次摘要调用。

**必须在 `evaluate` 之后而非 `observe` 之后**：否则本轮新证据尚未进入 critic 判定就被折叠，`last_round_new_evidence`、指纹去重、停滞检测全部失真。

`recursion_limit` 从 `(max_iterations + max_synthesis_attempts) * 4 + 10` 提到 `* 5`，覆盖每轮可能多出的一跳。

**Alternatives**：在 `_act` 开头内联判定并返回 `RemoveMessage` —— 省一个节点，但压缩不可见于轨迹、难以单测、且 `_act` 的 try/finally 计时逻辑会把压缩耗时错记成模型调用耗时。

### D4：摘要输入是结构化投影，不是原始消息

这是本设计相对 Claude Code / Codex 的关键分歧。它们必须把整段散文喂给摘要模型，因为推理是自然语言；这里 90% 的状态已经是 dataclass。摘要 LLM 的输入只有三部分：

1. 确定性渲染的决策轨迹（检索动作 / 失败抓取 / 驳回理由 / 缺口 / 预算）
2. 被压缩区间内 `AIMessage` 的非空文本（历次答案草稿）
3. ledger 头部清单（不含正文）

输入从几万 token 降到一两千，且 LLM 只做一件它擅长的事：把多版草稿收敛成一段「当前已知结论」。幻觉面被压到最小，成本可忽略。

轨迹里最重要的是「已排除路径」。压缩最典型的事故是模型忘了某 URL 抓过且失败，于是重新 `fetch_url` 同一死链，烧光预算并触发 stagnation。`_official_fetch_instruction` 已经在用 `fetch_outcomes` 防这件事，压缩必须把这份状态带过去。

### D5：摘要以 `HumanMessage` 注入，不用 `AIMessage`

`AIMessage` 会让模型把摘要当成「我自己说过的话」，倾向直接复述结论、跳过对 `[En]` 的核验，与系统提示的引用纪律冲突。用户角色 + `[上下文摘要]` 前缀让它保持外部输入的性质。

### D6：证据折叠用同 `id` upsert，不用 `RemoveMessage`

`add_messages` 按 `id` upsert，返回一条同 `id` 的新消息即原地改写。这既保住了工具回合的配对结构（不产生孤儿 tool_call），也避开了 `_compute_message_removals` 现在靠 `if getattr(m, "id", None)` 静默跳过无 id 消息的问题。

整段重建（tier-2）则用 `RemoveMessage(id=REMOVE_ALL_MESSAGES)` 加完整新序列，比逐条删除干净。

### D7：切分点必须落在工具回合边界

```
def _safe_cut_index(messages, desired) -> int:
    # 切点落在 ToolMessage 上 → 其请求方在左侧，前移
    # 切点落在带 tool_calls 的 AIMessage 之后 → 把该 AIMessage 一并划入右侧
```

被压缩区间整体移除，不做「只删 ToolMessage 留 AIMessage」的部分移除 —— 摘要替代的是整段。这条同时是孤儿 tool_call bug 的根治。

### D8：跨轮复用同一机制，`_compute_message_removals` 退役

压缩结果由 `SqliteSaver` 持久化，下一轮 resume 自动继承。跨轮不再需要独立的裁剪逻辑。`conversation.history_window` 保留其「历史轮次读取窗口」语义（`get_recent_turns`），不再用于消息裁剪。

### D9：三级降级链，压缩永不使循环失败

`LLM 摘要 → 确定性轨迹 → trim_messages 尾部截断`。任一级失败都被吞掉并降级，摘要来源记入可观测字段。

### D10：防抖三条 + 强制收口

- `max_compactions_per_run`（默认 2）
- 两次压缩间隔至少一轮，且要求当前预算 < `tokens_at_last_compaction`
- 压完仍超阈值且不可再压（例如 `keep_recent_rounds=2` 里恰好含两条 8000 字符全文）→ 置 `force_synthesis=True` 走 `synthesize` 收口

宁可用现有证据出一个标注了不确定性的答案，也不要卡死或无限压缩。

### D11：净增代码的理由

本 change 预期净增约 350-450 行（新模块 + 节点 + 工具 + 测试），删除约 30 行（`_compute_message_removals` 及其调用）。净增的理由是它补的是不变量 I4 的缺口而非新功能：token 是四类预算里唯一没有上界的一项，且现有的替代物（按条数删除）本身携带一个会在长会话中确定触发的 provider 拒绝 bug。以更少代码达成同等效果的方案（纯 `trim_messages` 截断）会直接破坏 I1 —— 被截断的证据无法回灌，`[En]` 引用悬空。

## Risks / Trade-offs

- **摘要丢失关键状态，模型重复已失败的检索** → 决策轨迹为确定性渲染且强制包含 `fetch_outcomes` 的失败项；摘要 LLM 只在轨迹之上收敛草稿语义，无权删除轨迹内容
- **摘要幻觉出新数值** → 提示层硬约束「只能引用已有 `[En]`，不得引入新数值」；事实校验仍由 `citation_check` 对 ledger 执行，摘要不参与证据裁决（I3）
- **压缩后 `[En]` 悬空** → 折叠只改消息正文，`evidence_records` 与 ledger 不动；`recall_evidence` 提供回灌路径
- **压缩自身成为死循环来源** → D10 的三条防抖 + 强制收口
- **token 估算偏差导致过早或过晚压缩** → 自校准系数 + 0.75 的保守阈值；偏差进入可观测指标，可在基线跑分中复核
- **`compact` 节点增加图跳数，影响 P50/P95** → 正常查询（5 轮内）触发不了压缩，零开销；触发时多一次约 1500 token 输入的廉价调用。时延本就是路线图记录的主要负债项，压缩指标随基线一起复跑
- **`keep_recent_rounds` 取值过小损害多步推理** → 默认 2 轮完整保留；被折叠证据可回灌，信息不是不可逆丢失

## Migration Plan

1. 先落 D6/D7 的配对完整性与 `evidence_pool` 上界 —— 这两项是 bug 修复，独立于压缩能力，可单独发布
2. 落 tier-1（证据折叠 + 决策轨迹 + `recall_evidence`），`enabled` 默认 false，在基线样本上对比 token 与答案质量
3. 落 tier-2（token 会计 + `compact` 节点 + 摘要），仍默认 false
4. 基线跑分不劣于当前后翻默认 true，`_compute_message_removals` 同批删除
5. 回退：`orchestration.context_compaction.enabled = false` 即退回无压缩语义；节点仍在图中但永不被路由选中。该开关是能力开关而非迁移期 flag，不属于 I5 要求在 M5 后删除的运行时开关

## Open Questions

- `context_window` 默认值取多少？各 provider 差异大（GLM 200k、部分 OpenAI 兼容端点 128k），保守默认建议 128000，但需要确认实际部署的模型清单
- `keep_recent_rounds` 默认 2 是否足够？需要在 `final_answer_dataset.csv` 上验证多步比较类问题是否因折叠而退化
- 手动压缩端点是否需要暴露到前端 UI，还是仅作为调试接口
