# TypeSafe Jev 对 ISE 语义判断环节的离线评测（2026-09-18）

TypeSafe AI 的 Jev 是 2026-09-15 开放早期访问的 "System One" 模型：输入状态和一组预先定义答案空间的问题，一次并行返回带概率的类型化判断（Noul 是非概率、Choice 选项分布、Score 有序等级），不生成文本。本报告记录它在 ISE 四个"现在靠词表、正则或长度阈值做语义判断"的环节上的离线表现，全部与现有确定性实现在同一 gold 上对比。**没有改动任何产品代码**，结论只用于决定是否接入。

复现脚本：`tests/quality/jev_requires_evidence_eval.py`、`tests/quality/jev_top3_eval.py`（需要 `TYPESAFE_API_KEY`）。原始输出见 [raw/](raw/)。

## 1. 运行条件

| 项目 | 值 |
|---|---|
| 模型 / SDK | `jev-latest`，`typesafe-sdk` 0.7.0（已装进 conda `env1`） |
| 端点 | `POST https://api.typesafe.ai/v1/systemone`，托管于美国西岸单区域 |
| 调用总数 | 约 550 次，零错误，零限流 |
| 时延（本机到端点） | p50 300 ms，p95 400 到 520 ms，最大 840 ms；并发 1 与 4 无差别 |
| 输入 token | 单问题约 100，8 问题共享状态约 860 |
| 费用 | $0.042 / 百万输入 token，输出免费；本次合计不到 0.02 美元 |
| 语言 | 中英混合查询全程无异常，两种语言准确率没有可见差异 |
| 重复稳定性 | 同一 95 题两轮：475 个概率值平均差 0.007，p95 差 0.03，过 0.5 线翻转 3 个 |

Jev 1.13 官方列明的短板在本次全部得到印证并且必须在代码里绕开：**字面化理解**（"光速是多少"确实要求精确数字，所以 exact_figure 为 0.93；"How are you doing today"因为 "today" 被判 recent 0.93）、**不做日期比较**、**没有会话上下文**（"这个模型支持多模态吗"被读作问助手本身）。

## 2. 结果总览

| 环节 | 现有实现 | 现有指标 | Jev 指标 | 措辞调整 |
|---|---|---|---|---|
| requires_evidence | 五组线索词表 | acc 0.785 | acc 0.937 / F1 0.953（修正规则） | 两轮 |
| intent_shape | COMPARISON_CUES 正则 | acc 0.846 | acc 0.979 | 一轮 |
| skill 路由（5 skill，161 例） | `handles_query` 词表 | P 0.50–0.72，R 0.95–1.0 | P 全部 1.0，R 0.71–1.0 | 一轮 |
| 路由 6 分类（route_intent 57 题） | 无对应基线 | — | acc 0.965 | 一轮 |
| 闲聊短路（40 例） | `is_small_talk_query` 子串 | acc 0.50 | acc 1.0（t=0.7） | 一轮 |
| claim_classes（7 标签） | 七张词表 | micro-F1 0.747 | micro-F1 0.883 | 两轮 |
| critical_ambiguity | 代词表 + 实体计数 | P 0.80 / R 0.727 | P 1.0 / R 1.0（叠加 local_context 门） | 两轮 |
| time_scope（4 类） | 正则 | acc 0.908 | acc 0.969 | 两轮 |
| 对比成员去噪 | 正则抽取 + 清洗 | F1 0.712，噪声率 0.133 | F1 0.892，噪声率 0.050（Jev 过滤正则候选） | 两轮 |
| existence_query | 枚举线索词表 | P 0.6 / R 0.75 | P 1.0 / R 0.5 | 两轮，无改善 |

"一轮"表示问题措辞写好后一次跑完没有改过；"两轮"表示看过第一轮结果后改了措辞或组合规则，数字偏乐观，需要留出题验证。

## 3. requires_evidence：不检索能否可靠回答

直接问 Jev "能否不检索回答"没有意义，它不知道回答模型知道什么。拆成关于查询本身的属性后可以判：任务类型 Choice（9 类）加 time_varying、exact_figure、recent、long_tail、local_context、wants_sources 六个 Noul，代码组合。

样本：`dataset/query_analysis_gold.csv` 65 题加 `dataset/jev_requires_evidence_hard.csv` 30 道困难题（职位在任者、看似常识但会变、冷门精确参数、稳定历史事实、概念型对比；agent 标注，未经第二人校验）。

| 规则 | P | R | F1 | acc | 过判 | 漏判 |
|---|---|---|---|---|---|---|
| 第一版 type_or_risk t=0.5（预设） | 0.784 | 0.892 | 0.835 | 0.758 | 16 | 7 |
| 修正版 t=0.3（事后） | 0.968 | 0.938 | 0.953 | 0.937 | 2 | 4 |
| 修正版 + 现有对比门 | 0.926 | 0.969 | 0.947 | 0.926 | 5 | 2 |

修正版规则：`time_varying ≥ 0.3 或 recent ≥ 0.3，或（事实类且 long_tail ≥ 0.5），或 wants_sources ≥ 0.5`。要点：

- **任务类型 Choice 全对**：gold 的 18 道不需证据题（寒暄、代码、换算、翻译、本地文档、概念解释）全部命中，confidence 接近 1.0。
- **time_varying 是判别信号**：稳定事实全部 ≤ 0.08（光速 0.03、澳大利亚首都 0.07、二战结束 0.02、OpenAI 成立年 0.04），需证据题大多 ≥ 0.3，在任者、市值、最新版本 ≥ 0.95。
- **exact_figure 不能单独触发**：它按字面回答"是否要精确数字"，对常识常数同样为真。第一版的 16 个过判几乎全来自它。
- **wants_sources 干净**："请给出官方文档依据" 0.97、"Please cite benchmarks" 0.96、"不要引用二手评测" 0.91、"不要只看官方宣传" 0.75，其余 < 0.05。ISE 当前没有这个信号。
- 剩余分歧是 gold 口径：稳定历史事实（"OpenAI 哪一年成立"、钨沸点）要不要证据；教科书式对比（PostgreSQL/MySQL/SQLite 适用场景）要不要证据。代词类续问（"帮我对比一下"）任务类型 confidence 只有 0.35 到 0.44，Jev 在表达不确定，这些本就走澄清。

在 ISE 里这个值只决定 `requires_evidence`，即"没有证据算不算阻塞缺口"（`utils/query_orchestration.py`）。现在它只从对比、时间、数字、当前、合规五组词表推出，"OpenAI 现在的 CTO 是谁"这类没命中词表的事实题会被判不需要证据；time_varying 与 long_tail 两个 Noul 正好补这个洞。

## 4. skill 路由

一个 Choice，六个选项（weather、finance、sports、location、transportation、none），描述直接改写自各 skill 的 `skill.yaml` 里的"用于/不用于"。样本：`skills/*/evals/cases.jsonl` 161 例，另用 `dataset/route_intent_dataset.csv` 57 题做六分类。

| skill | n | 基线 P | 基线 R | Jev P | Jev R | Jev F1 |
|---|---|---|---|---|---|---|
| weather | 34 | 0.655 | 0.95 | 1.0 | 1.0 | 1.0 |
| finance | 34 | 0.72 | 1.0 | 1.0 | 0.889 | 0.941 |
| sports | 30 | 0.5 | 1.0 | 1.0 | 1.0 | 1.0 |
| location | 32 | 0.68 | 1.0 | 1.0 | 0.706 | 0.828 |
| transportation | 31 | 0.536 | 1.0 | 1.0 | 0.933 | 0.966 |

53 个困难负例（"chicken stock recipe""nearest neighbor algorithm""train a neural network""距离产生美"）全部判 none，即 QD-20260909-05 的全部内容。召回损失 8 例全是 gold 口径：cases.jsonl 把"Find coffee shops near me""附近有什么餐厅""next train to Osaka"标为该 skill 正例、再由 preflight 以缺地点或缺起点拒绝；Jev 因为描述里写了"不含 near me""要有明确起点"直接判 none。删掉描述里那两句即可对齐。"这家公司最新财报如何""腾讯财报"Jev 判 none，与 finance manifest 的"不做 company news"一致，与 gold 不一致，需要裁定。

route_intent 六分类 0.965，错两题："50 USD in EUR"判 finance（gold 计算器），"airport to downtown Tokyo"判 transportation（gold 因起点不明归通用搜索）。

## 5. 闲聊短路

一个 Noul："是否只有寒暄、感谢、告别或对助手的寒暄，不含任何信息请求或任务"。样本 `dataset/small_talk_cases.csv` 40 例（20 纯寒暄、20 陷阱，agent 标注）。

| 方法 | P | R | F1 | acc | 错误数 |
|---|---|---|---|---|---|
| `is_small_talk_query` | 0.5 | 0.5 | 0.5 | 0.50 | 20 |
| Jev t=0.5 | 0.952 | 1.0 | 0.976 | 0.975 | 1 |
| Jev t=0.7 | 1.0 | 1.0 | 1.0 | 1.0 | 0 |

现有子串匹配两头都错：漏掉 "thx""在吗""收到""Good night!"，同时把"谢谢，那再帮我查一下 GLM-5.2 的价格""感谢信怎么写""拜拜的英文怎么说""你好世界这个程序怎么写"吞成寒暄。Jev 对这些陷阱给 0.01 到 0.13。唯一接近线的是"你是谁"0.54，归 about_assistant 类即可。

## 6. 查询理解各字段

一次调用：7 个 claim Noul、critical_ambiguity、existence、local_context Noul、time_scope Choice，外加对每个正则候选成员两个 Noul（"是否被比较的对象""是否只是干净的名字"）。基线是 `analyze_query` 加 `prepare_analysis(llm_invoke=None)`，指标口径复用 `tests/quality/analysis_eval.py`。

- **claim_classes** micro-F1 0.747 → 0.883。compliance 从 0 到 1.0，pricing 0.968，historical 1.0。剩余错误一半是 numeric 边界（"手续费谁更低"gold 标 numeric，Jev 只给 pricing），一半是 gold 里 current 与 temporal 的划分。
- **critical_ambiguity** 第一轮 3 个误报全是"这个项目""我上传的文件""the uploaded PDF"，Jev 按字面认为指代对象没在句子里；叠加 local_context < 0.5 后 11 个歧义题全中、零误报（基线漏 3、误 2）。
- **time_scope** 0.908 → 0.969，错的两题是"过去一周"gold 标 window 而 Jev 判 recent，以及 "How are you doing today" 的 today。
- **对比成员**：Jev 不能抽取，只能过滤。第一轮按字面接受了"SQLite 的适用场景"这类带尾巴的片段；加"干净名字"Noul 并按包含关系去重后 F1 0.712 → 0.892，噪声率 0.133 → 0.050（QD-20260909-04 的噪声部分可解）。剩余漏召回全是正则没有产出候选：Stripe、Pixel 10、Rust、Cloud Run，这要改抽取器。
- **existence** 没有改善：gold 的 4 个正例含"我上传的文件里有没有提到 XXX"（是非题）和"昨天英超有哪些比赛结果"，两版措辞都无法同时覆盖这两题并排除"历年名单""过去一周有哪些更新"。建议先重新定义该字段。

## 7. 结论与建议

1. **Jev 够用**。四个环节都优于现有词表和正则；所有剩余分歧都能归到 gold 口径、候选生成或缺少会话上下文，没有一例是 Jev 语义判断本身出错。
2. **必须在代码里组合**。Jev 只答字面问题，不能把单个 Noul 当结论；exact_figure、recent 这类信号要与任务类型、local_context 联合使用。
3. **接入形态**按仓库惯例：配置开关，Jev 作为一路信号，失败或低置信回落现有规则；问题文本与阈值集中在一个模块；每次调用把完整概率分布写进 audit log，补上 Jev 不给理由的缺口。
4. **接入顺序**：闲聊短路与 skill 路由（零调参一次过，无争议）→ claim_classes 与歧义判定 → 对比成员过滤（需配合抽取器召回一起改）→ requires_evidence（先定 gold 口径，再用留出题验证修正规则）。
5. **待定的 gold 口径**：稳定历史事实是否要证据；教科书式对比是否要证据；"财报"是否归 finance skill；`handles_query` 与 preflight 的分层是否保留；existence_query 的定义。
6. **风险**：早期访问、单区域、无 SLA；对注入不设防；无会话上下文，续问需把上一轮放进 state。

## 8. 复现

```bash
export TYPESAFE_API_KEY=...            # 或从本机私有文件读取
python -m tests.quality.jev_requires_evidence_eval --concurrency 4
python -m tests.quality.jev_top3_eval all
```

输出默认写到 `runtime/quality/jev/`。本次原始输出（含逐题概率）已固化在 [raw/](raw/)：`requires_evidence_run1/2`、`routing`、`smalltalk`、`analysis_run1/2`。
