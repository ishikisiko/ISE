# Tasks: langgraph-react-loop

## 1. 依赖与配置

- [x] 1.1 在 `env1` 环境安装 langgraph 并将锁定版本写入 requirements.txt（验证 `pip install` 无冲突）
- [x] 1.2 在 `config.example.json` 增加 `reactAgent.engine`（默认 `legacy`）与 `reactAgent.evaluation` 配置块（`judge_interval`、`repeat_threshold`、`no_progress_threshold`、`tool_error_threshold`、`new_evidence_min_ratio`）
- [x] 1.3 在 `postcheck.react_fallback` 下增加 `engine` 覆盖项的解析（`langchain/langchain_orchestrator.py` 的 `_normalize_postcheck_config`）

## 2. 判定原语复用改造

- [x] 2.1 在 `langchain/postcheck.py` 提取公开函数 `check_constraint_coverage(query, evidence_text, draft_answer, time_constraint)`，返回 `(constraints_met, constraints_missing)`，复用现有模式常量
- [x] 2.2 在 `langchain/postcheck.py` 提取公开函数 `evidence_increment_ratio(pool_text, new_observation)`，返回新增 token 比例
- [x] 2.3 为 2.1/2.2 编写单元测试（覆盖时间约束、对比意图、多跳意图三类 checklist 派生）

## 3. 循环状态机实现

- [x] 3.1 新建 `orchestrators/react_loop_graph.py`：定义 graph state（messages、evidence_pool、checklist、iteration、verdicts、停滞计数器、termination_reason）与 `LoopVerdict` dataclass
- [x] 3.2 实现 act 节点：`bind_tools` tool-calling 调用，模型无 tool call 时视为最终答案提议
- [x] 3.3 实现 observe 节点：执行工具调用，observation 写入 evidence_pool，记录工具调用指纹与错误计数
- [x] 3.4 实现 evaluate 节点：规则评估（2.1/2.2）每轮执行；按 `judge_interval` 及强制终止前调用 LLM judge（复用 postcheck judge LLM 与 JSON 协议）；产出 `LoopVerdict`
- [x] 3.5 实现条件边：四种终止语义（succeeded / exhausted / stagnated / unrecoverable）与"模型提前收尾被拒→反馈注回继续"分支
- [x] 3.6 实现停滞检测：指纹重复阈值与连续无新证据阈值判定，终止标记 `stagnated`
- [x] 3.7 惰性导入 langgraph：缺包时回退 legacy 引擎并打印告警

## 4. 编排器接入

- [x] 4.1 修改 `orchestrators/react_agent_orchestrator.py`：按 `reactAgent.engine` 选择图执行或 legacy AgentExecutor；`answer()` 签名与返回结构保持不变
- [x] 4.2 fallback 启动时将 postcheck verdict 的 `failure_types` / `missing_constraints` / `recovery_goal` 注入 graph 初始 checklist
- [x] 4.3 返回 `control["loop_status"]`、每轮 LoopVerdict 摘要及终止原因；保持既有 control 字段语义不变
- [x] 4.4 修改 `langchain/langchain_orchestrator.py`：fallback 元数据透传 `loop_status`；legacy `create_react_agent` 路径保留不动
- [x] 4.5 将每轮 LoopVerdict 与终止原因写入 `utils/workflow_trace.py` 轨迹

## 5. 测试

- [x] 5.1 `LoopVerdict` 与四种终止语义的单元测试（模拟 act/observe 序列，不依赖真实 LLM/搜索）
- [x] 5.2 停滞检测单测：重复指纹、连续无新证据、工具连续报错三条路径
- [x] 5.3 judge 失败退化单测：judge 抛异常/返回非法 JSON 时循环不中断且退化为规则评估
- [x] 5.4 更新 `tests/test_langchain_react_agent.py`：覆盖 `engine` 切换、`loop_status` 元数据、control 向后兼容
- [x] 5.5 parity 脚本：固定查询集在 legacy 与 langgraph 双引擎下运行，对比 loop_status 分布与答案质量（接入 `tests/search_quality_pipeline.py`）

## 6. 验证与切换

- [x] 6.1 `python -m pytest tests/` 全部通过
- [x] 6.2 CLI 冒烟：`python main.py "测试查询" --pretty`（默认 legacy）与 `reactAgent.engine=langgraph` 各跑一次，确认返回结构一致
- [x] 6.3 触发 postcheck fallback 的端到端验证：构造首答失败的查询，确认 `control["loop_status"]` 与 LoopVerdict 轨迹正确输出
- [x] 6.4 更新 `config.json` 样例与 AGENTS.md/文档中 ReAct 相关说明（如有）
