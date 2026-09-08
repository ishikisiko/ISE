# 文档索引

顶层四份是理解和维护 ISE 的入口，长期与代码同步；子目录按用途分开。
文档之间冲突时，架构方向以 `agentic_loop_roadmap.md` 为准，能力契约以 `../openspec/specs/` 为准。

## 顶层：长期维护

| 文件 | 内容 |
|---|---|
| [architecture.md](architecture.md) | 分层架构与组件图（mermaid） |
| [query_execution_paths.md](query_execution_paths.md) | `utils/query_orchestration.py` 契约边界与各执行路径 |
| [agentic_loop_roadmap.md](agentic_loop_roadmap.md) | Agentic loop 演进路线 M0–M6，架构方向的唯一权威 |
| [baseline.md](baseline.md) | 基线度量手册：怎么跑、记什么、各里程碑实测数字 |

## guides/：操作指南

| 文件 | 内容 |
|---|---|
| [guides/server_logging.md](guides/server_logging.md) | Web 部署的持久化审计与进程日志配置 |
| [guides/search_quality_evaluation.md](guides/search_quality_evaluation.md) | 检索质量评测步骤与回归脚本 |

## devbench/：开发能力 Benchmark（出题方资料）

评估 CLI + 模型能否独立交付 ISE 开发任务的框架。代码在私有 controller，本目录只放设计与操作说明。
整个目录被 controller 投影规则 `docs/devbench/**` 排除，不进入被测任务包；待办与优先级以 [../plan.md](../plan.md) 为准。

| 文件 | 内容 |
|---|---|
| [devbench/usage.md](devbench/usage.md) | 操作说明：选题、登记、启动、收卷、验收、报告 |
| [devbench/system_analysis.md](devbench/system_analysis.md) | 系统分析 v0.3：隔离、评分、候选题 |
| [devbench/p0_decisions.md](devbench/p0_decisions.md) | P0 决策记录 `p0-v2` |
| [devbench/cli_orchestration.md](devbench/cli_orchestration.md) | 管理 Agent 与 CLI 调度设计 `cli-orchestration-v1` |
| [devbench/background_executor.md](devbench/background_executor.md) | 后台执行器设计 `background-executor-v1` |
| [devbench/isolation_launcher.md](devbench/isolation_launcher.md) | 凭据代理、启动队列与三 CLI 验证 `isolation-launcher-v1` |
| [devbench/console.md](devbench/console.md) | 操作台设计 `console-v1` |
| [devbench/expansion_20260908.md](devbench/expansion_20260908.md) | T04 / T03 扩题交付 |
| [devbench/task_archive.md](devbench/task_archive.md) | 旧版任务历史明细，仅供追溯 |

## reports/：一次性分析与评测记录

已完成的工作记录，只追加不修改；结论已落地到代码或规划文档时以后者为准。

| 文件 | 内容 |
|---|---|
| [reports/autonomy_evaluation_20260908/report.md](reports/autonomy_evaluation_20260908/report.md) | guided / autonomous 两轮真实评测报告（2026-09-08） |
| [reports/autonomy_evaluation_20260908/protocol.md](reports/autonomy_evaluation_20260908/protocol.md) | 评测协议；`tests/autonomy_study.py` 冻结其摘要 |
| [reports/autonomy_evaluation_20260908/cases.md](reports/autonomy_evaluation_20260908/cases.md) | 逐题附表 |
| [reports/autonomy_evaluation_20260908/r2_change_plan.md](reports/autonomy_evaluation_20260908/r2_change_plan.md) | 第一轮结论与第二轮固定修改范围 |
| [reports/autonomy_evaluation_20260908/review_revision.md](reports/autonomy_evaluation_20260908/review_revision.md) | 辅助评审修订记录 |
| [reports/autonomy_evaluation_20260908/runtime_audit.md](reports/autonomy_evaluation_20260908/runtime_audit.md) | 运行口径补充审计 |
| [reports/architecture_improvement_plan.md](reports/architecture_improvement_plan.md) | 门控架构改进方案（已落地，见 `utils/query_orchestration.py`） |
| [reports/failure_analysis_tavily_firecrawl_brightdata.md](reports/failure_analysis_tavily_firecrawl_brightdata.md) | 对比类查询"迭代用尽"故障分析 |

## 放新文档时

- 与代码同步维护的说明放顶层或 `guides/`。
- Benchmark 出题方资料一律放 `devbench/`，否则不会被投影规则排除。
- 某次实验、故障或评测的记录放 `reports/`，多文件的评测建一个带日期的子目录。
