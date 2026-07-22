# Proposal: langgraph-react-loop

## Why

当前 ReAct 循环由 LangChain `AgentExecutor` 黑盒驱动：循环只在模型自行输出 `Final Answer:` 或达到 `max_iterations` 时结束，过程不可观测、无中间质量判断，且回退结果未经任何校验直接返回（`langchain/langchain_orchestrator.py` 中 `_apply_postcheck` 直接返回 fallback 结果）。失败语义仅有"截断/未截断"两种，无法区分迭代用尽、停滞、工具持续失败等不同失败形态，导致兜底策略和评测都缺乏依据。

## What Changes

- 将 ReAct 循环从 `AgentExecutor` 迁移到 LangGraph 显式状态机：`act → observe → evaluate → (continue | finish | give_up)`，循环状态（evidence pool、约束 checklist、迭代历史）显式维护在 graph state 中
- 新增独立 `evaluate` 节点：每轮迭代后基于规则评估进展（复用 `langchain/postcheck.py` 的筛查逻辑），按配置间隔或终局时调用 LLM judge 做分级评审
- 引入结构化 `LoopVerdict`：每轮产出 `new_evidence`、`constraints_met`、`constraints_missing`、`should_continue`、`reason`
- 细化循环终止语义：`succeeded` / `exhausted` / `stagnated` / `unrecoverable`，写入 `control["loop_status"]` 及原因说明
- 停滞检测：连续重复工具调用或连续 N 轮无新证据时提前终止
- 回退场景下将 postcheck verdict 的 `failure_types` / `recovery_goal` 作为显式成功标准注入 graph 初始状态，evaluate 节点据此判断补救是否达成
- 保留 `ReactAgentOrchestrator.answer()` 对外接口与返回结构兼容（**BREAKING** 仅限内部执行引擎；`control` 元数据新增字段不破坏既有字段）

## Capabilities

### New Capabilities
- `react-loop-evaluation`: ReAct 循环的迭代内评估能力，包括 LoopVerdict 结构、规则评估、分级 LLM judge、停滞检测与终止语义（succeeded / exhausted / stagnated / unrecoverable）

### Modified Capabilities
- `react-agent`: 执行引擎从 LangChain `AgentExecutor` 黑盒循环改为 LangGraph 显式状态机；终止条件由"模型自声明或迭代上限"扩展为"evaluate 节点判定"；循环状态显式化
- `react-orchestrator`: `control` 元数据新增循环终止状态与原因；fallback 场景接收显式成功标准并在结果中暴露补救达成情况

## Impact

- **代码**：`orchestrators/react_agent_orchestrator.py`（引擎替换）、`langchain/langchain_orchestrator.py`（`create_react_agent` 及 fallback 元数据）、新增 `orchestrators/react_loop_graph.py`（或等价模块）、复用 `langchain/postcheck.py`
- **依赖**：新增 `langgraph` 包（requirements.txt）
- **配置**：`config.json` 的 `reactAgent` / `postcheck.react_fallback` 增加评估相关开关（judge 间隔、停滞阈值）
- **测试**：`tests/test_langchain_react_agent.py` 需覆盖新终止语义；新增 loop evaluation 单测
- **兼容性**：对外 `answer()` 接口与返回字典结构保持不变；仅 `control` 增加字段
