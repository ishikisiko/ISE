# ISE Benchmark 脱离终端的后台执行器设计

> 状态：已实现（2026-09-07，controller `devbench/orchestration.py`、`jobs.py`、`batch_exec.py`），自测通过；带真实模型的 detach 全链路待用户授权后执行。本机用户级 systemd 不支持 mount namespace，unit 中的凭据不可达指令在本机不生效，见 controller `management/systemd/README.md`。
> 日期：2026-09-07。设计版本：`background-executor-v1`，对应 CLI 调度设计 `cli-orchestration-v1`。
> 上位文档：[CLI 调度设计](development_benchmark_cli_orchestration.md)、[系统分析](development_benchmark_system_analysis.md)、[操作说明](development_benchmark_usage.md)、[实施计划](../plan.md)。
> 出题方私有资料，不进入被测工作区。

## 1. 结论与范围

目标：一次真实运行从启动到报告不再依赖操作者终端保持打开；管理进程退出、机器重启后，收卷、验收、报告和批次结局都能自动补齐，且不产生重复提交或重复验收。

结论：**不新建大守护进程，不改被测 CLI 的启动方式。** 把 `execute_run` 拆成"启动"和"收尾"两段；启动仍在操作者终端同步执行，收尾做成幂等且可由任何进程接手；用目录式文件队列加一个 worker 进程跑收尾；用 `resume` 命令兜底。

不在本设计范围内：
- 把启动本身放到后台或网页触发。授权核验与凭据注入仍只在操作者进程里发生，"发起运行"的操作台另行评估。
- 更换队列或进程模型为 Redis、Celery、HTTP 服务。文件加原子替换已经是框架统一的存储方式，本设计沿用。
- 改动 supervisor 的进程模型、run 状态机或 evaluation 追加规则。

## 2. 现状：哪些已经脱离终端，哪些没有

| 阶段 | 现状 | 是否依赖终端 |
|---|---|---|
| preflight、prepare、清洁检查、授权扣减 | `execute_run` 前半段 | 是，但本来就应有人在场 |
| 被测 CLI 进程 | `launch` fork 出 reaper，reaper spawn CLI；launcher 退出后 reaper 被 init 领养，退出码落到 `supervision/state.json`；`deadline.json` 让任何一次 `tick` 都能执行截止终止 | 否 |
| 轮询到停止 | `execute_run` 中的 `while True: tick(); sleep(5)` | 是 |
| 写 `cli-outcome.json`、submit、grade、report、record_outcome | `execute_run` 后半段，顺序执行 | 是 |

因此真正绑在终端上的只有轮询循环和它后面的四步。操作说明第 9 节和 controller `docs/usage.md` 第 5 节的手工恢复步骤，本质上就是人工执行这段收尾。本设计把这段规则从文档变成代码。

## 3. 设计

### 3.1 拆分：`launch_run` 与 `finalize_run`

`execute_run` 拆成两个函数，原函数保留为两者的顺序调用，行为与现在完全一致，用于对照测试。

`launch_run(store, batch_id, run_id, ...)`：
- 内容：preflight、prepare、凭据注入、清洁检查、沙箱审计、授权核验与 `consume_run`、`launch`，随后立即返回。
- 位置：操作者终端同步执行。这是唯一读取 `~/.pi/agent/auth.json` 和消费授权的时刻。
- 返回：`plan`、`launch`（pid、reaper_pid、deadline_at）和 `prompt_digest`。
- 完成后写入 `orchestration.json`（见 3.2），stage 为 `launched`。

`finalize_run(store, batch_id, run_id, controller, prep_root, volumes_root, ...)`：
- 内容：`tick` 直到状态不是 `running`/`planned`，读 `status` 与 `logs`，写 `cli-outcome.json`，submit、grade、report、record_outcome。
- 不需要授权引用，不需要凭据，不需要 CLI 暂存目录。只需要 controller、prep、volumes 与 artifacts 的读写权限。
- 输入只来自磁盘：`run-manifest.json`、`run.json`、supervision 状态、batch 冻结记录。任何在 launch 进程内存里才有的值都不能是 finalize 的输入；`participant` 相关字段从 batch 冻结记录和 `run.json` 重新读取。

### 3.2 阶段记录与幂等

在 `volumes/<run_id>/orchestration.json` 记录编排进度，控制器写入，候选不可见也不可写（该文件在工作区 `work/` 之外）。

```json
{
  "schema_version": 1,
  "run_id": "…",
  "batch_id": "…",
  "stage": "launched | supervised | submitted | graded | reported | finished | abandoned",
  "attempt": 1,
  "holder": {"pid": 12345, "hostname": "…", "since": "2026-09-07T10:00:00+00:00"},
  "history": [{"stage": "launched", "at": "…", "by": "launch_run"}]
}
```

`finalize_run` 每一步先看事实再决定做不做，事实来源是已有记录而不是 `orchestration.json` 本身：

| 步骤 | 跳过条件 | 依据 |
|---|---|---|
| 轮询 | supervision 状态已不是 `running`/`planned` | `supervisor.status` |
| 写 `cli-outcome.json` | 文件已存在且摘要与 `run.json` 产物索引一致 | `store.get(run_id)["artifacts"]` |
| submit | `run.json` 已有 submission | 现有恢复规则："已有提交不重复收卷" |
| grade | 最新 submission 已有 evaluation | 现有恢复规则："已有验收只重生成报告" |
| report | 最新 evaluation 目录下 `report.json` 存在 | 报告是投影，重生成不改事实 |
| record_outcome | 批次 `latest_outcomes` 中该 run 已有终态 outcome | `batch.latest_outcomes` |

`orchestration.json` 只用来展示进度和统计 attempt，不作为跳过依据。这样即使它损坏或丢失，finalize 仍能从原始记录正确恢复。

互斥：`volumes/<run_id>/orchestration.lock` 用 `fcntl.flock` 独占；拿不到锁立即退出并报告持有者，不等待。这防止终端里的管理 Agent 和后台 worker 同时收尾同一个 run。

### 3.3 文件队列与 worker

队列是目录，位于 `runtime/jobs/`，权限 0700：

```
runtime/jobs/
  queued/<run_id>.json
  running/<run_id>.json
  done/<run_id>.json
  failed/<run_id>.json
```

任务文件内容：`run_id`、`batch_id`、`kind`（首版只有 `finalize`）、`enqueued_at`、`enqueued_by`、`controller`、`prep`、`volumes`。领取靠 `os.rename` 从 `queued` 到 `running`，原子且同名文件只能成功一次。

worker 入口 `python -m devbench worker --jobs runtime/jobs --once|--loop`：
- `--once` 处理完当前队列即退出，便于测试和 cron 式使用。
- `--loop` 常驻，空闲时按固定间隔扫描 `queued/`。
- 并发固定为 1。验收容器独占资源，串行是正确默认；将来需要并行时在任务文件加 `lane` 字段，而不是改 worker 逻辑。
- 每个任务调用 `finalize_run`；成功移到 `done/`，抛出 `ExecutionError` 或未捕获异常时移到 `failed/` 并把异常摘要写进任务文件和 `orchestration.json`。
- worker 不读取授权库，不读取 `~/.pi`，不挂载 CLI 暂存目录。

托管方式：systemd 用户服务 `ise-devbench-worker.service`，`Restart=on-failure`，配合 `loginctl enable-linger ubuntu` 保证重启后拉起，日志进 journal。开发阶段先用 `systemd-run --user` 临时起一个 `--loop` 进程验证，通过后再固化 unit 文件到 controller `management/systemd/`。

### 3.4 `resume` 兜底

`python -m devbench batch resume --batch <id> [--run <id>] [--enqueue|--inline]`：
- 扫描该批次全部 run，找出已 launched 但 `orchestration.json` 不是 `finished`/`abandoned`、且批次也没有终态 outcome 的 run。
- `--enqueue`（默认）把它们写入 `queued/`；`--inline` 在当前进程直接调 `finalize_run`，用于没有 worker 的机器。
- 已在 `running/` 且锁被活进程持有的 run 跳过并列出；锁持有者已死的 run 视为可恢复。

覆盖的场景：worker 崩溃、机器重启、用旧的前台方式跑到一半被 Ctrl-C、`failed/` 中的任务人工排查后重试。

### 3.5 入口变化

- `tools/accept_p3b.py --execute`：默认行为不变，即 `launch_run` 后在当前进程调 `finalize_run`。新增 `--detach`：`launch_run` 后写入队列并退出，打印 run_id 和查看命令。
- `devbench batch execute`：同样新增 `--detach`。
- `devbench run status`、`batch status`：输出中并入 `orchestration.json` 的 stage、attempt 与持有者，作为后续只读看板的数据来源。
- `devbench agent stop`：不变。停止后 worker 的 finalize 会自然走"cancelled"结局并完成收卷验收，与现在管理进程仍活着时的行为一致。

## 4. 边界规则

1. **授权只在 launch 消费。** finalize 与 worker 不接触授权库。批准这道闸的位置不变。
2. **凭据只在 launch 进程读取。** worker 进程不需要也不应能读取 provider 凭据；unit 文件用 `InaccessiblePaths=` 显式挡住 `~/.pi`、`~/.codex`、`~/.claude`。
3. **grade 中途崩溃。** run 会停在 `GRADING`。grade 本来就是追加新 evaluation ID，允许再评一次；`orchestration.json` 的 `attempt` 达到 2 后不再自动重试，登记 `invalid`、cause `harness_error`，进入已有的派生补跑规则。旧的不完整 evaluation 目录保留不删。
4. **submit 中途崩溃。** submit 先写归档再迁移状态；若归档存在但 `run.json` 无 submission，视为未提交，重新 submit 会得到相同 digest（内容未变）或新 digest（容器停止后 work 目录已冻结，正常情况下内容不变）。两种情况都按 submit 的既有规则记录，不手工修 `run.json`。
5. **重复入队。** `queued/` 与 `running/` 同名文件已存在时入队拒绝，不覆盖。
6. **不制造新的状态。** run 状态机、evaluation 追加、批次 outcome 的词表都不改。`orchestration.json` 的 stage 是编排进度，不是 run 状态，报告和排名不读它。
7. **前台路径保留。** 没有 worker 的机器上，一切命令仍可像现在一样在终端完成。

## 5. 验证

新增测试放在 controller `tests/test_devbench_executor.py`，使用现有假 CLI 夹具，不调用真实模型：

1. **分拆等价**：`execute_run` 与 `launch_run` + `finalize_run` 在同一夹具上产生相同的 submission digest、verdict 与 outcome。
2. **launcher 退出后收尾**：`launch_run` 后杀掉调用进程（或直接不调 finalize），运行 `worker --once`，断言 submission、evaluation、report、outcome 各恰好一份。
3. **重复 finalize 无副作用**：对同一 run 再跑一次 `worker --once` 和一次 `batch resume --inline`，断言所有记录数量不变，`orchestration.json` 的 attempt 不增加（因为无事可做直接进入 finished）。
4. **grade 中途崩溃**：用夹具让 grade 在第一次调用时抛异常，第二次成功；断言两个 evaluation 目录都存在，报告指向第二个，attempt 为 2。第三次崩溃场景断言 outcome 为 `invalid` 且 cause 为 `harness_error`。
5. **锁互斥**：一个进程持锁时另一个 finalize 立即退出并返回持有者信息。
6. **stop 后收尾**：launch 后写 `stop.requested`，worker 完成后 outcome 为 `cancelled` 且仍有 submission 与 evaluation。
7. **凭据不可达**：在 worker 环境下断言 `~/.pi/agent/auth.json` 不可读（unit 文件生效时的集成检查，记录在 controller docs 而非单测）。

验收标准：以上用例通过；用真实 pi profile 做一次 `--detach` 的 T01 全链路，中途关闭终端，报告仍在 `artifacts/runs/<run_id>/evaluations/` 下生成，且与前台方式的报告字段一致。

## 6. 对操作台与看板的影响

做完本设计后，"发起运行"操作台的五个障碍中，编排绑定终端和多入口并发两条解除。剩余三条（授权闸、凭据进入沙箱、只有 pi 真实验证）与本设计无关，需分别由正式隔离和更多 CLI 的真实 smoke 解决。

只读看板可以直接读 `orchestration.json` 与 `runtime/jobs/` 展示进度，不需要额外接口。

## 7. 实施清单

对应 [实施计划](../plan.md) 的 P3-D 条目：

| 条目 | 内容 | 主要改动文件 |
|---|---|---|
| P3-D01 | 拆分 `execute_run` 为 `launch_run` / `finalize_run`，finalize 输入只来自磁盘 | `devbench/batch_exec.py` |
| P3-D02 | `orchestration.json` 与每步幂等判断、flock 互斥 | `devbench/batch_exec.py`、`devbench/schemas.py` |
| P3-D03 | 文件队列与 `devbench worker` | `devbench/jobs.py`、`devbench/cli.py` |
| P3-D04 | `batch resume` | `devbench/batch.py`、`devbench/cli.py` |
| P3-D05 | `--detach` 入口与 status 输出并入编排进度 | `tools/accept_p3b.py`、`devbench/cli.py` |
| P3-D06 | systemd 用户服务与凭据不可达检查 | `management/systemd/`、controller `docs/` |
| P3-D07 | 测试与一次真实 detach 全链路验证，更新操作说明 | `tests/`、`docs/development_benchmark_usage.md` |
