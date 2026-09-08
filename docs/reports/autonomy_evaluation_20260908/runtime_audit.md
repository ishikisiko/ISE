# 自主度评测运行口径补充审计

本文件记录两轮测量期间后续查明的既有接线缺口，不修改已冻结的查询、配置、产品版本或评审输入。它补充并更正[原协议](protocol.md)中“入口请求值”容易被理解为“实际 loop 调用值”的部分。

## 入口参数不等于实际生成参数

`ReactAgentOrchestrator.answer()` 接收 `max_tokens`、`temperature` 等参数，但调用 `_answer_with_langgraph()` 时没有继续传递这两个值；loop 的 `invoke()` 也没有用它们覆盖模型对象配置。因此：

| 层次 | max_tokens | temperature |
|---|---:|---:|
| 正式评测向 answer 传入的值 | 4000 | 0.2 |
| 主模型对象的实际配置默认值 | 5000 | 0.7 |
| judge 模型对象的配置默认值 | 512 | 0.0 |
| search_recovery 内嵌生成的显式值 | 1200 | 0.2 |

证据：`runtime/baseline/autonomy-20260908-measured/parameter-forwarding-audit.json` 是禁用网络后、使用相同配置与真实 builder 读取模型对象的探针；两轮冻结源码中的 `orchestrators/react_agent_orchestrator.py` 和 `langchain/langchain_llm.py` 相同。该探针不是历史 HTTP 请求体快照，主测量观察器只保存了 usage、状态、模型 ID 和时长，没有保存完整请求体。reasoning 仍按既有场景策略解析，不能只根据模型对象默认值推断每一阶段。

结论：不得声称“真实生成温度已设为 0.2、输出已限制为 4000”。两轮、两模式使用相同默认模型对象与相同转发缺口，因此比较对象仍是同一配置下的自主度差异，但不构成入口参数契约已生效的证明。发现时第二轮已冻结并启动，本次不在中途修补，也不把该未验证的入口值当成实际值。

## 嵌套调用仍可能缺少共享 timing 记录

第一轮 `open015-guided` 有 13 条 HTTP 成功响应，却只有 12 条应用 LLM timing 记录。其中没有 HTTP 错误，因此不能把多出的调用直接称为重试。对照代码，`search_recovery` 内嵌 `SearchRAGChain.answer(max_tokens=1200)` 未传共享 `timing_recorder`；轨迹确实调用了该工具。额外 HTTP 响应为 3,536 输入 / 1,200 输出 / 4,736 total token，从调用顺序和参数看与该内嵌生成吻合；这个归属是基于源码和轨迹的推断，并非请求 ID 级别关联证明。

本次修复解决了 shim 重建消息导致的 loop_act usage 丢失，不等于所有嵌套生成都已接入统一计时。`token_capture_complete` 仅表示**已经注册的应用调用**有 token，不能独自证明账务完整。两轮成本均继续使用独立 HTTP 观察值；中断、断连和 SDK 内部不可见的交互仍可能使已知总数只是下界，USD 保持未知。

第二轮 `open010-guided` 在修复后直接复现这个剩余缺口：应用侧 14 次已注册调用、253,657 token，独立 HTTP 记录 16 次调用、265,605 token，差 2 次 / 11,948 token；`token_capture_complete=true` 与这个差额同时存在。因而第二轮也不能用运行器进度行显示的应用 token 作为最终成本。

## 有效引用可能不在裁判视图内

最终 v3-evidence 评审保留真实 `[En]` 映射，但仍按冻结口径只提供前 12 项证据、每字段最多 1800 字符。第一轮 guided 的 `open004/010` 使用了视图之外的有效证据编号。汇总器追加 `review_known_citations_omitted`，把这种情况与完整 ledger 中也找不到的 `unresolved_citation_ids` 区分。

因此，辅助 grounding / evidence_support 分只能按所给证据视图解释，不是对整份资料的独立事实核验；核心事实错误必须单列，不能被“引用了某个支持错误事实的页面”抵消。
