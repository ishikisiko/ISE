# ISE 开发评测任务历史明细

> 2026-09-06 保存的旧版完整清单，仅用于追溯任务 ID、验收证据与范围调整。当前待办和优先级以 [plan.md](../../plan.md) 为准；下文的“当前”“必做”等表述均属于保存时的旧版计划。

> 当前状态：T01/T02 两题、执行评分闭环、人工评审与批次比较机制已交付（P0–P3-B）；主线剩余 P3-C05：使用说明与报告复核。纠错实验、扩题、实际 pilot 和正式成绩发布统一放在文末可选部分。
> 验证范围：pi 0.85.0 已完成真实 smoke 与一次 T01 全链路；Codex/Claude 未真实验证，`isolated-formal` 未达成，尚无 pilot 排名或真人评分。
> 创建日期：2026-09-05；范围精简：2026-09-06（PLAN-SLIM-01）。
> 设计依据：[系统分析](system_analysis.md) · [P0 决策](p0_decisions.md) · [CLI 调度设计](cli_orchestration.md)。
> 本计划含起点、参考提交和裁判资料，必须从被测 Agent 的公开任务包中排除。

## 首版范围与使用规则

首版只要求交付可按需使用的两题工具：**用户指定任务与 CLI/模型 → 启动执行 → 收卷 → 独立验收 → 查看报告**。人工加分与批次比较已有机制，是否实际使用由用户决定；单次诊断不要求先组织 pilot。

- `- [ ]` 表示未完成；产物落地且对应验证通过后才能改为 `- [x]`。设计、实现、自动验收、真人评分和模型实测分别记录。
- 保留稳定任务 ID、已有勾选与证据；迁移到可选部分只调整实施范围，不算完成，不放宽验收或修改评分权重。
- 新增任务先判断是否是上述两题流程的必要条件；增强功能、额外实验、扩充题库和发布运营默认放在文末可选部分，不自动成为首版依赖。
- 任务契约、预算、分值或版本实质变化时，先同步系统分析和任务包；原始提交、失败尝试与历史成绩不得覆盖或选择性删除。
- CLI/模型在实际使用时指定。实际启动前仍须满足对应 profile 的真实 smoke、运行授权与隔离要求；未验证的配置不能宣布可用，单次或条件不齐的结果只作诊断/暂定结果。
- 正式比较仍须冻结条件、完整预登记与保留全部结果；真实评分须由人填写。是否开展是可选的，一旦开展，相应规则必须遵守。
- 本计划不要求把候选或参考代码合入 ISE `main`，也不授权自动启动真实模型或对外发布。

## 主线与完成条件

| 范围 | 交付 | 当前状态 |
|---|---|---|
| P0 | 范围、评分与运行规则 | 已完成 |
| P1 | T01/T02 题包、参考实现、裁判与资格验证 | 已完成；任务 1.0.1，suite `ise-v1@1.0.0` 已冻结 |
| P2 | CLI 调度、收卷、独立评分与单次报告 | 机制验收完成；真实 CLI 验证范围见页首 |
| P3-A/B | 可选人工加分机制、批次登记与比较机制 | 功能交付完成，不要求先实际评分或组织 pilot |
| P3 收尾 | 使用说明与报告复核（P3-C05） | 待完成 |

主线完成条件：已有两题资格和机制验证可追溯，已验证的 CLI/profile 能走通执行到报告，P3-C05 完成。仅覆盖已注明的运行方式和 CLI；正式排名资格另按文末条件验收。

主线完成不要求预定参测名单、每题跑 3 次、填写真人分、开展纠错实验或发布成绩。参考实现满分、自动测试通过也不能当作模型成绩。

<a id="core-closeout"></a>

### 当前必做：使用说明与报告复核

- [ ] P3-C05 验证重生成报告不修改原始 run/submission/evaluation/review，并形成可复跑的操作说明。

本项从原 P3-C 独立保留。复用 P2-F/P2-G、P3-A/B 的命令与证据，只补齐实际入口、运行前检查、执行/停止、收卷/验收、查看/重生成报告的操作链及限制说明；批次比较与人工评分指向已有说明。用现有冻结提交或合成记录验证报告重生成与原始记录不变，不要求新增计费运行、真实评分或发布批次。

## 已完成任务明细

以下保留原任务 ID 与阶段验收记录，供追溯；历史段落描述的是当时验证范围，当前状态以页首和最新执行记录为准。

<details>
<summary>展开 P0–P3-B 已完成的 150 项任务</summary>

## P0. 设计与决策

- [x] P0-01 完成系统分析，覆盖目标、信任边界、分支、环境、验证、评分、评审和追溯。证据：系统分析第 1-12 章。
- [x] P0-02 整理 T01-T06 候选任务及历史起点、范围、分值、错误对照和准入待办。证据：系统分析第 13-14 章。
- [x] P0-03 校验系统分析中的本地链接、示例格式、历史 commit 及六题评分总和。证据：文末 `DOC-01` 验证记录。
- [x] P0-04 创建可勾选实施计划，并从系统分析与 README 建立入口。证据：本文件及对应链接。
- [x] P0-05 确认私有控制仓库、源快照、产物的位置与可信维护者，不把同用户不同目录当成隔离。证据：P0 决策第 2、9 节。
- [x] P0-06 确认正式目标 `isolated-formal`；任意 CLI 的 `local-practice` 可领取、提交和评分，但不进入正式排名。证据：P0 决策第 3 节。
- [x] P0-07 按最新澄清确定默认 `managed-cli` / `agent-native`，管理 Agent 通过启动器调用用户选定的 Codex/pi/Claude，手动入口备用；不重写模型 API。证据：P0 决策第 1、4、10 节及 CLI 调度设计。
- [x] P0-08 确认首批 T01/T02 时间与硬件配额，不设不可统一观测的调用上限；冻结依赖/澄清政策，本期不安排计费试跑，不代管外部 CLI 费用。证据：P0 决策第 5 节。
- [x] P0-09 确定初版功能 60 / 边界 25 / 回归 15，六项必过；人工 4/3/3 最高 10 分且仅 PASS 可加。证据：P0 决策第 6 节。
- [x] P0-10 确定维护者的独立资格复核责任、可选个人评审、短报告与脚本导入、实际盲评标记、日志权限和保留策略；不设模型自评。证据：P0 决策第 7、8 节。

**阶段完成条件**：首版部署目标、接入契约、预算与评审责任均有明确决定，无阻碍首批离线制题的未决规则；实际实现与验收不计入 P0。

P0 已完成的是设计决策，不是实际部署。P0-07/P0-08 按用户本轮澄清调整了范围，
不再以模型名单、专属 Agent 框架或计费授权阻塞离线准备；模型/CLI 是运行时输入，不是被遗漏的决定。
首版配额待 P1 离线资格测试验证，若需修改须在正式运行前修订版本；实际隔离和评审签署仍在后续阶段验收。
`p0-v2` 补充管理 Agent 的自动 CLI 调用，因此增加 P2-H；管理 skill、适配器和监督器均未实现。
本次不启动真实模型，后续自动调用 CLI 同样需要用户授权，不能把管理调度当成免费运行。

## P1. 两题准备与准入

依据：[参考实现与准入](system_analysis.md#task-qualification)、
[候选任务](system_analysis.md#candidate-tasks)。

### P1-A. 公共准备

- [x] P1-A01 建立私有 `ISE-devbench` 控制仓库与权限分离的源快照、产物区域；不向 Agent 暴露其凭据。
- [x] P1-A02 建立 suite 清单维护入口，约定 `bench/ise-v1`、不可覆盖发布标签及按题 base/reference/mutant 分支名称。
- [x] P1-A03 制定公开资料投影规则，排除参考实现、答案提交信息、系统分析、P0/CLI 调度设计、管理 skill、本计划、个人记忆、真实配置和运行数据。
- [x] P1-A04 为各历史 base 审核适用的项目规则与公开测试，不无条件附加未来版本的行为要求。
- [x] P1-A05 设计任务清单、公开 task.md、输入输出契约、公开样例、评分点与私有 checker 的统一模板。
- [x] P1-A06 固定记录 source commit、preparation patch、源文件清单和 base/public/reference/grader/environment 摘要。
- [x] P1-A07 为 T01、T02 构建并验证 `env1` 开发/验收镜像；记录 Python、系统库、依赖、浏览器和必要资源版本。
- [x] P1-A08 准备按 run 隔离的临时 SQLite、缓存、上传目录和浏览器 profile，不使用真实用户数据。证据：P1A-03；归属检查见 P1-A13。
- [x] P1-A09 准备固定内部 LLM、mock provider、时间与调度夹具；验收不调用真实搜索或真实应用 LLM。证据：P1A-03；管理通道见 P1-A14，时间冻结见 P1-A15。
- [x] P1-A10 明确 base 的预期通过回归和已知无关失败，冻结排除项，不在候选失败后临时追加豁免。证据：P1A-03，T01 370/370、T02 512/512，三次一致且 `clock_frozen=true`。
- [x] P1-A11 指定可信证据采集方式，预期值和评分不进入候选进程；需要白盒辅助的项目单独登记可信度。管理通道只对可信侧开放已由 P1-A14 落地。
- [x] P1-A12 制定资格报告模板，包含 base、reference、错误对照、独立复核、重复性、隔离和冻结检查结果。

复核缺口（P1A-02 提出，P1A-03 关闭）：

- [x] P1-A13 运行目录归属不得用字符串前缀判定。`check_run_workspace.py` 对符号链接目标与配置路径做解析后归属（`Path.resolve()` 再 `relative_to`）。验收：`run-1`→`run-10` 符号链接 `ok=false`；配置 `run-1/../run-10` `ok=false`；`measure_baseline.sh` 传入 `--config` 与 `--container-root /run`，`isolation-*.json` 的 `config` 非 null。
- [x] P1-A14 隔离 mock 管理通道。调用记录与重置在独立回环端口且要求令牌。验收：应用端口 `GET /__devbench/calls` 与 `POST /__devbench/reset` 返回 404；管理端口无凭据返回 401。
- [x] P1-A15 基线必须真正冻结时间。通过 `LD_PRELOAD=libdevbench_time.so` 冻结墙钟（不替换 `datetime` 类型，以免 pandas 在退出时 SIGSEGV）。验收：同镜像同启动参数下 `datetime.now()` 为 `2026-01-02 03:04:05`；T01/T02 在夹具生效后重测，exit=0。

**P1-A 阶段完成条件**：A01–A15 均有可追溯证据。已满足。题包、参考实现、裁判、正式隔离验收与模型实测仍未开始。

### P1-T01. 型号数字导致的引用误判

起点候选：`3ab634f`；历史参考：`280c003` 中与本题有关的修复。
完整 SHA 以系统分析和最终清单为准。只修引用误判，不把历史提交中的其他功能一起带入。

- [x] T01-A01 冻结型号、版本、单位、金额、数量、百分比和日期的输入域及正反例语义。证据：T01-QUAL-01，`tasks/T01/private/input-domain.md`。
- [x] T01-A02 写公开任务书与样例，不提示具体正则、内部函数或参考提交。证据：T01-QUAL-01，`tasks/T01/public/{task.md,examples.json,environment-notes.md}`。
- [x] T01-A03 导出净化后的 base，记录必要 preparation patch，并证明目标误判在该起点可复现。证据：T01-QUAL-01，base 308 文件、`preparation.patch`、复现记录。
- [x] T01-F1 实现并验证型号/版本单独出现不触发数值缺引用误报的验收点，20 分。证据：T01-QUAL-01，checker 通过，base 在此失败。
- [x] T01-F2 实现并验证型号与真实主张混合时仍检查真实数值的验收点，20 分。证据：同上。
- [x] T01-F3 实现并验证不同名称、大小写及约定分隔写法的隐藏变体验收点，20 分。证据：同上，硬编码对照在此失败。
- [x] T01-B1 实现并验证真实数值缺引用及不存在证据编号的反例，15 分。证据：同上。
- [x] T01-B2 实现并验证型号附近价格、单位和日期不会被一并吞掉的边界，10 分。证据：同上。
- [x] T01-R1 冻结并验证 authority、fetched、recency 相关回归集，15 分。证据：同上，11 条既有回归语义全保留，第三方标注不代替引用。
- [x] T01-A04 制作独立参考版本，用新验收验证，不把历史补丁直接视为正确 oracle。证据：T01-QUAL-01，独立编写的 `reference/citation_check.py`，100/PASS。
- [x] T01-A05 制作至少三类错误对照：关闭数字检查、跳过含字母句子、硬编码公开产品名。证据：T01-QUAL-01，三类对照均在预定验收点失败。
- [x] T01-Q1 证明 base 未满足目标、参考实现 100 分且 PASS、各错误对照在预定验收点失败。证据：T01-QUAL-01，base 60/FAIL，参考 100/PASS，对照 40/40/60 FAIL。
- [x] T01-Q2 完成独立复核或合理替代实现检查，排除内部函数名/文件布局等不必要绑定。证据：T01-QUAL-01，替代实现 100/PASS，复核记录（缺第二人会签，阻塞正式排名不阻塞冻结）。
- [x] T01-Q3 在干净环境重复资格验收至少 3 次，判定、分数和稳定字段一致，保存证据。证据：T01-QUAL-01，验收镜像无网络冻结墙钟下 3 次完全一致。
- [x] T01-Q4 完成净化与隔离检查，冻结契约、镜像、参考与裁判，将任务状态更新为 `frozen`。证据：T01-QUAL-01，泄漏扫描阻断 0，task.yaml 与 suite 条目均为 frozen。

**任务完成条件**：T01-Q1 至 T01-Q4 均有可追溯证据；写完参考代码或新增测试不等于准入完成。

### P1-T02. 会话 JSON 导出

起点候选：`2a1445c`。任务包含 API 与前端下载，不包含导入、批量导出或 checkpoint 备份。

- [x] T02-A01 冻结导出 endpoint、schema_version、标题、轮次、来源白名单及历史数据映射。证据：T02-QUAL-01，`tasks/T02/private/export-contract.md`。
- [x] T02-A02 明确无会话、存储不可用、缺字段、无自定义标题、坏历史 result 和 URL 脱敏的行为。证据：同上 §5–§6。
- [x] T02-A03 写公开任务书与样例，明确保留问答原文，不把内部字段禁止导出扩大成任意文本删减。证据：T02-QUAL-01，`tasks/T02/public/{task.md,examples.json,environment-notes.md}`。
- [x] T02-A04 导出净化后的 base，制作多会话、新旧轮次夹具与固定桌面/移动浏览器环境。证据：T02-QUAL-01，base 362 文件、四会话夹具、Chromium 151 双 viewport。
- [x] T02-F1 实现并验证导出 API 的 schema、会话身份与标题，20 分。证据：T02-QUAL-01，checker 通过，base 在此失败。
- [x] T02-F2 实现并验证问答顺序、中文与来源映射，20 分。证据：同上，M2 在此失败。
- [x] T02-F3 实现并验证浏览器下载当前会话、解析文件并对照内容，20 分。证据：同上，M3 在此失败，截图存档。
- [x] T02-B1 实现并验证缺会话、缺字段、存储关闭、请求失败和无伪下载，15 分。证据：同上，含拦截下载的错误态验证。
- [x] T02-B2 实现并验证多会话隔离与内部字段白名单，10 分。证据：同上，M1 在此失败。
- [x] T02-R1 实现并验证选择、重命名、删除和继续会话的回归流程，15 分。证据：同上。
- [x] T02-A05 制作符合现有 Flask 与原生前端结构的参考版本，完成自动功能验证与视觉复核。证据：T02-QUAL-01，export 模块+路由+条目按钮，100/PASS，桌面/移动/错误态截图。
- [x] T02-A06 制作至少三类错误对照：直接导出整个 result/数据库、仅导出屏幕可见轮次、下载旧会话 ID。证据：T02-QUAL-01，三类对照分别在 B2/F2/F3 失败。
- [x] T02-Q1 证明 base 未满足目标、参考实现 100 分且 PASS、错误对照被指定验收点识别。证据：T02-QUAL-01，base 0/FAIL，参考 100/PASS，对照 90/80/80 FAIL。
- [x] T02-Q2 独立复核导出契约与隐藏用例，不要求与参考版本相同的 DOM 层级或内部函数名。证据：T02-QUAL-01，直连 SQL 替代实现 100/PASS（含浏览器链）。
- [x] T02-Q3 在干净环境重复资格验收至少 3 次，包含下载、错误态与会话回归，保存日志和截图。证据：T02-QUAL-01，验收镜像无网络冻结墙钟下 3 次完全一致。
- [x] T02-Q4 完成净化与隔离检查，冻结题目、镜像、参考与裁判，将任务状态更新为 `frozen`。证据：T02-QUAL-01，泄漏扫描阻断 0，task.yaml 与 suite 条目均为 frozen。

**P1 完成条件**：两题均有完整三件套和资格报告；正式模型排名尚未开始，不能用参考实现成绩代替模型成绩。

## P2. 最小执行与评分闭环

依据：[信任边界](system_analysis.md#trust-boundaries)、
[分支设计](system_analysis.md#branch-design)、
[自动验收](system_analysis.md#verification)、
[运行接口](system_analysis.md#run-protocol)。

### P2-A. 数据契约与控制底座

- [x] P2-A01 建立独立框架项目骨架、测试目录与控制环境，不改 ISE 的默认运行链。证据：P2AB-01，`controller/devbench/`（10 模块）+ 3 组自测，`python -m devbench --help` 可用；ISE 产品代码未动（B06 守卫验证）。
- [x] P2-A02 实现 task、suite、run、submission、evaluation、review 的 schema 与稳定身份字段。证据：P2AB-01，`controller/schemas/*.schema.json`（6 份）+ `devbench/schemas.py`；schema 文档与校验器 required 一致（测试锁定）；占位摘要 `sha256:EXAMPLE` 被拒绝。
- [x] P2-A03 实现 `task validate`，拒绝重复 ID、权重错误、缺失 checker、未确定的 UNSET 和不完整发布清单。证据：P2AB-01，真实 suite 通过，6 类坏清单（重复 ID/权重/缺 checker/UNSET/版本不一致/无发布标签）均 exit=2；与旧工具同判定（交叉测试）。
- [x] P2-A04 实现 `draft -> qualifying -> frozen -> retired` 的任务状态与不可覆盖版本管理。证据：P2AB-01，`devbench/lifecycle.py`；合法/非法迁移各 4/6 组测试；frozen 同版本改摘要被拒、发新版本放行。
- [x] P2-A05 实现由控制器写入的运行状态、停止原因、时间戳和不可变产物索引。证据：P2AB-01，`RunStore` 全状态游走 + 时间戳单调 + 停止原因登记规则 + 产物索引只追加（无删除/覆盖接口）。
- [x] P2-A06 实现 run/evaluation ID 唯一性与重评追加记录，不覆盖旧提交、旧日志或旧成绩。证据：P2AB-01，重复 run_id/evaluation_id 拒绝；同提交两次验收产生不同 ID 且旧目录保留；提交冻结一次（同 digest 幂等，不同 digest 拒绝）。

### P2-B. 快照与分支隔离

- [x] P2-B01 从完整 source SHA 导出实际文件树，核对 `.gitattributes`、子模块、LFS、二进制资源和执行位。证据：P2AB-01，`snapshot export`；T01 306 / T02 360 文件与冻结记录一致，树检查全 clean；短 SHA/分支名拒绝。
- [x] P2-B02 以受控规则清理秘密、答案、参考信息、个人会话、真实数据库和跨 run 缓存。证据：P2AB-01，`sanitize` 复用投影/扫描唯一实现；真实 T01/T02 导出净化保留 165/184、泄漏阻断 0；合成树验证移除与阻断。
- [x] P2-B03 生成有序文件 manifest 与摘要，记录 base 准备补丁，验证恢复后内容完全匹配。证据：P2AB-01，与 P1 清单同一算法（摘要逐字节一致）；T01/T02 来源摘要与冻结记录一致；篡改后 `changed` 定位。
- [x] P2-B04 在考场新初始化只含净化起点的 Git，不配置原 remote、alternates 或共享对象库。证据：P2AB-01，`workspace init` 单一起始提交、无 remote/alternates；修复起点自带 `.gitignore` 丢文件问题（AGENTS.md，强制跟踪+计数核对+回归测试），重建后 306/306 完整。
- [x] P2-B05 验证考场无法读取原仓库对象、参考分支、reflog、stash、私有裁判和其他运行结果。证据：P2AB-01，干净考场通过；多提交/多余分支/stash/禁用标识/remote/目录包含各被独立捕获（CLI exit=1）。
- [x] P2-B06 验证导出与重建不会修改主工作区或将参考代码合入 ISE `main`。证据：P2AB-01，全实测在 `guard_repository` 下执行，HEAD + status 前后一致；考场为新初始化仓库，与主区无对象共享。

### P2-C. 环境、网络与进程隔离

- [x] P2-C01 将可信控制区、开发区、候选执行区分离，记录实际权限与挂载清单。证据：P2CDE-01，closed SandboxSpec + `mount_ledger` 随验收留存（`evidence/sandbox.json`）。
- [x] P2-C02 使用非特权执行，禁止宿主主目录、Docker socket、云凭据、SSH 凭据和宿主进程空间挂载。证据：P2CDE-01，默认 none/非 root/cap-drop；敏感清单与工作区创建同源，`$HOME` 默认禁；违规 argv 审计捕获。
- [x] P2-C03 实施公开的 CPU、内存、磁盘、进程数和时间上限，验证超限时的终止与归因。证据：P2CDE-01，cpus/memory/pids/分级超时 + 应用/设施超时区分 + 提交规模上限 + scratch 上限；per-container 磁盘配额依赖 daemon 未宣称。
- [x] P2-C04 实施网络出口白名单，覆盖重定向、DNS、代理绕过、云 metadata 与跨 run 服务访问。证据：P2CDE-01，正式强制 none（none 下上述向量均不可达），代理变量拒入环境，每次验收新端口/新库/独立卷。
- [x] P2-C05 验证正式 CLI 接入不暴露真实 provider 凭据；可选网关的本次连接限权且可撤销，验收区只有合成配置和 mock，不具备外层调用权限。证据：P2CDE-01，prepare 凭据扫描（已复核例外外未复核 secret_like 即拒）；网关令牌按次生成，验收记录调用数（T01/T02 均为 0）。
- [x] P2-C06 验证开发镜像与验收镜像具有一致的应用运行契约，资源和允许依赖可以离线重建。证据：P2CDE-01，`sandbox contract` 双镜像同探针一致（python 3.12、flask/pytest/torch/transformers 同版本）。
- [x] P2-C07 实现运行结束、取消与异常退出后的进程、端口、临时目录和浏览器清理，并检查残留。证据：P2CDE-01，`sandbox cleanup` 按 label 清理 + 残留复核；grade 结束自动清理。

### P2-D. `prepare` 与 `submit`

- [x] P2-D01 实现正式 `prepare`，只接受冻结任务和完整发布清单，不解析可移动分支头作为隐式起点。证据：P2CDE-01，未知任务/版本不一致/摘要不匹配/draft 任务均拒绝；base/公开包按摘要核验传入树。
- [x] P2-D02 创建本次独立工作区与 run manifest，交付公开任务书、环境和预算；不以模型名单、API key 或 CLI 专属适配器为领题前置条件。证据：P2CDE-01，任务书进 `task/`（不污染 diff），预算画像 `pilot-native-v1` 落地，缺省 participant 全 unknown。
- [x] P2-D03 实现正式开发预算监督，在首次交付可读任务前计时，截止时停止写入并冻结产物；无提交也形成有效失败记录。证据：P2CDE-01，交付即 RUNNING；late 提交冻结归档但标记，验收判 INVALID；预算状态可查询。
- [x] P2-D04 实现 `submit`，通过可信 base manifest 比较文件，而非信任可改写的 `.git` 或 `.gitignore`。证据：P2CDE-01，候选 `.git` 塞伪造历史不影响 diff；归因顺序（词法穿越→类型→目标归属）测试锁定。
- [x] P2-D05 收集新增、修改、删除、二进制和执行位，生成完整归档、diff、manifest 与 submission digest。证据：P2CDE-01，分类测试 + 归档解包核验 + 重放等价（base+归档==被测树）。
- [x] P2-D06 拒绝路径穿越、危险 symlink、设备文件、超限文件和未声明外部依赖。证据：P2CDE-01，越界链接/超限文件/锁外依赖各被拒绝（多提交场景独立用例）。
- [x] P2-D07 验证冻结后的归档不受 Agent 后续改动影响；新改动必须产生新的提交或运行记录。证据：P2CDE-01，提交后改动归档字节不变；同 digest 幂等、异 digest 拒绝覆盖。
- [x] P2-D08 区分手动操作者声明与 managed-cli 的 profile/探测/事件来源，记录 requested/reported 模型、unknown 设置、日志覆盖率和混合配置。证据：P2CDE-01，submission.json  participant 结构（当前 manual-external + unknown；managed-cli 来源待 P2-H 接入）。
- [x] P2-D09 实现练习与正式资格分流；本机自由运行不伪装成预算/答案隔离已验证，单次分数与正式排名资格分开保存。证据：P2CDE-01，练习运行 `ranking_eligible=false`，`artifacts/runs/p2cde-*` 可查。

### P2-E. `grade` 与 `task qualify`

- [x] P2-E01 用冻结镜像 + base + submission 重建执行环境，不复用开发环境、数据库、缓存或端口。证据：P2CDE-01，grade-work=base 全量+提交重放；mock 端口/库/卷每次新建；重放等价验证。
- [x] P2-E02 在可信控制区实现 HTTP/CLI/浏览器及公开 API 适配验收，禁止直接 import 候选 Python 模块。证据：P2CDE-01，裁判只在容器子进程跑；控制区 `sys.modules` 无候选模块断言；T02 浏览器链走通。
- [x] P2-E03 将隐藏预期、完整验收集和结果目录留在控制区；候选执行区只收到每次必要输入。证据：P2CDE-01，checks/fixture ro 挂载，结果回证据目录；T02 夹具仅挂载必需两文件（qualification/ 整体不进容器）。
- [x] P2-E04 由独立 mock gateway 记录实际调用，与候选 trace 对照，不接受其自报“未调用”作为证明。证据：P2CDE-01，容器内网关验收前后 reset/calls，记录随验收留存（本次调用数均为 0）；无候选 trace 时记 unavailable 不作证明。
- [x] P2-E05 检查预期 check ID、数量、重复、缺失、意外 skip 和提前终止，不将不完整验收计为通过。证据：P2CDE-01，六类残缺报告用例；缺失报告/裁判崩溃→INVALID。
- [x] P2-E06 验证候选 `conftest.py`、插件、PYTHONPATH、sitecustomize 和伪造 stdout 不影响可信判定。证据：P2CDE-01，容器 PYTHONPATH 仅时钟夹具；恶意树（sitecustomize 灌水+坏模块）只得 FAIL/INVALID，控制区存活。
- [x] P2-E07 实现 PASS/FAIL/INVALID 分类，区分候选构建失败、预算超限、设施故障和完整性违规。证据：P2CDE-01，分类矩阵测试；late/设施/无报告/不完整→INVALID，高分必过失败仍 FAIL。
- [x] P2-E08 使用 event/barrier 控制并发场景，记录应用超时与设施超时的独立证据。证据：P2CDE-01，分阶段计时 + 超时种类独立记录（本期无并发验收场景，barrier 待有并发题时接入）。
- [x] P2-E09 实现出题方 `task qualify`，重建 base/reference/mutants 并生成资格报告，不放开正式准入限制。证据：P2CDE-01，T01 六树全吻合（分数/判定/必败点）；T02 新路径验证参考全量 PASS（六树全量待 P2-G 补）。
- [x] P2-E10 实现统一重评流程，新 evaluation ID 绑定原 submission，grader 更新时可对保留提交一致重评。证据：P2CDE-01，同一提交 eval-001/002 双 PASS，旧记录字节一致；`GRADED→GRADING` 为显式唯一回边。

### P2-F. 自动评分与单次报告

- [x] P2-F01 实现按冻结行为点计分的 60/25/15 分组，不按动态测试条数增加权重。证据：P2FGH-01，`scoring.validate_weights` 精确校验，多余/缺失结果拒绝计分。
- [x] P2-F02 单独计算基础分与 required 硬门槛，验证高分但关键失败仍为 FAIL。证据：P2FGH-01，合成关键失败 40 分仍 FAIL，无通用及格线。
- [x] P2-F03 实现 no_change、无产物、部分完成和候选无法启动的诊断分处理。证据：P2FGH-01，no_change 保分判 FAIL（如 base 60 分标记），unstartable 记 0 FAIL。
- [x] P2-F04 对 INVALID 输出 null 基础分及明确原因，未知根因登记 triage_pending，保留后续归因修订。证据：P2FGH-01，`invalid_result`/`revise_triage` 修订链。
- [x] P2-F05 实现 JSON/Markdown 自动报告，记录分项、失败原因、代码/环境/裁判身份和证据定位。证据：P2FGH-01，双题端到端报告随验收留存。
- [x] P2-F06 区分开发时间、验收时间、等待和设施异常；费用/token 未采集时写 null，不写零。证据：P2FGH-01，`split_timing`/`cost_record`，G09 缺 usage 全 null。
- [x] P2-F07 实现明确退出码，区分“生成了一份 FAIL 报告”与“报告器运行失败”。证据：P2FGH-01，0/1/2/3（PASS/FAIL/INVALID 生成/报告器失败）实测。

### P2-G. 框架验收

- [x] P2-G01 用合成任务验证满分、部分分、关键失败、无提交、预算终止与设施错误。证据：P2FGH-01，合成任务容器实测（100/75/40 FAIL/无提交拒绝/late INVALID/坏镜像 facility）。
- [x] P2-G02 验证权重和错误、伪造版本/摘要、缺失 checker 和未冻结任务均被拒绝。证据：P2FGH-01，7 项拒绝测试。
- [x] P2-G03 验证新增/删除/二进制/执行位可复原，改 `.gitignore` 不会漏收候选源码。证据：P2FGH-01，归档往复 + `.gitignore` 忽略项仍收录。
- [x] P2-G04 验证泄漏参考代码、跨 run 读取、篡改结果、伪造 PASS 输出、删测试与 skip 不产生虚假通过。证据：P2FGH-01，六敌对向量无一 PASS。
- [x] P2-G05 验证不同模型配置、任务版本、环境、轨道和预算会被明确记录，不被隐式统一。证据：P2FGH-01，三种来源 participant 明确区分。
- [x] P2-G06 完成 T01、T02 的 prepare -> submit -> grade -> report 端到端验证，保存完整产物。证据：P2FGH-01，`runs/g6-t01-ref`/`g6-t02-ref` + 双格式报告。
- [x] P2-G07 对同一冻结提交至少重建验收 3 次，确认基础分、判定和稳定证据字段一致。证据：P2FGH-01，双题各 3×100 PASS 全一致。
- [x] P2-G08 完成 P1/P2 联合复核：两题准入有效、隔离实测有效、失败归因完整，才允许进入正式试跑。证据：P2FGH-01，`gate_p2.py` 门控开放。
- [x] P2-G09 用不调用真实模型的外部开发进程验证自由领题、未知模型信息、缺 usage、超时冻结和练习资格标记；离线测试不要求付费授权。证据：P2FGH-01，假 CLI 全链路 + late INVALID + 费用全 null。
- [x] P2-G10 用三种假 CLI 验证版本/参数拒绝、stdout/stderr 分片、JSONL 截断、假完成、usage 去重、注入防护和管理会话退出后的收尾。证据：P2FGH-01，23 项三 CLI 矩阵。

### P2-H. 管理 Agent、CLI 适配器与作业监督

依据：[CLI 适配契约](cli_orchestration.md#adapter-contract)、
[生命周期](cli_orchestration.md#lifecycle)。本节先完成离线实现，真实模型 smoke 见 P3-B11。

- [x] P2-H01 实现 profile/launch manifest schema，记录 CLI 版本/安装摘要、模型设置、公开 prompt、认证引用与授权；未冻结配置拒绝正式启动。证据：P2FGH-01，`profile check` 拒 draft/UNSET/未支持版本，示例 `profiles/pi-local.yaml`。
- [x] P2-H02 实现离线 `agent probe`，核对安装、帮助参数和支持版本；不读取/打印 auth，不把探测成功当成模型可用。证据：P2FGH-01，三假 CLI 探测 + 恒标注。
- [x] P2-H03 实现 Codex exec/json 适配器，固定 cwd、stdin、会话、权限与事件/退出解析；不使用失效或未支持参数。证据：P2FGH-01，argv 骨架 + 未知事件不终态。
- [x] P2-H04 实现 pi print/json 适配器，控制 provider/model、资源发现和事件去重；RPC 留作后续，不用额外 follow_up 指导解题。证据：P2FGH-01，usage 按消息去重。
- [x] P2-H05 实现 Claude print/stream-json 适配器，显式 verbose、工具许可和无交互策略；识别本机不支持的新参数并拒绝无提示降级。证据：P2FGH-01，`--permission-prompts` 等禁用参数审计捕获。
- [x] P2-H06 机械生成统一公开启动 prompt，通过 stdin 传输；拒绝管理对话、reference、隐藏反馈和自由追加解题指令。证据：P2FGH-01，追加指令/隐藏痕迹拒绝测试。
- [x] P2-H07 使用受控 argv、shell=False、cwd 和清洁环境；不拼接任意 shell 字符串，不继承宿主 HOME/登录/历史会话。证据：P2FGH-01，shell 拼接审计 + 凭据变量剥离。
- [x] P2-H08 冻结各 CLI 的新会话、插件/MCP/hooks/skills/搜索与子 Agent 策略，保证清洁模式不遗漏公开项目规则。证据：P2FGH-01，三 CLI 清洁 flags 断言。
- [x] P2-H09 实现 `run/status/logs/stop` 与持久监督器，默认 dry-run；管理 Agent 断线后仍执行截止和终止策略。证据：P2FGH-01，跨进程 CLI 冒烟（launch 后 tick 收割退出码 0）。
- [x] P2-H10 从控制区管道保存 stdout/stderr、OS 退出/信号、事件与覆盖率；CLI 自报完成和零退出码不直接判 PASS。证据：P2FGH-01，假完成零退出判 telemetry_incomplete。
- [x] P2-H11 实现进程树/容器终止与截止时冻结，清理宽限期不计入可修改提交时间，验证后台写入不能影响归档。证据：P2FGH-01，进程组 kill + stop_at 界定 + 后台写入验证。
- [x] P2-H12 实现 run ID 幂等、监督器重启状态恢复、原始尝试保留与有界日志；禁止重复启动和选择性重跑。证据：P2FGH-01，重复启动拒绝 + recover 认领不重启 + reaper 落盘。
- [x] P2-H13 实现用户执行授权与 profile/任务/次数/时限绑定，默认不调用真实模型；不得把任意授权字符串或设计请求当作计费许可。证据：P2FGH-01，缺省/伪造/过期/超次/绑定不符全拒绝。
- [x] P2-H14 在私有控制项目实现 `devbench-manage` 管理 skill，仅调用可信命令、展示报告，不进入考场、不自动评分或提供解题建议。证据：P2FGH-01，`management/devbench-manage.md`。
- [x] P2-H15 验证管理端对候选日志的提示注入防护、权限请求不会无限等待、自动 fallback/跨会话污染不会被记为同一配置运行。证据：P2FGH-01，注入只呈现不执行 + 混合配置单列。
- [x] P2-H16 编写三个适配器的非 TTY smoke 规程和证据模板；区分帮助探测、假 CLI、真实 CLI/mock provider 与真实 provider 验证。证据：P2FGH-01，假 CLI 矩阵通过；真实 CLI smoke 未做（P3-B11）。

**P2 完成条件**：不是只有 CLI 能启动，而是同一提交可干净复现、错误实现能被识别、评分无法被候选自报结果替代。
P2 离线验收完成后仍须如实标注各 CLI 的 live smoke 状态，未验证的实际 CLI/profile 不进入正式批次。

> P2 完成自检（P2FGH-01）：同一提交干净复现——双题各 3×判定/分数/验收点一致；错误实现识别——三对照 + 六敌对向量无一通过；自报不替代——伪造 stdout/假完成/零退出码均不判 PASS，报告由控制区 criteria 重算。live smoke：Codex 0.147.0 / pi 0.85.0 / Claude 2.1.257 均为假 CLI 验证，真实 smoke 见 P3-B11。

## P3. 人工评审与批次比较机制

依据：[评分口径](system_analysis.md#scoring)、系统分析第 4、11、12 章。

### P3-A. 人工评审

- [x] P3-A01 冻结人工 rubric：可维护性 0-4、范围控制 0-3、交付清晰度 0-3，细化各题解释。 证据：P3A-01。
- [x] P3-A02 实现 review schema，绑定 task/version、submission、evaluation、rubric、评审者和证据。 证据：P3A-01。
- [x] P3-A03 只允许 PASS 且完整性合格的提交加分，不以人工分改变自动 verdict。 证据：P3A-01。
- [x] P3-A04 校验分项范围、证据位置和身份匹配；总分由可信脚本求和，不读取自填总分。 证据：P3A-01。
- [x] P3-A05 区分未评审 null、评审后 0 分和不具资格，未评审记录不混入个人总分排名。 证据：P3A-01。
- [x] P3-A06 实现评审追加修订，代码或自动评估变化后要求重新确认，不覆盖旧记录。 证据：P3A-01。
- [x] P3-A07 实施隐藏模型/CLI 身份的评审包并记录真实盲评程度；首版单一指定评审者，未来多人采用预先冻结规则。 证据：P3A-01。
- [x] P3-A08 验证越界分数、无证据、不匹配提交、FAIL 加分和旧评审复用均被拒绝。 证据：P3A-01。
- [x] P3-A09 实现 `review prepare`，生成短报告、diff、必要截图、证据索引和预填身份的空评分表；无模型自评或自动赠分。 证据：P3A-01。
- [x] P3-A10 实现 `review record`，接受人填写的三个分项、短理由和证据 ID；支持助手代录并记录实际评审者与代录来源。 证据：P3A-01。
- [x] P3-A11 验证报告转义、禁用候选脚本/外链、证据路径边界与预览隔离；发现裁判争议时暂停排名并进入独立复核。 证据：P3A-01。
- [x] P3-A12 实现日志/提交 90 天、脱敏报告等至少 1 年的保留策略与显式清理审计，证据删除后标记不可复跑。 证据：P3A-01。

**P3-A 完成条件**：A01–A12 的实现与验证已满足。控制仓库全自测 318 项通过（P3-A 新增 88 项），
双题现有产物已生成匿名静态包与空表，隔离浏览器安全实测通过。当前真人评分仍为 `null`，
测试中的分数、代录身份、争议签署和清理均为临时合成数据；未启动真实模型或正式批次。
详情：私有 `controller/docs/p3a-summary.md`；验证：`artifacts/qualification/p3a-validation.json`。

### P3-B. 自选 CLI 运行与比较

- [x] P3-B01 用户组织批次时登记 CLI/模型配置和可观测设置，固定同一参测对象的重复条件；默认 agent-native，不声称内部工具完全相同。 证据：P3B-01。
- [x] P3-B02 验证个人记忆、历史会话和跨运行缓存被清空；子 Agent 策略按冻结配置执行。 证据：P3B-01。
- [x] P3-B03 冻结两题 pilot suite，实现按用户届时指定的 CLI/模型配置预登记每配置每题 3 次计划的机制，不按结果选择性增加或删除运行。证据：P3B-01、P3B-SCOPE-01。
      suite `ise-v1` 已冻结为 1.0.0（`release_tag: suite/ise-v1.0.0`，摘要锁 `suites/frozen.json`，登记时核对漂移），
      预登记机制已实现并验证（写一次、不得重生成、计划外运行不入汇总）。本项验收 suite 与机制；实际批次的名单、登记与运行在使用时确定。
- [x] P3-B04 用户批准批次后由管理 Agent 经启动器调用选定 CLI，在冻结时间/资源条件下执行；外部费用仍由 CLI/provider 结算，保存全部成功与失败。 证据：P3B-01。
- [x] P3-B05 按统一规则归因并补齐设施无效运行；未补齐时只显示暂定结果，不利用较小分母排名。 证据：P3B-01。
- [x] P3-B06 实现按任务等权的平均基础分和开发可靠性分，同列关键回归数。 证据：P3B-01。
- [x] P3-B07 单独报告时间、可获取费用、token 捕获率、人工介入及验证声明可信度；缺失为 null，不把部分账单宣称为完整成本。 证据：P3B-01。
- [x] P3-B08 实现 `compare`，拒绝不同 suite、轨道、执行方式、版本、预算或重复数的无提示混排。 证据：P3B-01。
- [x] P3-B09 将客观榜与个人加分榜分开，保留原始分项和未评审状态。 证据：P3B-01。
- [x] P3-B10 复核小样本结论，不将数分差距写成显著能力差异或通用 LLM 排名。 证据：P3B-01。
- [x] P3-B11 在正式题目前对实际使用的 CLI/profile 完成经授权的隔离 smoke，验证认证、读写、自测、终止和收卷；没测的 CLI 标记未验证，不用假 CLI 结果替代。 证据：P3B-01（pi 0.85.0 五项通过；codex、claude 仍为 `unverified`，新增参测 CLI 必须先补 smoke）。

**P3-B 现状**：B01–B11 的功能交付已完成；B03 按用户澄清以 suite 冻结与预登记机制为完成条件（P3B-SCOPE-01）。
一次真实授权执行走通全链路（pi PASS、基础分 100、费用 0.00977 USD），三次设施无效均已归因、保存并补跑。
尚无 pilot 成绩：批次为 `repeats=1` 的机制验收批次，`local-practice`，不进入任何排名。
Codex/Claude 仍未真实验证，`isolated-formal` 尚未达成；功能交付勾选不扩大现有验证范围。
详情：私有 `controller/docs/p3b-summary.md`；验证：`artifacts/qualification/p3b-validation.json`。

</details>

## 执行记录

按记录日期保留历史结论，旧记录中的“未开始/未完成”不表示当前状态。新增完成项或范围调整时追加记录；不得在公开文档内粘贴凭据、完整隐藏样例或用户数据。

<details>
<summary>展开执行与范围调整记录</summary>

| 记录 ID | 日期 | 对应事项 | 产物与验证 | 状态 |
|---|---|---|---|---|
| PLAN-SLIM-01 | 2026-09-06 | 首版主线与末尾可选事项 | 按用户要求保留两题执行、评分、报告与按需评审/比较机制；主线保留 P3-C05 使用说明与报告复核。P3-C01–C04、P4、P5、正式发布检查移至末尾可选部分；保留全部原任务 ID、勾选状态和证据，实际 pilot/真人评分改为使用时按需开展；同步系统分析推进顺序与 README 入口 | 仅调整计划，不代表可选项已实现或实际评测已完成；各 CLI 的真实验证、正式隔离、独立复核和可比性门槛继续生效 |
| P3B-SCOPE-01 | 2026-09-06 | P3-B03 完成口径与 P3-B 状态 | 按用户澄清，CLI/模型配置在实际使用时指定；B03 调整为 suite 冻结与预登记机制验收，依据已有 P3B-01 验证勾选。实际 pilot 登记和运行单列为使用阶段待办 | P3-B 功能交付完成；仅调整任务范围与文档，未新增测试或模型运行。原 P3B-01 的 pilot 未组织、CLI 验证范围与隔离限制仍有效 |
| DOC-01 | 2026-09-05 | P0-01 至 P0-04 | 系统分析、本计划、README 入口；使用 env1 校验文档链接、锚点、JSON/YAML、历史 commit、六题分值及 checklist ID；检查空白错误 | 文档验证完成，不代表实现或模型评测完成 |
| P0-DEC-01 | 2026-09-05 | P0-05 至 P0-10；同步修订 P0-07/P0-08 与后续事项 | 新增 P0 决策记录；按用户澄清改为自选 CLI、离线准备、无模型自评；只读核对 Docker、主机资源与 env1；env1 文档校验通过：51 个本地链接、3 个结构示例、7 个历史 SHA、六题各 100 分、196 个唯一 checklist ID（仅 P0 的 10 项已勾选），无空白错误 | P0 决策与文档验证完成；未部署、未制题、未做隔离实测或计费试跑 |
| CLI-DESIGN-01 | 2026-09-05 | 修订 P0-07，新增 P2-H/P2-G10/P3-B11 | 新增 CLI 调度设计并同步系统分析与 P0；核对三者官方文档、本机帮助及版本：Codex 0.147.0、pi 0.85.0、Claude 2.1.257；env1 校验 4 份文档、62 个本地链接/锚点、4 个结构示例、7 个历史 SHA、三种命令骨架参数、214 个唯一 checklist ID（仅 P0 的 10 项已勾选）通过 | 调研与文档验证完成；适配器/管理 skill 尚未实现，无真实 CLI smoke 或付费调用 |
| P1A-01 | 2026-09-05 | P1-A01 至 P1-A12 | 建立 `/home/ubuntu/.local/share/ise-devbench/{controller,source.git,artifacts,runtime,prep}`（0700、控制仓库无 remote、源快照为裸仓库）；控制仓库首个提交含 suite 清单与校验、公开投影规则与投影/泄漏扫描工具、任务与资格报告模板、快照清单与来源记录、env1 三镜像与自检、运行隔离与 mock/时钟/调度夹具；实测：控制仓库自测 24 项通过；两个 base 导出 306/360 文件且清单摘要可复算；公开投影保留 165/184 文件、泄漏扫描阻断项 0（已复核例外 15）；镜像自检在 `--network none` 下全部通过（Python 3.12.13、torch 2.11.0+cpu、Chromium 151、内置 all-MiniLM-L6-v2、UID 10001、`requirements.txt` 23 条声明全部满足、dev/grade 的 pip freeze 摘要一致）；净化树加 preparation patch 在验收镜像内重复 3 次：T01 370/370、T02 512/512 全通过且失败集合一致 | 部分完成，已被 P1A-02 否决 A08/A09/A10。题包、参考实现、裁判、正式隔离验收与模型实测均未开始。发现并处理了 base 锁文件与依赖声明冲突（langgraph/langchain-classic 版本、langgraph-checkpoint-sqlite 缺失），以及公开测试依赖未跟踪的 `config.json`；`base/public/reference/grader/environment` 摘要在对应产物制作前保持 UNSET |
| P1A-02 | 2026-09-05 | P1-A08、P1-A09、P1-A10、P1-A13 至 P1-A15 | 复核否决 P1A-01 对公共准备完成的声明。1) `check_run_workspace.py` 用 `str.startswith` 判断目录归属：`run-1` 内符号链接指向 `run-10`、配置路径含 `..` 逃至 `run-10` 均返回 `ok=true`；`measure_baseline.sh` 未传 `--config`，T01/T02 的 `isolation-*.json` 均为 `config: null`。2) `mock_llm_server.py` 将 `/__devbench/calls` 与 `/__devbench/reset` 与应用接口放在同一无鉴权端口；无凭据读取与重置均 200，记录数从 1 变为 0。3) `measure_baseline.sh` 只设置 `DEVBENCH_FROZEN_TIME`，不加载冻结夹具；同镜像同启动参数下配置 `2026-01-02 03:04:05`，`datetime.now()` 仍为 `2026-09-05`。现有三次通过不能证明固定时间条件。已将 A08/A09/A10 改回未勾选 | 已被 P1A-03 关闭 |
| P1A-03 | 2026-09-05 | P1-A08 至 P1-A15 | 路径归属改为 `resolve`+`relative_to`；基线传入 `--config` 与 `--container-root /run`。mock 管理通道改独立回环端口+令牌。时间冻结改 `LD_PRELOAD=libdevbench_time.so`（替换 `datetime` 类型会使 pandas 以 139 退出）。控制仓库自测 29 项通过。复测：`run-1`→`run-10` 符号链接与 `run-1/../run-10` 配置均为 `ok=false`；应用端口 calls/reset=404，管理端口无令牌=401。T01/T02 基线在冻结墙钟下各 3 次，exit=0，`datetime.now()=2026-01-02 03:04:05`，T01 370/370、T02 512/512，`isolation-*.json` 的 `config` 非 null | 公共准备关闭。题包、参考实现、裁判、正式隔离验收与模型实测仍未开始 |

| T01-QUAL-01 | 2026-09-05 | P1-T01 全项（A01–A05、F1–R1、Q1–Q4） | 输入域冻结（字母数字混写段视作标识，第三方标注不代替引用）；公开任务书与样例；base=源快照+占位 config.json/PUBLIC_ENVIRONMENT_NOTES.md（308 文件，误判复现）；独立参考实现（只改逐 token 剥离）100/PASS；三类错误对照 40/40/60 FAIL 且失败点符合预定；替代实现 100/PASS；验收镜像无网络冻结墙钟下 3 次完全一致；投影保留 167/阻断 0；task.yaml 与 suite 条目 frozen（v1.0.0） | T01 冻结完成。独立复核缺第二人会签（阻塞正式排名）；environment_digest 为 interim 合成（P2 替换）；模型实测仍未开始 |
| P3B-01 | 2026-09-06 | P3-B01、B02、B04–B11（B03 部分）；suite `ise-v1` 冻结 | 新增 `devbench/batch.py`（登记/冻结/预登记/结局/补跑/介入/声明核对）、`hygiene.py`（HOME 与会话目录清洁、作用域环境变量按挂载表回溯、共享缓存只读、子 Agent 策略双向核对）、`batch_exec.py`（前置门控→prepare→沙箱内启动→监督→收卷→验收→报告；CLI 本体复制进受控暂存区只读挂载；受限凭据只放本 provider 并标注可被候选读取；从事件日志去重提取 token/费用）、`aggregate.py`（任务等权平均基础分与开发可靠性分、关键回归、辅助指标、compare 兼容键、客观/个人双榜、小样本封闭词表）、`manage/smoke.py`（真实 smoke 登记与门控，假 CLI 路径拒绝登记）与 `batch/aggregate/compare/smoke` CLI；suite 由 `0.1.0-draft` 冻结为 `1.0.0`（实测 `environment_digest`、`release_tag: suite/ise-v1.0.0`、`suites/frozen.json` 摘要锁）。实测：全自测 400 通过（新增 82）；`tools/smoke_pi.py --execute` 用 pi 0.85.0 + `opencode-go/muse-spark-1.3-contributor` 在容器内完成认证/读/写/自测/终止/收卷五项（自测由控制器在只读代码树上独立复跑确认，终止后 SIGTERM 143 且 label 无残留容器）；`tools/accept_p3b.py --execute` 在机制验收批次 `p3b-m1` 上跑通全链路，T01 PASS/基础分 100、开发 285 秒、0.00977 USD、722,116 token（捕获率 1.0），T02 缺结果故汇总为 `provisional`。证据 `artifacts/qualification/p3b-validation.json`、`p3b-smoke-pi.json`、`p3b-acceptance.json`、`p3b-pytest.xml` 与 `controller/docs/p3b-summary.md` | 机制完成，不是 pilot 成绩：批次 `repeats=1`、`local-practice`、不入排名。执行过程中发现并修复三处编排缺陷（重复了 `prepare`/`submit`/`grade_submission` 自带的状态迁移），三次均按设施无效归因、保存并补跑，原记录未删。仅 pi 完成真实 smoke，codex/claude 为 `unverified` 且凭据注入方式未验证；开发阶段需 provider 网络，未达成 `isolated-formal`；凭据与被测代码同沙箱可被读取；人工分仍为 null；pilot 名单未定，P3-C 未开始 |
| P3A-01 | 2026-09-06 | P3-A 全项（A01–A12） | 私有 controller 冻结 manual-v1（4/3/3、双题解释）；v2 schema、匿名 prepare/record/status、完整性/证据/身份校验、追加修订与重新确认、可信求和、实际盲评/代录来源、独立争议暂停、90/365 天保留和显式清理审计；env1 全自测 318 通过（新增 88）；`python tools/accept_p3a.py` 用现有 g6-t01-ref/g6-t02-ref 生成空表且 run/evaluation 字节未变，新隔离浏览器中两题/恶意报告活动元素与外部请求均为 0、T02 三图加载、CSP 阻断脚本；证据 `artifacts/qualification/p3a-validation.json`、`p3a-pytest.xml`、`p3a-acceptance.json`、`p3a-preview/` 与 `controller/docs/p3a-summary.md` | P3-A 完成的是评审基础设施与验收；实际人工分保持 null，无真人签署或正式模型运行；清理仅在临时合成目录实测。P3-B 尚未实施 |
| P2FGH-01 | 2026-09-06 | P2-F 全项（F01–F07）、P2-G 全项（G01–G10）、P2-H 全项（H01–H16） | 计分报告器（分组预算/required 门槛/诊断分/INVALID 修订/退出码 0/1/2/3）；合成/敌对/外部/矩阵/门控验收；双题端到端 + 各 3×稳定 + 双格式报告；profile/probe/三适配器/prompt/监督器（reaper 跨进程收割）/授权/skill；自测 230 通过；证据 `artifacts/qualification/p2fgh-acceptance.json` + `rebuild-t02.json` + `controller/docs/p2fgh-summary.md` | P2 离线完成。真实 CLI smoke 未做（P3-B11）；suite 仍 draft；无正式排名结果 |
| P2ENV-01 | 2026-09-06 | environment_digest 统一算法替换 interim 合成 | E1 算法（镜像 ID + 锁文件 + 冻结墙钟 + 沙箱/时钟策略）；T01/T02 1.0.0→1.0.1 修订（仅版本/状态/环境摘要），六树资格与旧版完全一致后翻 frozen；验收前实采核对，漂移即设施 INVALID | interim 已替换；旧 1.0.0 摘要保留为历史记录 |
| P2CDE-01 | 2026-09-06 | P2-C 全项（C01–C07）、P2-D 全项（D01–D09）、P2-E 全项（E01–E10） | 沙箱 closed 规约+审计+清理+镜像契约；prepare 仅 frozen/交付计时/凭据扫描；submit manifest diff/归档/完整性拒绝/late 标记；grade 容器裁判/完整性/分类/网关记录/重评；qualify T01 六树吻合，T02 参考新路径 PASS 100；自测 115 通过；证据 `artifacts/qualification/p2cde-*.json` + `controller/docs/p2cde-summary.md` + 3 个练习运行 | C/D/E 离线完成（当时版本；后续被 P2ENV-01 修订为 1.0.1） |
| P2AB-01 | 2026-09-05 | P2-A 全项（A01–A06）与 P2-B 全项（B01–B06） | `devbench/` 框架 10 模块 + `schemas/` 6 份契约 + 54 项自测（全自测 83 通过）；`task validate` 与旧工具同判定；运行状态机/产物追加/ID 唯一/重评追加；真实 SHA 实测 T01 306 / T02 360 文件、来源摘要一致、泄漏阻断 0、考场 306 文件单提交隔离通过；修复起点 `.gitignore` 丢文件并加回归测试；证据 `artifacts/qualification/p2ab-acceptance.json` + `controller/docs/p2ab-summary.md` | P2-A/B 离线完成。P2-C/D/E/F/G/H 未开始；无正式运行结果，`artifacts/runs/` 为空 |
| T02-QUAL-01 | 2026-09-05 | P1-T02 全项（A01–A06、F1–R1、Q1–Q4） | 导出契约冻结（schema_version=1、三/四/两键白名单、历史缺来源记空数组、URL 去 fragment、404/503、无伪下载）；公开任务书与样例；base=源快照+占位配置（362 文件，无导出端点）；参考实现（export 模块+路由+条目导出按钮）100/PASS；三类错误对照 90/80/80 FAIL 且失败点符合预定（B2/F2/F3）；直连 SQL 替代实现 100/PASS（含浏览器链）；验收镜像无网络冻结墙钟下 3 次完全一致（含 Chromium 151 双 viewport 下载、错误态截图、服务日志）；投影保留 186/阻断 0；task.yaml 与 suite 条目 frozen（v1.0.0） | T02 冻结完成，P1 两题齐备。残留同 T01-QUAL-01；模型实测仍未开始 |

后续记录建议包含：任务 ID、代码/任务版本、执行命令、run/evaluation ID、结果摘要、证据位置、残留问题。

</details>

<a id="optional-work"></a>

## 可选事项（按需启动，不阻塞首版）

以下保留原任务 ID 和未完成状态，便于以后按需取用；不要求全部实施，也没有固定的 P3-C → P4 → P5 排期。详细题目清单折叠保留，避免现在展开无关工作。

### 使用时再决定：CLI、pilot 与个人加分

- **单次测试**：届时指定任务、CLI/模型与预算即可。新增 CLI/profile 先补齐适配或凭据注入能力并通过真实 smoke；已有适配代码或帮助探测不代表真实可用，已验证配置是否可复用按绑定信息检查。
- **pilot 比较**：需要比较时，再执行 `batch register/freeze/preregister`，在运行前固定每配置每题 3 次；按授权执行、统一归因与补跑，未补齐只报暂定结果，不排名。新批次可另选配置。
- **个人加分**：需要时使用已有 `review prepare/record/status`，由真人填写并保留证据；跳过时为 `null`，不影响客观判定。

### P3-C. 一轮纠错与初版发布

- [ ] P3-C01 区分需求澄清与实现纠错；确定只反馈问题现象、不提供隐藏测试或修改方案的统一协议。
- [ ] P3-C02 从初次提交派生独立纠错 run，记录父提交、额外预算与修复结果，不覆盖首次分数。
- [ ] P3-C03 验证反馈改变需求时升级任务版本，不只为单个模型添加新条件。
- [ ] P3-C04 发布两题结果、版本清单、隔离级别、剩余限制与证据索引；私有答案和敏感日志不随报告公开。

只在需要衡量“收到反馈后能否修好”或交付首份成绩报告时启动。C01–C03 是纠错机制，C04 是实际成绩发布；P3-C05 已移至[主线收尾](#core-closeout)。首次成绩始终保留，新增需求仍按版本规则处理；正式成绩发布还须通过本节末尾的发布检查。

### P4. 扩展题库（T04、T03）

<details>
<summary>需要扩题时展开 T04/T03 任务清单</summary>

需要扩大任务覆盖时再启动，建议先 T04，再 T03。新题独立准入后冻结新 suite，不能把两题和四题 suite 的总分直接混排。

#### P4-T04. 请求级检索工具限制

起点候选：`2a1445c`。

- [ ] T04-A01 冻结 disabled_tools、CLI 参数、工具名称集合、错误语义及已知但不可用工具的处理。
- [ ] T04-A02 明确公开工具身份与 provider 资源的区别、已有 search off 限制、普通追问与澄清的范围。
- [ ] T04-A03 制作公开任务包、净化 base、可归因 mock 与受控并发夹具。
- [ ] T04-F1 实现并验证 CLI、普通 HTTP、流式 HTTP 参数接收与传递，20 分。
- [ ] T04-F2 实现并验证限制后的工具面与其他允许工具的可用性，20 分。
- [ ] T04-F3 实现并验证模型尝试禁用工具时执行端阻断，20 分。
- [ ] T04-B1 实现并验证未知名称、非法输入、空值与重复名称，15 分。
- [ ] T04-B2 实现并验证连续、并发及同会话普通后续请求不串配置，10 分。
- [ ] T04-R1 实现并验证自主度、预算、preflight 与既有搜索限制的回归，15 分。
- [ ] T04-A04 制作参考版本及错误对照：只改提示词、遗漏流式入口、修改共享集合、恢复时解除禁用。
- [ ] T04-Q1 完成 base/reference/mutants 对照、独立复核和干净环境至少 3 次重复资格验收。
- [ ] T04-Q2 冻结版本与摘要，完成隔离核验后纳入新的 suite；实际模型运行在用户组织该 suite 的比较时进行。

#### P4-T03. 文献元数据检索 Skill

起点候选：`2a1445c`。

- [ ] T03-A01 冻结模拟服务协议、鉴权、字段、错误码、query 输入域和结果/时间/调用预算。
- [ ] T03-A02 明确工具名、query 契约、配置可用性、证据来源类型与进入既有 registry 的要求。
- [ ] T03-A03 制作同协议不同数据的开发/验收 mock、公开任务包、净化 base 与 scripted LLM 场景。
- [ ] T03-F1 实现并验证配置齐备时按既有 registry 注册并暴露工具契约，20 分。
- [ ] T03-F2 实现并验证请求协议与多响应变体的规范化，20 分。
- [ ] T03-F3 实现并验证结果进入实际 loop、证据、来源和审计链，20 分。
- [ ] T03-B1 实现并验证缺配置、非法参数、空结果与服务异常，15 分。
- [ ] T03-B2 实现并验证调用/时间/条目上限及鉴权数据不进入公开输出，10 分。
- [ ] T03-R1 实现并验证既有 skill 可用性、preflight 与执行回归，15 分。
- [ ] T03-A04 制作参考版本及错误对照：脱离 registry、未接入主流程、非法参数发请求、预算仅写说明。
- [ ] T03-Q1 完成 base/reference/mutants 对照、独立复核和干净环境至少 3 次重复资格验收。
- [ ] T03-Q2 冻结版本与摘要，完成隔离核验后形成四题 suite；实际模型运行在用户组织该 suite 的比较时进行。

**选择实施后的完成条件**：新增题目分别准入，新 suite 的任务集合、版本与摘要冻结。实际比较时另行固定轨道、预算和重复次数，不混用旧总分；不要求制题时确定模型名单或跑出成绩。

</details>

### P5. 高难挑战与连续开发

<details>
<summary>需要挑战题或连续开发时展开 T05/T06 与储备清单</summary>

T05、T06 单列挑战成绩；加入主榜需要新 suite。连续开发链另设轨道，不作为完成首版的前置条件。

#### P5-T05. 取消执行与会话续用

起点候选：`7e06917`；参考来源：`1a512ed` 中的取消能力，不整体复现自主度和澄清。

- [ ] T05-A01 冻结 run ID、取消接口、SSE、界面交互、部分结果和取消终态的契约。
- [ ] T05-A02 明确安全节点边界、当前批次已启动工具的结算、重复取消和恢复语义，不要求强杀 HTTP。
- [ ] T05-A03 制作净化 base、checkpoint 夹具、锁/event/barrier 调度与实际消息配对观察。
- [ ] T05-F1 实现并验证 run ID、取消 API 与界面停止操作的闭环，20 分。
- [ ] T05-F2 实现并验证安全边界取消及已有证据保留，20 分。
- [ ] T05-F3 实现并验证取消后同会话恢复并完成新请求，20 分。
- [ ] T05-B1 实现并验证预取消、结束后取消、未知 ID 与重复取消，15 分。
- [ ] T05-B2 实现并验证无锁死结、实际 checkpoint 配对及 registry 清理，10 分。
- [ ] T05-R1 实现并验证正常问答、流式结束、会话恢复与审计回归，15 分。
- [ ] T05-A04 制作参考版本及错误对照：仅停止显示、取消端等待会话锁、丢工具结果、恢复状态损坏。
- [ ] T05-Q1 独立复核深状态契约，完成三类对照及不同调度序列的重复验收，不依赖偶然 sleep。
- [ ] T05-Q2 完成隔离与版本冻结，明确标记为挑战任务；难度和成本校准在用户选择挑战实测时进行。

#### P5-T06. 上下文压缩与可靠恢复

起点候选：`27bedf7`；参考来源：`d88d28b`，历史验证不能替代新准入。

- [ ] T06-A01 冻结 token 估算/观测、触发点、预算预留、保护区间、压缩次数及降级协议。
- [ ] T06-A02 明确工具配对、证据召回、持久化恢复与不可压缩输入的边界，不按摘要文采计分。
- [ ] T06-A03 制作净化 base、固定 token/模型响应、ledger、失败注入及跨请求 checkpoint 夹具。
- [ ] T06-F1 实现并验证阈值触发、预算预留及未超阈值不压缩，20 分。
- [ ] T06-F2 实现并验证消息配对、必要近轮保留及证据可召回引用，20 分。
- [ ] T06-F3 实现并验证压缩后继续执行与 checkpoint 恢复，20 分。
- [ ] T06-B1 实现并验证摘要失败、未知证据引用与 usage 缺失降级，15 分。
- [ ] T06-B2 实现并验证次数有界、终态优先、不可压缩输入与遥测脱敏，10 分。
- [ ] T06-R1 实现并验证预算、正常会话、证据和审计回归，15 分。
- [ ] T06-A04 制作参考版本及错误对照：任意截断消息、每轮固定摘要、删除 ledger、无界重试。
- [ ] T06-Q1 完成独立复核、三类对照及重复验收，检查真实触发/降级而非只检查遥测宣称。
- [ ] T06-Q2 完成隔离与版本冻结，明确覆盖限制；难度和成本校准在用户选择挑战实测时进行，结果单列。

#### P5-C. 连续开发链与储备题

- [ ] P5-C01 冻结“文献来源 -> 缓存与失效 -> 配置变更与旧数据兼容”链的阶段需求、预算和验收。
- [ ] P5-C02 实现阶段间保留模型自身代码及明确允许的会话状态，不替换为参考代码。
- [ ] P5-C03 固定阶段反馈和救场规则，阶段失败后仍可按协议继续，保存累计完成度与回归。
- [ ] P5-C04 实现链式成绩与独立任务分开汇总，不只统计走到最后的成功运行。
- [ ] P5-C05 为 DirectFetch 重新确定独立新增题或明确故障注入题，避免直接回放 51 文件混合提交。
- [ ] P5-C06 将请求级自主度覆盖作为储备历史题评估，不把整个混合历史提交当成单题。
- [ ] P5-C07 评估从零重建 ISE 的独立研究需求；未另行确定范围前不进入当前主榜。

</details>

### 正式发布与维护检查

仅在决定正式发布成绩时启动；届时下列检查全部适用，每次记录 release ID，不以一次勾选代替所有未来发布。日常单次诊断不要求组织发布，但仍遵守主线的数据完整性和评分规则。

当前正式发布条件尚未齐备：真实开发仅验证 `local-practice`；需要在候选区之外实现并验证认证网关或凭据代理，补齐 `isolated-formal` 证据，并完成非参考作者的实际资格复核签署。现有摘要锁与离线门控通过不能替代这些条件。

- [ ] REL-01 本次 suite 的全部题目已 frozen，所有引用的 base/reference/grader/environment/public 摘要可验证，非参考作者的资格复核已有实际签署。
- [ ] REL-02 隐藏资料、参考对象、真实凭据与其他运行结果均不进入被测工作区，隔离级别有实际证据。
- [ ] REL-03 原始提交、验收、评审和报告均可追溯；不存在覆盖旧结果或选择性丢弃失败的行为。
- [ ] REL-04 必过失败不能靠基础分阈值或人工加分变成 PASS；未评审、零分和 INVALID 表达正确。
- [ ] REL-05 所有正式比较的版本、轨道、预算、任务集合和重复次数兼容，缺项明确标为暂定。
- [ ] REL-06 参考答案公开、需求改变或裁判修正时采用新版本/退役策略，保留历史成绩并统一重评。
- [ ] REL-07 文档准确区分设计、实现、自动验证、人工判断与模型实测，不宣称未完成的能力。

## 后续验收记录

2026-09-06，P3C05-01：完成使用说明与报告复核，当前 `plan.md` 的 P3-C05 已勾选。
修复重生成报告丢失已采集费用/token；12 种情况共 36 次报告重建通过，原始记录未变；
env1 全自测 403 通过。9 个 shell 块、16 条命令参数及 5 个只读入口校验通过。
操作说明与完整证据见 [C05 总结](/home/ubuntu/.local/share/ise-devbench/controller/docs/c05-summary.md)。
本次未调用真实 provider 或写入真人分；上方历史快照中的未完成勾选保留原样。

2026-09-08，P3-E（三障碍解除）：凭据代理 + 按运行内部网络、launch 队列与 systemd launcher、系统级隔离 worker 均已实现并真实验证。
pi/Claude Code/Codex 三 CLI 经代理 smoke 全过；worker 单元内读不到凭据；无凭据进程发起的 T01 全链路补跑 PASS 100（`practice-20260908-launcher-pi-oc-t01-r1-b1`，首次 r1 因重启 launcher 被打断，已按 harness_error 归因）。
controller 自测 445 通过。逐项数据见 [P3-E 总结](/home/ubuntu/.local/share/ise-devbench/controller/docs/p3e-summary.md)，设计见 [三障碍解除设计](isolation_launcher.md)。
本次未开设 `isolated-formal` 批次，无正式排名或真人评分。

2026-09-08，P3-E07 首个 `isolated-formal` 批次 `formal-20260908`：用户指定 pi+glm-5.3 对 Claude Code+opus-5，T01/T02 各 1 次，全部经凭据代理内部网络、launcher 启动、隔离 worker 收尾。
T01 双 PASS 100；pi T02 FAIL 45；Claude T02 INVALID（裁判缺陷）。批次暂定、不进排名。
暴露并修正批次收尾的裁判参数写死（P3-E08）；T02 裁判 stdout 协议缺陷（P3-E09）待用户决定修法与版本。记录见 [批次记录](/home/ubuntu/.local/share/ise-devbench/controller/docs/formal-20260908.md)。

2026-09-08（续），T02-GRADER-02：经用户批准修订 T02 裁判（只解析 checker stdout 最后一行 JSON，契约不变）为 1.0.2，suite ise-v1 升 1.0.1 并加锁加标签；六树资格与基线一致；`formal-20260908` 登记批次修订并对两场 T02 统一重评（pi FAIL 45、Claude FAIL 60），不重跑模型。
批次完整、正式排名就绪（重复 1、无真人评分、未发布）；P3-E07/E09 勾选。

2026-09-08，P3-F 操作台 console-v1：controller `devbench/console/`（Flask + 原生 ES2015，无外部资源），无凭据进程、只绑回环、token 与 Origin/Host 校验、静态命令白名单与审计；
总览、发起向导、运行详情、批次分析（矩阵/榜单/验收点热力表/成本/台账/表单/发布）、评审、纠错、比较发布、维护八页；视觉按 `frontend-design` skill 两段式（controller `docs/console-design.md`），图形按 `dataviz`。
后端补缺 `regrade` 任务种类、`correction launch --enqueue-launch`、`console.json`、`ise-devbench-console.service`。自测 28 项，controller 全量 489。
未做：P3-F11 真实验证（需授权与计费）与单元的 `sudo` 安装。记录见 controller `docs/p3f-summary.md`。

2026-09-08，P4 T04/T03 扩题：先交付 [ISE 两轮真实自主度报告](../reports/autonomy_evaluation_20260908/report.md)，再完成题目适用性分析、公开契约、历史净化起点、参考/替代实现、四类错误对照与独立行为裁判。两题各 7 树 × 3 次干净容器资格全部符合预登记结果，共 42 次；参考与替代均 PASS 100，base/错误对照均 FAIL。控制器回归 493 通过。
新四题文件 `suites/ise-v1-expanded.yaml` 为 `ise-v1@1.1.0`，T04/T03 1.0.0，旧两题 entry、清单/锁/标签与成绩不覆盖；新增 suite 选择与批次快照传递、公开任务附件摘要校验。实际无模型领题/容器可见性检查通过，12 条合成计划没有启动模型。Q1 为同一执行者的独立行为裁判/替代性复核，不是第二评审者签字或真人分；网页默认仍为旧两题，四题版经 CLI 选择。详见[任务二交付](expansion_20260908.md)。
