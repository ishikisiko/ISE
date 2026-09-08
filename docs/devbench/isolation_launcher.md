# ISE Benchmark 三障碍解除：凭据代理、启动队列与三 CLI 真实验证

> 状态：已实现并真实验证（2026-09-08，controller `devbench/credproxy.py`、`manage/credentials.py`、`launcher.py`、`jobs.py`、`tools/smoke_cli.py`、`management/systemd/system/`）。验证结果见第 6 节，未达成事项见第 7 节。
> 日期：2026-09-08。设计版本：`isolation-launcher-v1`，对应 `background-executor-v1` 与 `cli-orchestration-v1`。
> 上位文档：[后台执行器设计](background_executor.md)、[CLI 调度设计](cli_orchestration.md)、[系统分析](system_analysis.md)、[操作说明](usage.md)、[实施计划](../../plan.md)。
> 出题方私有资料，不进入被测工作区。

## 1. 要解决的三条障碍

后台执行器完成后，"发起运行"操作台仍有三条障碍（见后台执行器设计第 6 节）：

| 障碍 | 原状 | 本设计的解法 |
|---|---|---|
| 授权闸 | 授权核验与消费只在操作者终端进程里发生，网页或后台进程不能发起 | 授权**签发**仍只在操作者终端；**消费**移到常驻 launcher。任何进程只能写一条不含凭据的 launch 请求，launcher 核验授权库里用户签发的记录后才启动 |
| 凭据进入沙箱 | 受限凭据副本写进会话目录，与候选代码同沙箱可读；开发容器需 bridge 直连 provider，正式隔离（`--network none`）做不到 | 宿主侧**凭据代理** + 按运行的 docker **内部网络**：容器只拿一次性令牌与代理地址，无外网无 DNS；真实凭据只在宿主代理进程内存 |
| 只有 pi 真实验证 | Codex/Claude 仅离线适配器验证，凭据注入方式未验证 | 通用 smoke 工具 `tools/smoke_cli.py` 对 pi、Codex、Claude 各做一次经代理的真实 smoke，五项全过才登记 |

三者合在一起的效果：控制台或任何无凭据进程写请求 → launcher（读得到凭据）启动 → 隔离 worker（读不到凭据）收尾，全程不需要操作者终端在场，也不需要把凭据交给网页或沙箱。

## 2. 凭据代理与内部网络（障碍二）

### 2.1 机制

1. **网络**：launch 时 `docker network create --internal ise-devbench-formal-<run_id>`，创建后立即 `inspect` 复核 `Internal=true`。容器只能到达该网络的网关地址（宿主），到不了外网，也没有 DNS（实测 `1.1.1.1:443` 连接失败、`api.openai.com` 解析失败）。
2. **代理**：宿主上 `python -m devbench.credproxy` 绑定网关地址的临时端口。真实凭据经 stdin 传入子进程内存，不落盘、不进容器、不写日志；`proxy/state.json` 只记 pid、端口、令牌摘要、upstream 与头名。
3. **令牌**：每次运行随机生成 `devbench-<43 字符>` 令牌；容器内 CLI 以 `Authorization: Bearer <令牌>`（或 `x-api-key`）请求代理，代理核对后换成真实凭据头原样转发到登记的 upstream origin（路径不改写）。未知令牌 401；收尾撤销后 403。
4. **台账**：`proxy/requests.jsonl` 每请求一行：时间、方法、路径、状态、字节数、耗时、令牌摘要；不记请求体、响应体或任何头部值。`summary.json` 汇总计数。
5. **拆除**：收尾（finalize）监督结束后立即撤销令牌、结束代理、删网络；幂等，不需要凭据，隔离 worker 可执行。`agent stop` 也会顺带撤销令牌。

### 2.2 各 CLI 的接法（均已真实验证）

| CLI | 容器内拿到什么 | 代理侧 | 备注 |
|---|---|---|---|
| pi | 会话目录 `auth.json`（`{provider: {type: api_key, key: 令牌}}`）+ `models.json`（内置 provider 的 `baseUrl` 改指代理，路径与真实 baseUrl 相同） | `Authorization: Bearer <真实 key>` → `https://opencode.ai` | 内置 baseUrl 从暂存 CLI 的模型目录读取，不手写 |
| Claude Code | 环境变量 `ANTHROPIC_BASE_URL=http://<网关>:<端口>`、`ANTHROPIC_AUTH_TOKEN=令牌` | `Authorization: Bearer <OAuth access token>`，并按合并语义追加 `anthropic-beta: oauth-2025-04-20` | 用宿主 `~/.claude/.credentials.json` 的订阅登录；不带 refresh token |
| Codex | 会话目录 `auth.json`（`auth_mode: chatgpt`，`access_token=令牌`，`refresh_token=""`，`id_token` 为只含账号/套餐声明、占位签名的三段式 JWT）+ `config.toml`（`chatgpt_base_url` 指代理；自定义 `model_providers.devbench`：`base_url=<代理>/backend-api/codex`、`requires_openai_auth=true`、`supports_websockets=false`） | `Authorization: Bearer <ChatGPT access token>` + `chatgpt-account-id` → `https://chatgpt.com` | 只改 `chatgpt_base_url` 时模型请求仍直连 chatgpt.com 且走 WebSocket，必须用自定义 provider；`id_token` 签名段为空会让 Codex 退回 API-key 模式（均为 2026-09-08 宿主实测） |

### 2.3 网络策略与隔离级别

`batch_exec.select_network` 统一裁决：

| 隔离级别 | 凭据交付 | 允许的网络 |
|---|---|---|
| `local-practice` | `scoped`（副本进会话目录，候选可读）或 `proxied` | `bridge`（默认，配 scoped）/ `proxied`（配 proxied） |
| `isolated-formal` | 只允许 `proxied` | `proxied`（默认）或 `none`；`bridge` 与 `scoped` 一律拒绝 |

`sandbox.SandboxSpec` 相应放宽：正式隔离允许 `none` 或 `ise-devbench-formal-<run_id>` 前缀的内部网络；`audit_argv` 对未登记网络名报问题。`DEV_NETWORK["isolated-formal"]` 由 `none` 改为 `proxied`，这是"正式隔离下开发容器如何连 provider"这一原设计空白的正式答案。

### 2.4 明确不声称的事

- 代理只转发 HTTP(S)，不做 WebSocket 隧道；Codex 依赖关闭 WebSocket 的自定义 provider。
- 代理换头不隐藏请求内容：provider 仍能看到全部提示与代码，这是 provider 侧的固有事实。
- `scoped` 模式的凭据副本不带 refresh token，避免容器内 CLI 刷新令牌让宿主登录失效；代价是访问令牌过期即失败（Claude 令牌约 8 小时，Codex 约 8 天，均在启动前检查）。
- 记录里出现的一次性令牌只在代理进程存活期间有效；启动/收尾/smoke 记录均去掉容器环境，不保留令牌。

## 3. 启动队列与 launcher（障碍一）

### 3.1 任务种类

`runtime/jobs/` 新增 `launch` 种类（文件名 `<run_id>.launch.json`，与 finalize 的 `<run_id>.json` 互不冲突）。payload 只允许引用类字段：`authorization_id`、`auth_store`、`cli_kind`、`cli_source`、`cli_version`、`prep`、`network`、`credential_mode`、`require_smoke`、`requested_by`；出现未登记字段或疑似凭据的值即拒绝入队。

### 3.2 三个进程的职责

| 进程 | 读凭据 | 签发授权 | 做什么 |
|---|---|---|---|
| 操作者终端 | 是（不必） | **是**：`auth grant --approval-reference <批准出处> --granted-by <人>` | 唯一签发授权的地方；也可直接 `batch execute --execute --credentials-from-host` |
| 任意无凭据进程（控制台、脚本） | 否 | 否 | `batch execute --execute --enqueue-launch --authorization <id>`：核验授权存在且绑定一致后写 launch 请求 |
| `devbench launcher`（systemd） | 是 | 否 | 领取 launch 请求 → `authorize_launch` 再核验一次 → 按 profile 的 `auth_profile_ref` 取凭据 → 暂存 CLI → `launch_run` → 入队 finalize |
| `devbench worker`（systemd，凭据不可达） | 否 | 否 | 领取 finalize → 监督到停止 → 拆代理/网络 → 收卷 → 验收 → 报告 → 结局 |

没有授权记录时 launcher 只登记失败（"授权库不存在/引用不存在/绑定不匹配"），不去碰凭据也不暂存 CLI；授权仍绑定 profile 摘要、任务与次数，`used_runs` 在 launch 时扣减。控制台因此**永远不需要**凭据或签发权，这就是操作台"授权闸"的解法：闸门还在，只是钥匙不在网页手里。

### 3.3 `auth_profile_ref` 解析（`manage/credentials.py`）

| 引用 | 来源 | scoped | proxied |
|---|---|---|---|
| `host-pi-auth:<provider>` | `~/.pi/agent/auth.json` 单个 provider | 该 provider 条目 | 仅 `api_key` 类型；upstream/base_path 取自内置模型目录 |
| `host-codex-auth` | `~/.codex/auth.json` | chatgpt 令牌（去 refresh）或 API key | chatgpt 令牌 + 账号头；沙箱 `id_token` 去掉 email/name |
| `host-claude-auth` | `~/.claude/.credentials.json` | `claudeAiOauth`（去 refresh、去 mcpOAuth） | OAuth access token + oauth beta |

只在启动进程里 import；worker、控制台、批次登记都不该用它。`describe()` 只输出形态与过期信息。

## 4. worker 的凭据隔离（原 P3-D06 遗留）

本机用户级 systemd 不能建立 mount namespace，但**系统级**单元可以。`management/systemd/system/ise-devbench-worker.service` 以 `User=ubuntu` 运行、`InaccessiblePaths` 挡住 `~/.pi`、`~/.codex`、`~/.claude`、`~/.claude.json`、`~/.hermes`、`~/.ssh`、`~/.aws`，并以 `--require-credential-isolation` 自检。实测（2026-09-08）：单元内 `credential_exposure` 为 `isolated: true, readable: []`，同一命令在普通终端被拒绝并列出 4 个可读凭据文件。边界说明：worker 在 `docker` 组，docker 组本身等价于可读任意宿主文件；本单元达成的是"进程正常路径读不到凭据、代码里没有读凭据的路径"，不是对恶意 worker 代码的防护。

## 5. 三 CLI 真实 smoke（障碍三）

`tools/smoke_cli.py --cli {pi,codex,claude} --network {proxied,bridge} --execute --approval-reference <出处>`：暂存 CLI → 生成并冻结 profile（`profiles/<id>.json`）→ 从宿主取凭据 → 建网络/代理 → 容器内版本核对 → 本地登录状态 → 监督下真实开发运行（读、写、自测） → 控制器独立复跑 pytest → 短截止 + 停止 → 收卷清单 → （proxied）容器外网/DNS 探测、会话目录秘密扫描、代理台账 → 拆除 → `smoke.record` 登记。替代原 `tools/smoke_pi.py`。

Codex 的两处容器内事实：Linux 沙箱默认后端 bubblewrap 需要非特权 user namespace，在 `--cap-drop ALL` 容器里不可用；`use_legacy_landlock` 的 Landlock 后端对 `codex sandbox` 可用但 exec 模式直接 panic（"permission profiles requiring direct runtime enforcement are incompatible with --use-legacy-landlock"）。因此容器内 Codex 用 `--sandbox danger-full-access`，以外层 docker 容器为沙箱边界，该事实写入 profile 的 `model.settings` 与运行记录。

## 6. 验证结果（2026-09-08）

见 controller `docs/p3e-summary.md` 的逐项数据。摘要：

| 项目 | 结果 | 关键数据 |
|---|---|---|
| pi 0.85.0 经代理 smoke（opencode-go） | 通过 | 代理转发 7 次、0 拒绝；费用 0.00045 USD、12,031 token；容器外网/DNS 探测失败（符合预期），会话目录无真实凭据 |
| Claude Code 2.1.263 经代理 smoke（订阅 OAuth） | 通过 | 转发 6 次；费用 0.0668 USD、86,886 token；`anthropic-beta` 头合并透传 |
| Codex 0.153.4 经代理 smoke（ChatGPT 登录） | 通过（第三次） | 转发 38 次、43,844 token，费用不可得；自定义 provider 关闭 WebSocket，容器内 `danger-full-access` 并记录 |
| worker 凭据隔离（系统级 systemd） | 通过 | 单元内 `isolated: true, readable: []`；同一命令在终端被拒并列出 4 个可读凭据文件 |
| 无凭据进程发起 → launcher → 隔离 worker（T01，r1） | 启动成功、运行被打断 | 操作者 restart launcher 连带杀掉 CLI；登记 `invalid/harness_error` 并 `backfill`，单元改 `KillMode=process` |
| 同上补跑（T01，r1-b1） | PASS，基础分 100 | 约 24 分钟；0.0107 USD、772,462 token；代理 42 次全部转发、0 拒绝、0 上游错误（8 次 429 已重试）；网络/代理收尾拆除 |

两次 T01 运行均为 `local-practice`，不进入排名。

同日开设首个 `isolated-formal` 批次 `formal-20260908`（用户指定 pi 0.85.0 + zai-coding-cn glm-5.3 对 Claude Code 2.1.263 + claude-opus-5，T01/T02 各 1 次；两个正式 profile 先各自通过 isolated-formal 级 smoke）：

| run | 判定 | 基础分 | 开发用时 | 费用（CLI 报告） | 代理转发/拒绝 |
|---|---|---:|---:|---:|---|
| pi-glm53 / T01 | PASS | 100 | 3 分 49 秒 | 0.169 USD | 14 / 0 |
| claude-opus5 / T01 | PASS | 100 | 6 分 51 秒 | 1.333 USD | 23 / 1 |
| pi-glm53 / T02 | FAIL（裁判 1.0.2 重评） | 45 | 9 分 38 秒 | 0.845 USD | 70 / 0 |
| claude-opus5 / T02 | FAIL（裁判 1.0.2 重评） | 60 | 10 分 17 秒 | 3.703 USD | 72 / 1 |

四场均由无凭据进程发起、launcher 启动、隔离 worker 收尾，容器只在内部网络；Claude 的 1 次拒绝是其自带探测不带令牌。两场 T02 暴露了裁判链路的两处缺陷并均已修（详见第 7 节与 controller `docs/formal-20260908.md`）；批次汇总：平均基础分 pi-glm53 72.5、claude-opus5 80.0，状态完整、正式排名就绪（重复 1、无真人评分、未发布）。

## 7. 未达成与边界

- `formal-20260908` 四场跑完、批次完整。过程中修了两处裁判链路缺陷：（1）批次收尾把 T01 的裁判参数写死套给所有任务，T02 从未走过批次收尾链路——controller `grading_specs.py` 按任务登记参数与夹具；裁判无产出的 INVALID 正确成文并登记 harness_error；`batch resume --inline --regrade` 显式重评。（2）T02 裁判把 checker 的整个 stdout 当 JSON 解析，候选代码在被测路径打印一行诊断即整场无验收报告——经用户批准修为只解析最后一行 JSON，发 T02 1.0.2（契约不变，六树资格与基线一致）与 suite ise-v1 1.0.1（新锁、新标签），`grader_digest` 改为可复算算法，批次登记修订（`batch revise`）后对两场 T02 提交统一重评，不重跑模型。遗留：人工评审 rubric 只列 T02 1.0.1，新运行评审前需发新 rubric 版本。
- 正式排名还需 REL-01..07 的其余条件（真人评分、重复数、可比性）。
- 代理不做 WebSocket；Codex 依赖 `supports_websockets=false`。未来 CLI 若强制 WebSocket，需要给代理加隧道。
- 凭据代理运行在宿主、以操作者身份持有真实凭据：它是"操作者进程"的替身，不是对宿主上恶意进程的防护。
- launcher 读得到凭据且常驻；它只在授权核验通过后取用，但它本身不是隔离边界。
- 控制台仍未建设；本设计只是把它需要的三条前提补齐（见后台执行器设计第 6 节的剩余障碍表，现已全部解除）。
