# Skills

M2 已建立 runtime skill 契约：`contracts.py` 定义协议与 preflight 结果，`registry.py` 校验
`skill.yaml`、执行可用性门并构建模型工具面。每个 handler 继承统一的 `EvidenceSource`，输出可直接
进入 ledger 与 audit。

`finance/`、`weather/`、`location/`、`transportation/`、`sports/` 均为端到端 skill；每个目录的
`SKILL.md` 与 `evals/cases.jsonl` 分别向模型和 pytest 表达同一组正反例。新增 skill 必须同时提供
manifest、handler、模型说明和可执行 eval。结构化能力只从 registry 进入 plan、loop 与 legacy
入口；不存在统一领域 router 或兜底 `domain_api` 工具。
