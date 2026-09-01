## Context

M5 之后运行时收敛为单一 LangGraph `act → observe → evaluate` 循环，规则强度直接写死在 `ReactLoopGraphRunner` 的方法体里：

| 规则 | 位置 |
|---|---|
| 成功标准注入 | `_derive_checklist` / `_format_success_criteria` |
| critic 驳回终答 | `_evaluate` → `evaluate_termination` → `_rejection_message` |
| 引用机械校验 | `_check_draft_citations` → `evidence/citation_check.py` |
| 过程叙述判无效 | `_process_narration_reason` |
| 预算 | `termination.max_iterations` 与 per-tool `max_calls_per_query` |
| 强制合成 | `late_loop_no_answer` / `degraded_synthesis` / `_pricing_fetch` |
| 澄清短路 | `langchain_orchestrator.answer` 中 loop 前的 `_build_clarification_response` |

这七处没有任何配置面可以调节，也没有中间强度——要么全绑定，要么改代码。`_evaluate` 现为 290 行的单函数，已经是这些规则交织的产物（`force_synthesis`、`degraded_synthesis_force`、`compact_next`、`pricing_recovery` 四个布尔量互相排斥地拼装 `reason` 与 `next_action`）。

约束：不变量 I1–I5 与 `openspec/config.yaml` 的 proposal 规则第 2 条（不得重新引入 engine flag 或第二套执行路径）。

## Goals / Non-Goals

**Goals**
- 规则强度成为一个可注入的策略维度，同一个 loop 支持 `guided` 与 `autonomous` 两种自主度
- `guided` 为默认，行为与本 change 前完全一致，可独立回退
- 高自主模式在开放式任务上可用：模型主导计划、自主收尾、自主澄清，预算充足且可中止
- 两个模式可离线定量对比（质量与成本两侧）

**Non-Goals**
- 不按 query 内容自动选择自主度——那等价于复活已退役的分类器语义
- 不新增第二张图、第二个 orchestrator、第二套停止逻辑
- 不改变 `evaluate_termination` 的规则集本身（只改其结论是否绑定模型行为）
- 不在本 change 内引入子 agent、并行子循环或工具热加载

## Decisions

### D1 单图 + 策略注入，而非第二张图

`AutonomyPolicy` 是一个不可变值对象，在请求入口解析一次，透传给 `ReactAgentOrchestrator` 与 `ReactLoopGraphRunner`。图的节点与边不变。

**备选**：新建 `orchestrators/autonomous_loop_graph.py` 与 `engine.mode` 开关。**否决理由**：M4-D4 明确「同一运行时不能保留两个裁判」，M5 已删除 `engine.mode` 并在后置审计中清理了 `orchestrator_mode` 残留键。两张图意味着 audit / ledger / compaction / conversation / trace 五处接线各维护两份，且没有机制阻止两者长期漂移——legacy `SmartSearchOrchestrator` 就是这样长成 1288 行且 `audit` 引用数为 0 的。

**备选**：只把预算和 narration guard 做成可配置的最小验证。**否决理由**：拿不到完整高自主形态，评测结论无法指导后续投入；且这些配置项本身仍会散落，不解决"规则强度无载体"的根因。

### D2 advisory 而非关闭确定性规则

`autonomous` 下 critic 与引用校验照常完整计算，只是结论不绑定模型行为。

**理由**：确定性规则是纯 Python，成本≈0，唯一花钱的是 LLM judge（D3 单独处理）。保留计算换来的是——verdict 照常进 trace 与 audit（I2），两模式可按同一套指标定量对比，并且能统计「模型忽略了多少条 critic 提醒」，这是将来决定是否收紧的唯一数据来源。

**备选**：`autonomous` 下完全跳过 critic。**否决理由**：省不下多少成本，却使 trace 里 `deterministic_pass` / `failure_types` / `rule_hits` 全空，两模式的质量对比只能靠人工评分。

### D3 `autonomous` 默认关闭 LLM judge

judge 的作用是语义充分性把关，而 `autonomous` 已把收尾判断交给模型自身。在 15–20 轮的循环里按 `judge_interval` 调用会显著抬高成本，收益与模型自判重叠。策略保留 `judge_enabled` 字段，可单独打开做对照实验。

### D4 advisory 缺口每条只注入一次

advisory 唯一的真实副作用是：模型看到"你这句没来源"后可能反复自我修改，把预算烧在措辞上。对策是把已注入缺口集合放进 loop state（因而随 checkpoint 续跑保留），同一缺口不重复注入。verdict 仍每轮照常记录该缺口存在。

### D5 `preflight` 不进入策略字段

策略对象**不提供**关闭或降级 preflight 的取值——不是默认开启，是没有关闭这个取值。这是 I3 的承重墙：模型的意图不能单独决定一次外部调用的参数是否合法。高自主指的是"规划与收尾归模型"，不是"外部调用参数归模型"。

### D6 预算独立成组，不复用 `termination`

`termination` 块保持为 `guided` 的预算来源不动；`autonomous` 的预算写在 `autonomy.profiles.autonomous.budgets`。两者共用同一套字段名与同一套解析函数。

**理由**：复用同一个 `termination.max_iterations` 会让"切模式"变成"改全局预算"，`guided` 的回退保证就没了。缺失或非法取值回退到预设内置默认而非视为无限——I4 要求的是上界存在，不是上界被配置写对。

### D7 跨轮切换自主度即重置轨迹

会话记录最近一轮的生效自主度；新一轮不同则不续跑 checkpoint，以初始状态执行并在 control 中说明。

**备选**：直接续跑。**否决理由**：会出现"`autonomous` 续跑一条被 `guided` critic 驳回过的轨迹"——消息序列里带着返工反馈，已注入缺口集合的语义也对不上。**备选**：强制一次 compaction 后续跑。**否决理由**：压缩不消除语义错配，只是把它摘要化，复杂度高而正确性不增。重置是唯一不产生脏状态且实现最简单的选择。

### D8 规划文本载体选"文本与 tool_calls 共存"

`narration_guard=off` 时，`_act` 保留同时携带文本与 `tool_calls` 的响应中的文本部分（当前有 `tool_calls` 就不看文本），文本进入消息序列与 trace，但不作为候选终答。

**备选**：引入结构化 todo / plan 工具。**否决理由**：改动面大得多（新工具 + 状态字段 + 前端呈现），而"计划文本有地方去"这个需求用共存即可满足。若评测显示模型的计划需要跨轮稳定引用，再单独提 todo 工具。

### D9 取消在节点边界生效，通过显式端点而非依赖断连

新增 `POST /api/answer/<run_id>/cancel`；`/api/answer/stream` 在流开始时先发出携带 `run_id` 的事件。服务端维护 `run_id → threading.Event` 的有界注册表，loop 在每个节点入口检查该 event。

**理由**：SSE 的实现是"worker 线程跑 pipeline + 生成器消费队列"，客户端断连在 WSGI 下只有生成器被 GC 时才可能表现为 `GeneratorExit`，不可靠也不及时。显式端点是确定的信号。取消端点**不得**获取 `_conversation_lock`——那把锁正被目标请求持有。

**不中断进行中的工具调用**：等待其自然结束并登记结果后再停止推进。中途丢弃会产生无响应的 tool_call，破坏消息序列结构与 I1 的 provenance。

### D10 澄清作为工具 + 非终态，而非复用现有短路

`ask_user` 是本地工具（无 provider 调用、不产生证据、独立预算），触发后本次执行进入"等待用户输入"的非终态，loop 状态保留待续跑。`clarification_owner=system` 时该工具根本不出现在工具面上，现有确定性短路保持不变，两者互斥。

澄清答复续跑时 `iteration` 与工具预算**不重置**——否则模型可以通过反复发问绕开 I4 的上界。

### D11 先拆 `_evaluate`，再加策略分支

不允许在现有 290 行函数上直接加 `if policy.critic_verdict == "advisory"`。实施顺序固定为：先把 `_evaluate` 拆成「归一化事实 → 调 critic → 决定动作 → 组装 verdict」四段且行为逐字节不变（有测试锁），再让「决定动作」一段读策略。

### D12 关于净代码量为负的纪律

本 change 预期**净增**代码，与路线图 §6「净代码量应为负」相悖，理由如下：该纪律的适用对象是 M0–M5 的架构收敛阶段，其目的是防止在将死模块上继续投入。架构已于 M5 收敛，此后进入 M6 能力扩张阶段，新增能力必然净增。约束改为：新增代码必须集中在新增能力上，`AutonomyPolicy` 的引入必须**减少**而非增加 loop 中的隐式条件分支（`_evaluate` 拆分是这一条的具体兑现）。

## Risks / Trade-offs

- **成本与时延**：`autonomous` 预算放宽后每问 token 与 P95 时延预期为 `guided` 的 3–4 倍 → 取消能力与预算放开同批落地；跑分时成本指标与质量指标同等对待，超出可接受范围先调预算而非回退架构。
- **advisory 路径腐烂**：没人看的 verdict 会慢慢失效且测不出来 → 断言两模式下 verdict 均写入 trace 与 audit 的回归测试；对比跑分把 advisory 缺口计数作为固定指标。
- **`_evaluate` 复杂度失控** → D11 的拆分先行是硬前置，不是建议。
- **两模式行为漂移，`guided` 被误伤** → `guided` 下行为与变更前一致由回归测试保证；策略解析的默认路径必须有专门用例覆盖"配置中无 `autonomy` 块"。
- **长会话下压缩参数不适配**：`context_compaction` 的 `threshold=0.75` / `keep_recent_rounds=2` 是按 5 轮短 loop 调的，20 轮会频繁触发 tier-2 摘要 → 压缩参数进入 `autonomous` 的预算组，跑分时单独观察 `compactions` 与 `peak_context_ratio`。
- **模型滥用 `ask_user`**：反复发问而不检索 → 独立且很小的调用预算；澄清续跑不重置迭代计数。
- **取消注册表泄漏**：`run_id` 条目未清理 → 有界容量 + 请求结束即移除 + LRU 淘汰。

## Migration Plan

分四批，每批自身可发布可回退，默认 `guided` 始终不变：

1. **P0（不可拆）**：`_evaluate` 拆分 → `AutonomyPolicy` 骨架与解析 → 五个维度（checklist / critic / citation / narration / 预算）接入 → config `autonomy` 块与 per-request 覆盖 → 取消端点与节点边界检查。预算放开与取消能力必须同批，否则会出现无法中止的长 loop。
2. **P1**：规划文本载体（文本与 tool_calls 共存）。
3. **P2**：`ask_user` 工具、SSE 非终态事件、澄清归属互斥与续跑语义。
4. **P3**：`dataset/open_task_dataset.csv`（~20 条）、`baseline_runner --autonomy`、两模式对比跑分。

**回退**：任一批次出问题，将 `autonomy.mode` 置回 `guided` 即恢复变更前行为；彻底回退则回退版本。`autonomy` 开关是能力开关，不随本 change 完成而删除，需在 `docs/agentic_loop_roadmap.md` 的 M6 表中记录其长期存在的理由，避免后续架构审计将其识别为 I5 遗留的迁移 flag。

## Open Questions

- `autonomous` 的具体预算数字（初值拟定 `max_iterations=15`、per-tool 为 `guided` 的 3–4 倍），须由 P3 跑分回填。
- 澄清往返在前端会话列表中的呈现方式（是否单独成一条气泡）——不影响后端契约，实施时定。
- 开放式任务评测集的评分口径：人工评分 rubric 还是复用 `tests/search_quality_pipeline.py` 的 LLM 评分。前者更可信但不可复跑，倾向于两者都出、以人工为准。
