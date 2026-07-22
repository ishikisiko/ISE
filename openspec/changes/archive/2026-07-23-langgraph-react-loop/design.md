# Design: langgraph-react-loop

## Context

当前 ReAct 循环由 `LangChainOrchestrator.create_react_agent()`（langchain/langchain_orchestrator.py:1455）构建的 `AgentExecutor` 驱动：内部是文本解析的 Thought/Action/Observation 黑盒循环，仅暴露 `max_iterations` 与 `handle_parsing_errors` 两个旋钮。循环结果的成功/失败完全由模型自声明 `Final Answer:` 决定，回退结果在 `_apply_postcheck`（langchain/langchain_orchestrator.py:1434-1453）中未经校验直接返回。已有的质量判定设施集中在 `langchain/postcheck.py`（规则筛查 `screen_search_answer` + judge 合并 `merge_judge_verdict`），只作用于默认管道首答，不作用于 ReAct 循环。

约束：
- `ReactAgentOrchestrator.answer()` 对外签名与返回字典结构必须保持兼容（被 `_apply_postcheck` 与测试依赖）
- 现有 postcheck judge 配置（`postcheck.judge`，含独立 provider）应复用，避免新增一套 LLM 角色配置
- 项目运行在 conda 环境 `env1`，依赖需写入 requirements.txt

## Goals / Non-Goals

**Goals:**
- 循环状态显式化：evidence pool、约束 checklist、迭代历史、连续无进展计数均存在于 graph state，可观测、可测试
- 每轮迭代后经 evaluate 节点产出结构化 `LoopVerdict`，终止决策由"模型提议 + evaluate 处置"共同作出
- 终止语义细化为 `succeeded` / `exhausted` / `stagnated` / `unrecoverable`，并通过 `control["loop_status"]` 暴露
- 评估成本可控：规则评估每轮执行（零 LLM 成本），LLM judge 按间隔与终局执行
- 迁移期保留 legacy `AgentExecutor` 路径，配置开关切换

**Non-Goals:**
- 不改变默认主链路（非 ReAct 统一搜索编排器）的执行逻辑
- 不改变 postcheck 对首答的筛查规则本身（仅复用其判定原语）
- 不引入在线学习或自动调参；阈值均为静态配置
- 不做多 agent 协作 / 并行工具调用（保持每轮单工具）

## Decisions

### D1: 用 LangGraph StateGraph 自建循环，而非继续使用 AgentExecutor 回调

图结构：`agent (act) → tools (observe) → evaluate → 条件边 (continue | finish)`。

- **被选方案**：显式状态机。evaluate 是一等公民节点，可以直接读写 state（checklist、证据池、计数器），条件边天然表达四种终止语义。
- **备选 1：AgentExecutor + callback（原方案 B）**。callback 只能旁观，无法可靠地修改循环状态或注入"约束已满足，请收尾"的控制信号；提前终止依赖异常 hack。否决。
- **备选 2：LangGraph prebuilt `create_react_agent`**。它内部仍是"模型自声明结束"，evaluate 节点需要包在外层循环里，每轮重启图，状态传递反而更绕。否决。

### D2: 模型提议结束，evaluate 处置结束（自评 + 外部验证双重确认）

agent 节点输出 `Final Answer` 时不直接终止，而是进入 evaluate：
- checklist 为空且规则抽查通过 → `succeeded`，接受答案
- checklist 有缺项且剩余迭代 > 0 → 将缺项作为反馈消息注回 state，循环继续（模型看到"尚缺：时间约束/对比覆盖"）
- checklist 有缺项且剩余迭代 = 0 → `exhausted`，返回 best-effort 答案

理由：单纯模型自声明（现状）无验证；单纯规则判定会误杀合理答案（规则覆盖不全）。双重确认在两者间取平衡。

### D3: 复用 postcheck 判定原语，但不复用其 verdict 类型

- 把 `langchain/postcheck.py` 中的纯函数原语（`_contains_any`、`_extract_numbers`、`_stringify_evidence`、模式常量）提取/复用为迭代内检查：约束覆盖（时间、对比、多跳长度）与证据增量检测
- 循环内使用新的 `LoopVerdict` dataclass（`new_evidence`、`constraints_met`、`constraints_missing`、`should_continue`、`reason`），不复用 `PostcheckVerdict`：后者面向"完整答案是否可交付"，前者面向"本轮迭代是否有进展、是否应继续"，生命周期与字段语义不同，混用会互相污染

### D4: 分级评审——规则每轮、judge 按间隔

- 规则评估：每轮执行，零 LLM 成本
- LLM judge：每 `judge_interval` 轮（默认 2）及强制终止前各执行一次；复用 postcheck judge LLM（`postcheck.judge` 配置的 provider），未配置时回退主 LLM
- judge 输入：query、当前最佳答案草稿、checklist 状态、最近 N 条 observation 摘要；输出与 `_run_postcheck_judge` 相同的 JSON 协议（passes / recoverable / missing_constraints），降低实现分叉

理由：每轮 judge 的延迟与成本不可接受（一次回退最多 max_iterations 次额外 LLM 调用）；规则足以捕捉停滞与约束覆盖。

### D5: 停滞检测用"工具调用指纹 + 证据增量"双信号

- 指纹：`tool_name + 归一化参数`（小写、去空白）；连续 `repeat_threshold`（默认 2）轮指纹相同 → `stagnated`
- 证据增量：observation 文本与 evidence pool 已有内容做 token 级重叠比较，新增 token 比例 < `new_evidence_min_ratio`（默认 0.1）记为无进展；连续 `no_progress_threshold`（默认 2）轮无进展 → `stagnated`
- 工具连续报错 `tool_error_threshold`（默认 2）次且无一次成功 observation → `unrecoverable`

理由：单一信号误判率高（换 query 措辞但搜索引擎返回相同结果，指纹不同但实际无进展）。

### D6: 消息协议改用 tool-calling，放弃文本解析 ReAct

新图中的 agent 节点使用 LangChain tool-calling（`bind_tools`）而非 `create_react_agent` 的文本协议：
- 消除 `handle_parsing_errors=True` 掩盖的解析失败噪声（当前循环里解析失败也被计为一次迭代）
- "模型提议结束"表达为：模型本轮不发起 tool call 而直接产出文本内容

风险：现有中文 ReAct prompt 为文本协议编写，需改写为 tool-calling system prompt；legacy 路径保留旧 prompt 不受影响。

### D7: 配置与开关

新增配置（config.example.json 同步更新）：

```json
"reactAgent": {
  "engine": "langgraph",            // "langgraph" | "legacy"（迁移期默认 legacy，稳定后切换）
  "evaluation": {
    "judge_interval": 2,
    "repeat_threshold": 2,
    "no_progress_threshold": 2,
    "tool_error_threshold": 2,
    "new_evidence_min_ratio": 0.1
  }
}
```

`postcheck.react_fallback` 下新增 `engine` 覆盖项，允许回退路径与独立 ReAct 路径分别开关。

### D8: 模块落点

- 新增 `orchestrators/react_loop_graph.py`：state 定义、LoopVerdict、evaluate 节点、图构建
- 修改 `orchestrators/react_agent_orchestrator.py`：按 `engine` 配置选择图执行或 legacy AgentExecutor；`answer()` 签名不变
- 修改 `langchain/langchain_orchestrator.py`：`create_react_agent` 保留（legacy 用）；fallback 元数据透传 `loop_status`
- 判定原语提取到 `langchain/postcheck.py` 的公开函数（`check_constraint_coverage`、`evidence_increment_ratio`），供 graph 与 postcheck 共用

## Risks / Trade-offs

- [LangGraph 新依赖引入版本冲突] → requirements.txt 锁定版本；`env1` 环境验证安装；legacy 路径不 import langgraph（惰性导入），缺包时自动回退 legacy 并告警
- [judge 调用增加回退路径延迟] → judge 间隔可配；评测脚本（tests/search_quality_pipeline.py）记录 judge 耗时；可为 judge 配置小模型 provider
- [evaluate 误杀导致循环空转或过早收尾] → 所有阈值配置化；`control["loop_status"]` 与每轮 LoopVerdict 写入 trace（utils/workflow_trace.py）便于事后审计调参
- [tool-calling 协议下模型不再输出 Thought，可解释性下降] → system prompt 要求最终答案前输出简要推理摘要；trace 记录工具调用序列本身即推理轨迹
- [双引擎并存期行为不一致] → parity 测试：同一查询集在两种引擎下运行，对比 loop_status 分布与答案质量分；稳定后再将默认切到 langgraph

## Migration Plan

1. 添加 `langgraph` 依赖并实现 `react_loop_graph.py`，默认 `engine: "legacy"`，功能不启用
2. 单测覆盖 LoopVerdict、停滞检测、四种终止语义；parity 脚本对比双引擎
3. `config.json` 切 `engine: "langgraph"`，观察线上 trace 一个评测周期
4. 稳定后移除 legacy 默认（另行提案决定是否删除 AgentExecutor 代码路径）

Rollback：配置改回 `engine: "legacy"` 即可，无数据迁移。

## Open Questions

- evaluate 反馈注回 state 的消息角色：system message（强约束但可能干扰上下文）还是 tool message（弱约束但更自然）？倾向 tool message，实现时 A/B 验证
- judge 是否需要看到完整 observation 还是摘要？默认最近 3 条截断摘要，视 token 成本调整
- `stagnated` 终止后是否值得用剩余预算做一次"换策略"搜索（强制 search_recovery 工具）再放弃？首版不做，留作后续增强
