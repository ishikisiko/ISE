# ISE 开发评测框架使用文档

ISE-devbench 用固定的 ISE 开发任务，评估一个 CLI + 模型配置能否独立交付可验收的代码。正常流程是：选择任务与配置 → 登记运行计划 → 启动被测 CLI → 自动收卷 → 独立验收 → 查看报告；人工加分按需进行。

本文面向当前 Linux 主机上的使用者，命令以私有 controller 的实际入口为准。初次使用按第 1–6 节完成一次单题练习即可。

## 1. 当前能测什么

截至 2026-09-06，任务集为 `ise-v1@1.0.0`：

| 任务 | 版本 | 开发内容 |
|---|---|---|
| T01 | 1.0.1 | 修复产品型号、版本数字导致的引用误判 |
| T02 | 1.0.1 | 增加会话 JSON 导出接口与界面下载 |

每次运行使用任务固定的起点、公开任务书和独立裁判。基础分满分 100，人工加分最多 10 分，两者分开记录。

当前真实验证覆盖 **pi 0.85.0 + `opencode-go/muse-spark-1.3-contributor`** 的 smoke（最小真实调用验证）和两次 T01 全链路（前台一次、`--detach` 一次，均 PASS）。下面的快速开始使用对应的 `pi-opencode-go` profile。T02 已有任务与验收准备，但尚无该配置的真实开发全链路结果；Codex/Claude 目前只有适配器离线验证。

当前运行级别为 `local-practice`，可生成诊断报告。正式隔离尚未达成，也没有正式排名或真人评分结果。CLI 与模型在使用时选择，更换配置的步骤见第 8 节。

## 2. 从哪里操作

框架部署在 ISE 仓库之外；仅克隆 ISE 仓库不会安装 controller、题包或镜像。

| 位置 | 用途 |
|---|---|
| `/home/ubuntu/.local/share/ise-devbench/controller/` | 命令入口、任务清单、CLI profile 和裁判 |
| `/home/ubuntu/.local/share/ise-devbench/prep/` | 每题准备好的基线与资格证据 |
| `/home/ubuntu/.local/share/ise-devbench/artifacts/` | 批次计划、提交、验收和报告 |
| `/home/ubuntu/.local/share/ise-devbench/runtime/volumes/` | 每次运行的工作区、进程状态与日志 |

使用前需要已有上述部署、可用的 Docker、冻结的 `ise-devbench/env1-dev:v1` / `ise-devbench/env1-grade:v1` 镜像，以及所选 provider 的 pi 登录。当前快捷脚本从 `~/.hermes/node` 暂存 pi，并从 `~/.pi/agent/auth.json` 取所选 provider 的凭据。其他机器需要先按 [controller README](/home/ubuntu/.local/share/ise-devbench/controller/README.md) 和 [环境准备说明](/home/ubuntu/.local/share/ise-devbench/controller/docs/env1-image.md) 配置，不能只替换下面的目录。

后续命令均在同一终端、controller 目录下执行，使用 conda `env1` 的 Python：

```bash
cd /home/ubuntu/.local/share/ise-devbench/controller
DB_PY=/home/ubuntu/miniforge3/envs/env1/bin/python
DB_ART=/home/ubuntu/.local/share/ise-devbench/artifacts
DB_VOLUMES=/home/ubuntu/.local/share/ise-devbench/runtime/volumes
```

也可以让管理 Agent 操作。例如，先给它下面的指令；执行阶段的真实授权记录由它按框架要求登记：

```text
按 docs/development_benchmark_usage.md，使用私有 controller，选择 T01 和
pi-opencode-go profile，新建 local-practice 单次练习计划。先核对任务、
profile 与真实 smoke 状态，返回批次 ID、run_id、配置和预算，暂不启动模型。
```

确认具体计划后，可以继续指示“执行该计划中的 T01 一次，完成收卷、独立验收并返回报告”。管理 Agent 调用 controller；被测 CLI 只收到冻结的公开任务资料。本使用文档属于管理资料，现有 `docs/development_benchmark_*.md` 投影规则会将它排除在被测任务包之外。

## 3. 检查配置，生成计划

先检查任务清单与 profile，并核对该配置绑定的真实 smoke：

```bash
"$DB_PY" -m devbench task validate --suite suites/ise-v1.yaml --root .
"$DB_PY" -m devbench profile check --profile profiles/pi-opencode-go.json
"$DB_PY" -m devbench smoke status

DB_PROFILE_DIGEST=$("$DB_PY" -c 'import json; from pathlib import Path; from devbench.manage.profiles import profile_digest; print(profile_digest(json.loads(Path("profiles/pi-opencode-go.json").read_text())))')
"$DB_PY" -m devbench smoke check --kind pi --profile-digest "$DB_PROFILE_DIGEST"
```

`profile check` 中的 `formal_launch` 只表示 profile 结构检查通过，不能据此认定真实调用或正式隔离通过。继续执行前，`smoke check` 应确认该摘要已验证。

新建批次，选择 T01；需要测 T02 时将 `DB_TASK` 改为 `T02`：

```bash
DB_BATCH="practice-$(date +%Y%m%d-%H%M%S)"
DB_TASK=T01
"$DB_PY" tools/accept_p3b.py --batch "$DB_BATCH" --task "$DB_TASK" \
  --actor ubuntu --out "$DB_ART/qualification/$DB_BATCH-plan.json"
"$DB_PY" -m devbench batch plan --batch "$DB_BATCH"
```

这一步会写入批次、冻结与预登记记录，并暂存 CLI，但不调用模型。查看生成的 plan 文件中的 `steps.dry_run`，核对任务、profile、预算和路径；从 `batch plan` 的条目读取所选任务的完整 `run_id`，在当前终端设置 `DB_RUN`，后续查看与报告命令都使用它。

该快捷脚本会登记 **T01、T02 各一次**，每次只执行 `--task` 选中的一题。只跑一题时，批次汇总显示 `provisional`（暂定），这是预期结果。新一轮独立测试用新批次名；若要补齐同批次另一题，保留 `DB_BATCH` 并切换 `DB_TASK`，重新读取其 `run_id`。已经有结局的运行不能用重复启动来覆盖。

## 4. 执行一次真实测试

下面命令会产生真实模型调用。将本次实际批准记录填入 `DB_APPROVAL` 后执行；如果用户已明确授权该任务、配置与次数，沿用该授权记录即可。

```bash
"$DB_PY" tools/accept_p3b.py --batch "$DB_BATCH" --task "$DB_TASK" \
  --actor ubuntu --approval-reference "${DB_APPROVAL:?请先填写本次实际批准记录}" \
  --execute --out "$DB_ART/qualification/$DB_BATCH-result.json"
```

框架会核验并消费绑定配置、任务和次数的授权，随后自动准备工作区、启动 CLI、监督到停止、冻结提交、独立验收、生成报告并登记结局。默认在这个终端里同步完成，**保持终端运行到编排结束**。

不想守着终端时加 `--detach`：启动后命令立即返回，收尾（监督到停止、收卷、验收、报告、结局登记）交给后台 worker。收尾是幂等的，任何进程都能接手：

```bash
DB_JOBS=/home/ubuntu/.local/share/ise-devbench/runtime/jobs
"$DB_PY" -m devbench worker --jobs "$DB_JOBS" --once     # 处理完当前队列即退出；--loop 常驻
"$DB_PY" -m devbench batch resume --batch "$DB_BATCH" --volumes "$DB_VOLUMES" --inline   # 没有 worker 时就地收尾
```

常驻 worker 的 systemd 托管与本机凭据隔离的实测结果见 [systemd 说明](/home/ubuntu/.local/share/ise-devbench/controller/management/systemd/README.md)。

当前 pi 流程只注入所选 provider 的凭据；凭据与候选代码位于同一沙箱，可被候选代码读取，因此按 `local-practice` 使用。不要把整个登录文件或密钥拼进命令行。

正常完成后，命令返回结果索引路径。`$DB_ART/qualification/$DB_BATCH-result.json` 的 `steps.runs` 列出运行状态、判定、分数和结局；`executed: true` 只说明执行过，是否通过以独立验收的 verdict 为准。

### 4.1 不在终端启动：写 launch 请求，由常驻 launcher 启动（P3-E）

授权仍只能在操作者终端签发；之后任何不持有凭据的进程（脚本、未来的控制台）只需写一条 launch 请求，
常驻的 `ise-devbench-launcher.service` 核验授权、取宿主凭据、启动；`ise-devbench-worker.service`
（凭据不可达）收尾。全程不需要终端保持打开。

```bash
# 1) 终端签发授权（绑定 profile 摘要、任务、次数、时限，并记录批准出处）
"$DB_PY" -m devbench auth grant --store "$DB_ART/authorizations.json" --id "$DB_BATCH-$DB_RUN" \
  --profile pi-opencode-go --profile-digest "$DB_PROFILE_DIGEST" --task "$DB_TASK" --max-runs 1 \
  --expires-in 14400 --approval-reference "${DB_APPROVAL:?}" --granted-by ubuntu
# 2) 任意进程写 launch 请求（本进程不读凭据、不暂存 CLI）
"$DB_PY" -m devbench batch execute --batch "$DB_BATCH" --run "$DB_RUN" \
  --prep /home/ubuntu/.local/share/ise-devbench/prep --volumes "$DB_VOLUMES" \
  --cli-kind pi --cli-source /home/ubuntu/.hermes/node --cli-version 0.85.0 \
  --auth-store "$DB_ART/authorizations.json" --authorization "$DB_BATCH-$DB_RUN" \
  --execute --enqueue-launch --network proxied --credential-mode proxied
# 3) 进度：batch status --volumes（含 launch/finalize 队列与代理计数）
```

`--network proxied` 表示开发容器只在按运行的 docker 内部网络里，真实凭据留在宿主代理进程；
`--credential-mode scoped` + `--network bridge` 是旧的直连方式（凭据副本进沙箱，只能标 local-practice）。
正式隔离批次只接受 proxied。

## 5. 查看进度或停止

另开终端时先重复第 2 节的目录与变量设置，并设置原来的 `DB_BATCH`、`DB_TASK` 和计划中的完整 `DB_RUN`。

```bash
"$DB_PY" -m devbench run status --run "${DB_RUN:?请填写计划中的 run_id}"
"$DB_PY" -m devbench agent status --run-dir "$DB_VOLUMES/$DB_RUN"
"$DB_PY" -m devbench agent logs --run-dir "$DB_VOLUMES/$DB_RUN"
"$DB_PY" -m devbench batch status --batch "$DB_BATCH" --volumes "$DB_VOLUMES"
```

`run status` 查看提交与验收记录，`agent status/logs` 查看被测进程与日志，`batch status` 查看整个批次的完成情况；带 `--volumes` 时两者都并入编排阶段（launched、supervised、submitted、graded、reported、finished）、尝试次数与当前持有者。日志中的自述不作为通过依据。

需要停止时单独执行：

```bash
"$DB_PY" -m devbench agent stop --run-dir "$DB_VOLUMES/$DB_RUN" --reason cancelled
```

停止后，仍在运行的编排（终端或 worker）会继续收卷和验收；以 `run status` 中的提交、evaluation 和最终状态确认完成。如果管理进程也退出了，用 `batch resume` 补齐（第 9 节）。

## 6. 查看报告，理解结果

单次报告保存在 `$DB_ART/runs/$DB_RUN/evaluations/<evaluation_id>/report.md`，同目录的 `report.json` 用于程序读取；实际 evaluation ID 可从 `run status` 获得。

需要重新生成报告时运行：

```bash
"$DB_PY" -m devbench report --run "$DB_RUN" --task-manifest "tasks/$DB_TASK/task.yaml" \
  --volumes "$DB_VOLUMES"
```

命令返回实际报告路径，默认使用最新 evaluation；指定历史记录可加 `--evaluation-id`。重新生成只更新派生报告，不重新调用模型或裁判，也不修改原始提交、验收和人工评审记录。

| 字段或结果 | 怎么理解 |
|---|---|
| `PASS` | 通过自动验收门槛；不等于 CLI 自报完成 |
| `FAIL` | 有效运行未通过门槛；结合失败项判断交付缺陷 |
| `INVALID` | 设施或验收条件无效，先查原因，不能直接归为模型能力失败 |
| `base_score` | 自动基础分；功能 60、边界 25、回归 15；必过项失败仍判 FAIL |
| `manual_bonus` / `personal_total` | 真人评审加分及个人总分；未评审保持 `null`，与打了 0 分不同 |
| 费用、token、耗时 | 辅助指标；未采集为 `null`，费用来自 CLI 采集记录，不代表独立核对过完整账单 |
| `provisional` / `ranking_ready` | 批次结果是否暂定、是否具备排名条件；单题练习不能据此给出 suite 排名 |

生成批次汇总：

```bash
"$DB_PY" -m devbench aggregate --batch "$DB_BATCH" --leaderboards \
  --out-json "$DB_ART/qualification/$DB_BATCH-summary.json" \
  --out-md "$DB_ART/qualification/$DB_BATCH-summary.md"
```

报告重生成会核对运行索引绑定的执行记录以恢复费用与 token。迁移后用 `--volumes` 指向保留的运行目录；已登记记录缺失或摘要不符会报错，不能用旧报告里的数字补造原始数据。

`report` 退出码为 0=PASS 报告、1=FAIL 报告、2=INVALID 报告、3=报告器失败。其他命令的退出码可能不同，例如 `grade` 的 INVALID 为 3；应结合输出判定，避免把有效的失败报告当作命令故障。

## 7. 可选：人工评审

先生成证据包与空评分表，再由真人评审：

```bash
"$DB_PY" -m devbench review prepare --run "$DB_RUN" --controller .
"$DB_PY" -m devbench review status --run "$DB_RUN" --controller .
```

收到真人填写的评分表后导入，`DB_REVIEW_FILE` 指向该文件：

```bash
"$DB_PY" -m devbench review record --run "$DB_RUN" --controller . \
  --file "${DB_REVIEW_FILE:?请填写真人完成的评分表路径}"
```

只有合格的自动 PASS 提交可以获得人工加分。管理 Agent 可以代录真人给出的分数、理由和证据，不自行给分或代签。随后重新生成报告即可查看评审投影。详细规则见 [人工评审说明](/home/ubuntu/.local/share/ise-devbench/controller/docs/p3a-summary.md)。

## 7.1 可选：一轮纠错

只对自动验收为 FAIL 的有效运行做一轮纠错。反馈由验收结果机械生成，只列未通过验收点的公开编号与公开摘要，不含隐藏用例、失败样例和修改方案，也不接受人工自由文本。派生运行的 ID 是原 run 加 `-c1`，使用独立的 `correction-v1` 预算，结果单列为"一轮纠错成功率"，首次成绩不覆盖。

```bash
"$DB_PY" -m devbench correction preview --run "$DB_RUN" --controller .
"$DB_PY" -m devbench correction derive --run "$DB_RUN" --controller . --actor ubuntu \
  --reason "标准化一轮纠错" --prep ../prep --volumes "$DB_VOLUMES" --batch "$DB_BATCH"
"$DB_PY" -m devbench correction status --run "$DB_RUN" --controller .
```

启动派生运行用 `correction launch`，仍需该配置的真实 smoke 和本次运行的授权；完整参数见 controller 操作说明第 8 节。需求澄清与纠错分开登记：澄清只能复述公开契约；若澄清改变了验收要求，命令会拒绝，正确做法是用 `batch gap` 登记任务语义缺口（汇总变为暂定、比较暂停），升级任务版本后对全部配置统一重跑。

## 7.2 可选：生成发布包

```bash
"$DB_PY" -m devbench publish --batch "$DB_BATCH" --out "$DB_ART/publications/$DB_BATCH" --controller .
```

生成结果、版本清单、隔离级别、剩余限制与证据索引（相对路径加摘要，不含内容）。私有答案、隐藏用例、失败样例文本、候选日志、绝对路径和凭据不进入发布包；泄漏检查未通过时不写出任何文件。发布包按数据如实标注暂定与排名就绪状态，不等于正式成绩发布。

## 8. 更换模型、CLI，或做多次比较

快速开始的 `accept_p3b.py` 固定读取 `profiles/pi-opencode-go.json`，不是任意 CLI 的通用启动器。更换配置需要重新登记身份与验证：

1. 在 profile 中明确 CLI 版本与安装摘要、provider、模型、设置、预算、执行方式和隔离级别。
2. 做安装探测与 profile 检查，再为新摘要完成真实 smoke。安装探测的入口是 `agent probe --kind pi --executable <实际路径>`，只证明安装可探测。
3. 用新配置登记、冻结批次并预登记全部运行，再按计划执行。不要修改旧批次内已冻结的配置。

smoke 工具是 `tools/smoke_cli.py`，支持 `--cli {pi,codex,claude}`、`--model`、`--cli-version`、`--network {proxied,bridge}`。它即使不传 `--execute` 也会暂存 CLI 并写出 profile；只查看验证状态应使用 `smoke status/check`。三个 CLI 都已在 `proxied` 网络（内部网络 + 宿主凭据代理）下完成真实 smoke，profile 分别为 `pi-opencode-go`、`codex-chatgpt`、`claude-oauth`（见 [三障碍解除设计](development_benchmark_isolation_launcher.md)）。

每配置每题重复 3 次的 pilot、多配置登记和补跑规则见 [批次操作说明](/home/ubuntu/.local/share/ise-devbench/controller/docs/p3b-summary.md)。通用 `batch execute` 当前提供 `--secrets-env`，没有文件凭据参数；pi 快捷入口通过内部接口注入凭据，不能直接把它的命令改成其他 CLI 名称使用。

已有批次可用 `compare` 对照，两个变量填入实际批次 ID：

```bash
"$DB_PY" -m devbench compare --batch "${DB_BATCH_A:?请填写第一个批次 ID}" \
  --batch "${DB_BATCH_B:?请填写第二个批次 ID}"
```

默认拒绝任务集、预算、重复数等口径不兼容的比较。正式排名还需要正式隔离、独立资格复核签署和完整可比批次；当前练习结果用于诊断交付表现。

## 9. 常见问题与进一步维护

| 现象 | 处理方式 |
|---|---|
| `No module named devbench` | 确认在私有 controller 目录执行，并使用 `env1` Python |
| profile 没有通过真实 smoke | 核对配置摘要；变更配置后重新完成对应 smoke，不能只做版本探测 |
| 启动前拒绝 | 查看 dry-run、profile、授权、镜像和安装摘要的具体错误，先修复设施条件 |
| CLI 停止了却没有报告 | 确认管理编排或 worker 是否仍活动；若已退出，执行 `batch resume --batch ... --volumes ... --inline`，它按已有提交/验收/报告自动判断还差哪一步 |
| 结果为 INVALID | 保留原记录并登记原因；只有符合规则的设施原因可派生补跑，不能覆盖原结果 |
| 汇总始终暂定 | 检查是否有未运行任务、缺失 outcome 或未补齐的设施无效；单题练习本就不完整 |
| 报告重生成提示执行记录缺失或摘要不符 | 检查 `--volumes` 和原始 `cli-outcome.json`；保留错误与旧报告，不填造费用或 token |

中断恢复优先用 `batch resume`；它拒绝接手锁持有者仍存活的运行。手工 `submit` / `grade` 只在 resume 也无法处理时使用，完整命令见 [controller 操作说明第 5 节](/home/ubuntu/.local/share/ise-devbench/controller/docs/usage.md)。已有提交不重复收卷，已有验收只重生成报告。`submit` 和 `grade` 自己负责状态迁移，不额外手动 `run transition`；手动恢复后还需按实际 CLI 结局补齐批次 outcome。

框架维护者复核报告功能时可运行以下命令，它使用副本与合成评审记录，不调用模型或录入真实人工分：

```bash
"$DB_PY" tools/accept_c05.py
```

更多背景与维护入口：[实施任务清单](../plan.md)、[系统设计](development_benchmark_system_analysis.md)、[后台执行器设计](development_benchmark_background_executor.md)、[C05 验证说明](/home/ubuntu/.local/share/ise-devbench/controller/docs/c05-summary.md)、[P3-C/D 实现记录](/home/ubuntu/.local/share/ise-devbench/controller/docs/p3cd-summary.md)。
