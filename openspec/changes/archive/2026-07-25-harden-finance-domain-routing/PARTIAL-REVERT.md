# 归档说明：部分回退

归档日期：2026-07-25。本变更的任务全部实现完成，但归档时按
[docs/agentic_loop_roadmap.md](../../../../docs/agentic_loop_roadmap.md) 的 M0
拆开处置：分类器一侧回退，preflight 一侧保留。

**delta spec `specs/finance-domain-routing/` 未同步进 `openspec/specs/`**——它描述的
守卫式分类行为已被回退，同步进去只会新增一份出生即失效的 spec。

## 保留

| 任务 | 内容 | 去向 |
|---|---|---|
| 2.1 | 符号抽取限定为显式语法、交易所代码、原文大写候选，保留已知映射 | 留在 `search/source_selector.py`，M2 迁入 finance skill 的 preflight |
| 2.2 | LLM 仅用于校验歧义大写候选；空结果时回退搜索 | 同上 |
| 2.3 | provider 错误载荷视为失败，全部失败时返回 unhandled | 同上 |
| 3.1 | 对应测试 | `tests/test_source_selector_finance_symbols.py`（原 `..._finance_routing.py`） |

理由：这三项本质是**工具入参校验**，与路由架构无关，在 agentic loop 下同样需要。

## 回退

| 任务 | 内容 | 理由 |
|---|---|---|
| 1.1 | LLM 分类提示词中的 finance 正例/反例与不确定回退 | 分类器在 M3 删除，为其加固是对将死代码的投入 |
| 1.2 | LLM finance 标签需确定性关键词或符号证据交叉校验 | 同上 |

连带移除：`_extract_finance_symbols` 的 `allow_intelligent` 参数（回退 1.2 后无调用方），
以及三个只测分类行为的用例。

**知识未丢失**：1.1/1.2 编码的正反例已转为数据形态存于
`skills/finance/evals/cases.jsonl`，M2 建 finance skill 时接入 pytest。

## 若要恢复

**1.1/1.2 的实现不在 git 历史中**——本变更实现完成后一直停留在工作区，未提交，回退发生在
首次提交之前。`git revert` 取不回来。

恢复依据是本目录的 `proposal.md` / `design.md` / `tasks.md`（描述了要做什么与为什么），
加上 `skills/finance/evals/cases.jsonl`（正反例的数据形态）。按这两者重新实现的成本，
低于当初从零设计的成本。
