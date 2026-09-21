# fix-loop-retrieval-gating-and-synthesis

状态：已实施并归档（2026-09-21 开立，同日离线修复、真实验证、delta spec 同步主 spec 后归档）。原登记：质量评测 0918 真实运行报告 [§9 缺陷登记](../../../docs/reports/quality_evaluation_20260918/report.md)，产品缺陷 **QD-20260918-01 ～ 05**。评测计划（[quality_evaluation_plan.md](../../../docs/quality_evaluation_plan.md) §0.1）要求产品缺陷另开 change 修，本 change 即该 change。

## Why

0918 真实运行 98 题里，16 道可答题判 0，其中 9 题的根因不是检索质量，而是循环自身的五个缺陷：

| 编号 | 缺陷 | 证据 |
|---|---|---|
| QD-20260918-01 | `force_search` 从 `server.py` / `search_quality_pipeline.collect` 一路传到 `ReactAgentOrchestrator.answer`，但只出现在 docstring 里，`_answer_with_langgraph` 与 `ReactLoopGraphRunner` 都没有接收；循环不会被强制搜索 | 搜索采集三组 16/22、4/12、16/54 题零搜索，"空结果率"实际量的是没去搜 |
| QD-20260918-02 | 模型不调工具直接作答 → critic 以 `citation_missing` 拒收，但拒收文案是"不要再调用检索工具"（此时台账里根本没有证据可引）→ 模型第二轮输出过程叙述 → `no_progress_streak` 连续两轮无新证据 → `stagnated`，终答只剩"检索陷入停滞"模板，第一轮的候选答案被丢弃 | stagnated 16/16 题零搜索；final025（水的沸点）、open014 判 0；可靠性套件 4 题因此不稳定 |
| QD-20260918-03 | `_synthesize` 只有 `degraded_synthesis` 标志为真时才做接地综合；`late_loop_no_answer` / 压缩受阻触发的 `force_synthesis` 走 else 分支，取"最后一条 AI 文本"——那是一条工具调用（正文为空）——于是输出"迭代次数用尽"模板。第二次综合把模板当草稿再包一层前缀。open002 的 judge 解析失败后 critic 还把这条模板判成 `constraints_satisfied` | 7 道可答题（final020/040/057/062、open002/004/009）+ 拒答题 abs006/007 |
| QD-20260918-04 | `_best_effort_answer` 不幂等：综合阶段产出的答案已带"现有证据或执行预算不足……"前缀，终止时再次经过该函数又加一层 | 46/98 份答案前缀出现两次 |
| QD-20260918-05 | `analyze_query` 的英文指代线索 `this` / `that` / `it` 只做整词匹配，关系代词 "a system **that** deploys" 也命中；且 `has_pronoun and (not entities or len(comparison_members) < 2)` 里第二个条件对非对比问题恒真，问题里已经点名的实体无法解除歧义。澄清文案又固定说"comparison target" | open019 被反问要比较哪两个；离线扫描另见 wzh005/wzh023（"Kubernetes 这个名字"）、le028 |

路线图归属：M6 能力扩张期的循环可靠性修补，不改变架构方向；不引入第二套编排器、分类器或 engine flag。不变量：I1/I2 不涉及（证据与 audit 链路不变）；I3 本 change 强化——强制搜索、停滞与收口全部由确定性 critic 裁决；I4 新增的 `tool_free_threshold` 是有界且可配置的；I5 每项修复独立可回退，默认 guided 路径在无 `force_search` 请求时只在上述缺陷触发的场景改变输出。

## What Changes

- **QD-01**：`force_search` 透传到 `ReactLoopGraphRunner`。生效时（a）系统提示追加"先检索再作答"成功标准；（b）critic 在尚未发起任何搜索工具调用时拒收模型终答（缺口 `search_required`），拒收文案点名 `web_search`；（c）`control.request_parameters.force_search` 与 `control.loop_search_calls` 回显。搜索被禁用（`allow_search=false`）或工具面没有搜索工具时不生效。
- **QD-02**：（a）`no_progress_streak` 只统计发起了工具调用却没有新证据的轮次；不调工具的轮次计入新的 `tool_free_streak`，达到 `termination.tool_free_threshold`（默认 3）才判 `stagnated`；（b）台账为空时拒收文案改为明确要求调用 `web_search`；（c）终止时若当前草稿为空，回退到消息历史中最后一条非空候选答案（去掉工具协议与模板），不再只输出终态模板。
- **QD-03**：只要台账有已保留证据，任何进入 `synthesize` 节点的路径都做接地综合（`degraded` 标志只决定是否附加 coverage 提示）；接地综合失败与无证据路径都退回候选答案而非空草稿；critic 把终态模板文本视为空答案，不能被接受为 `succeeded`。
- **QD-04**：`_best_effort_answer` 幂等——先剥离已有的中英文限定前缀再加一次；模板文本不加前缀。
- **QD-05**：英文 `this` / `that` 只在作独立代词（后面不紧跟字母单词）时算未解析指代；问题里已有实体时不再因 `len(comparison_members) < 2` 恒真而判歧义（对比问题仍要求两个成员）；澄清文案按歧义类型措辞。

## Impact

- 代码：`orchestrators/react_loop_graph.py`、`orchestrators/react_agent_orchestrator.py`、`utils/query_orchestration.py`、`langchain/langchain_orchestrator.py`。
- 配置：`termination.tool_free_threshold`（新增，默认 3；`config.example.json` 登记）。
- specs：`react-loop-evaluation`（停滞检测、终止语义、强制合成）、`react-orchestrator`（answer 接口的 `force_search`）、`unified-rag-execution`（澄清短路的判定条件与文案）。
- 验收：新增 `tests/test_loop_retrieval_gating.py`；现有 613 项单测无回归；`python -m tests.quality.analysis_eval` 在 `query_analysis_gold.csv` 上 `critical_ambiguity` 指标不下降。真实运行验证（重跑 final025 / open019 / final020 三题与搜索采集 `--force-search`）在离线验收通过后、由用户指定模型再执行。
