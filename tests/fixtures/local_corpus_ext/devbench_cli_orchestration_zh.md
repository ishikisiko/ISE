# ISE Benchmark 管理 Agent 与 CLI 调度设计

> 状态：调研与设计完成；管理 skill、启动器和适配器尚未实现，也未启动被测模型。
> 日期：2026-09-05。设计版本：`cli-orchestration-v1`，对应 P0 决策 `p0-v2`。
> 上位文档：[系统分析](system_analysis.md)、[P0 决策](p0_decisions.md)、[实施计划](../../plan.md)。
> 出题方私有资料，不进入被测工作区。

## 1. 结论与范围修订

默认交互改为：**用户指定 CLI/模型配置和任务 -> 管理 Agent 调用 devbench -> 启动隔离的被测 CLI -> 自动收卷、验收、报告**。
用户不必在几个终端之间手动传递任务和结果。原来的手动领题方式作为备用入口保留。

这需要三个 CLI 的进程适配器，不需要重写它们的推理循环，也不需要实现模型厂商 API 适配器。
模型仍由用户选择；框架只是按已确认配置启动 CLI，不自动替换模型、不选择“更强模型”替用户完成任务。
新的默认执行方式称为 `managed-cli`，原方式保留为 `manual-external`；比较轨道仍是 `agent-native`。

管理 Agent 不是参赛者，不给参赛者补实现建议。它的工作是安排运行、查看进度、触发既定验收和展示报告。
模型与 CLI 的组合才是首版的比较对象，这种自动调度不会自动消除 CLI 本身带来的能力差异。

本次仅设计，不启动付费调用。未来自动启动 CLI 也可能消耗订阅额度或产生 API 花费，
不能用“没有直接调用模型 API”来暗示它免费或不需要运行授权。

<a id="interaction"></a>

## 2. 用户如何操作

### 2.1 面向用户的默认入口

在可信控制项目中使用拟定的 `devbench-manage` skill；不要求被测 CLI 安装该 skill。
skill 的发现方式随管理端 CLI 而异，底层始终使用相同的 `python -m devbench` 命令，不能依赖某家的专属斜杠命令协议。

| 用户对管理 Agent 说 | 管理端实际动作 |
|---|---|
| “检查这台机器有哪些 CLI 能用于测试。” | 运行离线 `agent probe`，检查版本、参数和所需运行文件，不读取或打印凭据 |
| “用 pi 的这个模型配置做 T01，先看看运行计划。” | 解析运行配置，输出 dry-run：题目版本、CLI、模型、权限、隔离、时限和可能的花费 |
| “按这个计划启动。” | 在已具备授权和准入条件时提交作业；控制器创建考场并启动对应 CLI |
| “看看做到哪里了。” | 返回状态、耗时、文件变更计数和脱敏事件摘要；不向参赛者发送新提示 |
| “停止这次运行并收卷。” | 控制器停止进程树、冻结已有文件、按取消规则验收，不由管理 Agent 修改代码 |
| “比较这几次的结果。” | 检查任务、版本、配额、隔离与重复数后生成对比，不择优丢弃失败 |
| “打开人工评审材料。” | 生成短报告、diff、截图与三个空加分项，用户可以跳过 |

如果用户已明确授权一个包含 CLI 配置、任务与重复次数的批次，无须每道题再问一次。
授权必须绑定该批次，不能被管理 Agent 扩大为任意模型、更多重跑或无限运行。

### 2.2 命令接口草案

以下为未来 devbench 命令，当前不可执行：

```bash
python -m devbench agent probe --cli codex --offline
python -m devbench agent probe --cli pi --offline
python -m devbench agent probe --cli claude --offline
python -m devbench run --task T01 --version 1.0.0 --profile pi-work --run trial-001 --dry-run
python -m devbench run --task T01 --version 1.0.0 --profile pi-work --run trial-001 --execute --authorization approval-001
python -m devbench status --run trial-001
python -m devbench logs --run trial-001 --tail 40
python -m devbench stop --run trial-001 --reason user_cancelled
python -m devbench report --run trial-001
```

`run` 是 prepare、launch、submit、grade、report 的编排入口，底层命令仍可单独使用以便调试与重评。
未明确执行时默认 dry-run，不创建可预读的正式试卷，不调用外层模型，不消耗正式重复次数。
`--authorization` 是控制器核验的授权记录引用，不是只要填入一个任意字符串就允许启动。
`--execute` 不能绕过未冻结任务、配额、秘密保护和 CLI 准入门槛。

手动入口仍可 `prepare -> 用户自行启动 CLI -> submit -> grade`。未适配的新 CLI 也能交卷，
但元数据、计时和隔离缺口须如实记录，不因缺适配器而否定全部产物验证能力。

<a id="architecture"></a>

## 3. 管理与执行分层

```text
用户
  |
管理 Agent + devbench-manage skill
  | 仅提交任务 ID、已批准运行配置、操作与授权引用
  v
可信 devbench 控制器 / 持久作业监督器
  |-- 冻结 task/public prompt/launch manifest
  |-- CLI adapter: codex | pi | claude
  |-- 进程生命周期、资源、超时和日志
  |
  +--> 不可信开发沙箱：新 CLI 会话 + 净化 ISE 工作区
  |        |
  |        +--> 源文件产物；CLI stdout/stderr 只作运行证据
  |
  +--> 停止全部写入 -> 冻结 submission
  |
  +--> 新候选执行沙箱 <--> 可信外部验收驱动
  |
  +--> 自动分、PASS/FAIL/INVALID、可选人工评审
```

管理 skill 只负责使用接口，不包含题目答案，不生成裁判逻辑或自由改写启动 prompt。
公开启动指令由控制器从冻结任务包机械生成，记录摘要；适配器只转换传输方式，不能夹带管理会话和隐藏反馈。
默认拒绝 `extra_prompt`、任意宿主命令、用户提供的任意可执行路径和未审核的环境变量继承。

管理会话即使看过参考代码，也不能将历史对话注入被测进程。不要使用管理 CLI 的 resume、fork 或“最近会话”。
同品牌嵌套调用也须建立独立进程环境，不继承管理端的会话标记、连接句柄或个人设置；不能靠移除一个嵌套检测变量替代隔离。
管理 Agent 同样可能被候选日志诱导，日志必须作为不可信数据呈现，不能把其中的命令当成下一步操作依据。

日常管理权限应限于运行 API；题目发布、reference 修改和 grader 变更属于单独的维护操作。
如果管理员仍有宿主全权限，系统不能防止可信管理员主动篡改全部证据，报告不得宣称抵抗这一威胁。
防止候选访问控制区的强制边界仍是 P2 的操作系统隔离，而不是 skill 中的文字要求。

<a id="cli-evidence"></a>

## 4. 调研结果与本机证据

### 4.1 三者都有非交互入口

| CLI | 首版采用 | 输入与输出 | 扩展但不作为首版依赖 |
|---|---|---|---|
| Codex | `codex exec --json` | `-` 从 stdin 接收任务；stdout 为 JSONL 事件；支持 `-C` 与 `--model` | app-server/SDK；不为一次独立任务先引入常驻会话协议 |
| pi | `pi --mode json -p` | print 模式可读 stdin，stdout 为会话与执行事件；进程 cwd 决定工作区 | `--mode rpc` 可接收 prompt/abort 等 JSONL 命令 |
| Claude Code | `claude -p --output-format stream-json --verbose` | 支持管道任务输入、事件输出与 `--model`；由父进程设置 cwd | 双向 stream-json/Agent SDK；不必引入 SDK 才能启动 CLI |

来源：[OpenAI 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、
[pi 官方 README](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md)、
[Claude Code 编程式运行](https://code.claude.com/docs/en/headless)。

首版统一采用“一次启动、一次固定初始任务、运行到退出”的形式。
CLI 内部可以多轮读写和自测；这里的“一次”不是只允许一次模型调用。
不需要 tmux、模拟按键或解析彩色 TUI；不采用某 CLI 的后台会话管理来替代统一监督器。

### 4.2 已核对的本地安装

仅执行 `--help`、`--version`，并阅读 pi 安装包文档；没有执行真实 prompt、读取 auth 内容或调用模型。

| CLI | 本机版本 | 已见关键参数 |
|---|---|---|
| Codex | `codex-cli 0.147.0` | exec、JSONL、stdin、cd、model、ephemeral、workspace-write、全局 approval never |
| pi | `0.85.0` | print、json/rpc、provider/model、no-session、offline、资源自动发现控制 |
| Claude Code | `2.1.257` | print、stream-json、verbose、model、no-session-persistence、dontAsk、tools/allowedTools、safe-mode |

解析到的可执行文件分别位于 Codex standalone release、`@earendil-works/pi-coding-agent` 与 `@anthropic-ai/claude-code` 安装目录。
P2 要复制/构建固定 CLI 安装层，记录镜像、包及可执行文件摘要，不直接挂入包含个人设置的整个宿主安装目录。
当前安装可用不代表冻结镜像已存在，也不证明这些版本能在隔离环境内成功登录和完成任务。

一个已确认的版本陷阱：官方 Claude 文档的 `--permission-prompts none` 要求 `2.1.259+`，
本机 `2.1.257` 的帮助中没有该参数，首版不能使用它。采用本机支持的权限模式与允许工具配置，
再通过无人值守资格测试证明不会等待输入。[Claude 无人值守权限说明](https://code.claude.com/docs/en/headless#turn-off-permission-prompts-in-unattended-runs)

### 4.3 启动命令形态

下列只展示沙箱内的调用骨架，不是完整部署脚本，不应直接在当前 ISE 主工作区执行。
`${MODEL_ID}` / `${PROVIDER}` 必须来自用户确认的配置；`/workspace` 是本次考场，不是宿主仓库。
统一 prompt 经 stdin 输入；cwd、清洁 HOME、环境白名单、网络和镜像由监督器在命令之外设置。

```bash
codex -a never exec -C /workspace --sandbox workspace-write --model "${MODEL_ID}" --ephemeral --json -
pi --mode json -p --provider "${PROVIDER}" --model "${MODEL_ID}" --no-session --offline
claude -p --model "${MODEL_ID}" --output-format stream-json --verbose --no-session-persistence --permission-mode dontAsk --tools "Bash,Read,Edit,Write,Glob,Grep" --allowedTools "Bash,Read,Edit,Write,Glob,Grep"
```

参数依据除本地帮助外，分别见 [Codex 命令参考](https://learn.chatgpt.com/docs/developer-commands?surface=cli)、
[pi CLI 参考](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md)、
[Claude CLI 参考](https://code.claude.com/docs/en/cli-reference)。

实现使用 `subprocess` 的 argv 数组、`shell=False` 和指定 cwd/env，不将上述命令字符串交给 shell 二次解释。
不把 prompt、模型名、run ID 或文件路径拼进 `sh -c`；路径必须由控制器解析并限制在本次运行范围。
stdout/stderr 由控制区管道采集，不把控制区日志目录作为 `-o` 路径挂进被测沙箱。

<a id="adapter-contract"></a>

## 5. 适配器契约

适配器只管不同 CLI 的调用及观测差异，不接管工具规划和模型推理。

| 接口 | 责任 |
|---|---|
| `probe` | 离线核对支持版本、参数、安装摘要和运行依赖；报告 supported/unsupported，不请求模型 |
| `build_launch_spec` | 从已验证 profile 生成 argv、cwd、环境引用、stdin 输入策略、事件协议和 session 策略 |
| `normalize_event` | 将已支持版本的 JSONL 转成统一事件，并保留原始记录定位；未知字段不冒充完成信号 |
| `classify_exit` | 综合 OS 退出/信号、事件终态、监督器停止原因与证据完整性，生成 launch outcome |

创建沙箱、启动、超时、取消、最终冻结归统一监督器管理，不让每个适配器另写一套生命周期。
`probe` 通过只表示 CLI 接口兼容；实际认证另做经授权的 smoke，不能把 `--help` 成功当作模型可用。

运行配置结构示意，不是可执行 profile：

```yaml
schema_version: 1
profile_id: pi-work-draft
status: draft
track: agent-native
execution_mode: managed-cli
cli:
  kind: pi
  expected_version: 0.85.0
  installation_digest: UNSET
adapter_revision: UNSET
model:
  provider: USER_SELECTED_PROVIDER
  requested_id: USER_SELECTED_EXACT_MODEL_ID
  settings: {}
session_policy: fresh_ephemeral
customization_policy: frozen_public_only
prompt_policy: frozen_task_only
isolation_level: isolated-formal
budget_profile: pilot-native-v1
auth_profile_ref: UNSET
authorization_ref: null
```

正式执行拒绝 draft、UNSET、未批准 profile 和不支持的 CLI 版本。模型名单仍可在运行前随时由用户选择，
但一次 run 启动后配置不可静默改变。同一名称的 profile 修改后生成新 digest，不覆盖历史配置。
比较清单包含 execution_mode；默认不将手动交卷与管理式调度无提示混排，需要合并时先证明共同条件等价并公开口径。
请求的 model ID、CLI 实际报告的模型、provider 和 provenance 分开保存，CLI 自报不等于独立 provider 证明。
禁止自动 fallback；若 CLI 实际换模型，单列混合配置或取消该单模型配置的排名资格，不把它当成正常重复。

<a id="configuration"></a>

## 6. 配置、会话与权限隔离

所有 CLI 的共同前置条件：新 HOME、独立会话目录、冻结公开资料、无控制区挂载、无个人记忆、无宿主环境整体继承。
默认保持 CLI 自身的 coding 工作方式，不把三者替换成统一自制 Agent；清洁配置与工具策略写入 profile 供比较时披露。

| CLI | 清理与公开资料策略 | 必须避免的误解 |
|---|---|---|
| Codex | 单次 `CODEX_HOME`，使用受控生成的 config；ephemeral；不 resume；审计 skills/MCP/hooks、规则与搜索设置 | `--ignore-user-config` 只跳过主 config，不等于禁用全部规则/资料发现，也不隔离 auth |
| pi | 单次 `PI_CODING_AGENT_DIR`；no-session；关闭未批准的 extensions/skills/templates 发现；公开项目规则经固定方式加载 | `--no-extensions` 仍允许显式 `-e`；`--offline` 只禁止启动期网络操作，不禁止模型调用 |
| Claude Code | 单次 HOME；no-session-persistence；固定 settings、MCP 与工具策略；可用 safe-mode 禁用自定义发现并显式提供公开项目规则 | no-session-persistence 不等于没有 hooks/记忆；safe-mode 仍需审核管理员策略，不凭一个 flag 宣称全隔离 |

依据：[Codex 命令参考](https://learn.chatgpt.com/docs/developer-commands?surface=cli)、
本机 pi 帮助及 [pi 环境文档](https://pi.dev/docs/latest/environment-variables)、
[Claude CLI 参考](https://code.claude.com/docs/en/cli-reference)。

如果某清洁模式跳过 AGENTS.md/CLAUDE.md，启动器必须通过冻结公开 prompt 补齐同一份项目规则，不能让某个 CLI 少拿需求。
不得把 CLI 的技能发现开关与 ISE 应用源码中的 `skills/` 模块混淆，后者仍是正常被测代码。
首版禁止子 Agent、个人扩展和额外搜索；允许本地读写、shell 和公开自测。不支持该 profile 的 CLI 不进入正式模式。

CLI 权限提示必须预先处理，不由管理 Agent 在考试中逐次判断“这个工具值不值得批准”。
Codex 使用 never 配合 workspace-write；Claude 使用 dontAsk 配合公开工具允许集合；pi 的 shell 能力由外层沙箱限制。
工具 allowlist 不是操作系统沙箱，能够执行 shell 就不能仅靠工具名称保证没有其他程序或网络访问。
不把绕过所有权限检查的参数作为默认值；某 CLI 在容器内权限不兼容时先修环境或拒绝启动，不自动扩大到宿主权限。

### 6.1 认证与花费

认证引用只指向用户批准的连接配置，不在 profile、argv、日志或任务中保存真实密钥。
被测 CLI 与候选代码共享权限时，两者可读取的环境密钥不可能对候选代码保密；不能仅用 `env` 注入就声称完成秘密隔离。
正式模式采用候选区之外的认证网关或经验证的凭据代理，只给本次限权、可撤销的访问能力；验收区没有该能力。
CLI 的账号登录/订阅方式不一定适配同一种网关，必须逐组合验证，不能承诺所有现有登录状态可直接搬入正式沙箱。
OpenAI 也明确提醒，运行仓库代码的环境不应共享真实模型密钥。[自动化认证说明](https://learn.chatgpt.com/docs/non-interactive-mode#authenticate-in-automation)

本期保持 `execution_authorized: false`，只允许离线探测与假 CLI 测试。
未来运行需用户批准任务、CLI/profile、重复次数和墙钟上限，并说明使用订阅或 API、费用是否可观测。
框架能够停止本地运行，但不承诺取消已到达 provider 的请求或保证统一美元硬上限。
Claude 的预算参数可作为特定 profile 的附加限制，不冒充跨三个 CLI 的等费用保证；统一费用实验另行设计。

<a id="lifecycle"></a>

## 7. 生命周期与无人值守运行

作业控制是持久化的程序逻辑，不依赖管理 Agent 一直保持当前对话。
管理端关掉终端、模型回答结束或连接中断，不能导致开发沙箱无限运行或丢失截止时间。
后台 `run` 的零退出码仅表示作业受理成功；最终成绩通过 status/report 获取，不能与 grader 的 PASS 退出码混用。

1. 离线校验任务冻结、profile、CLI 版本、授权、资源和隔离能力；不满足条件时 `LAUNCH_REJECTED`，尚未开始考试。
2. 在私有准备区构建考场和固定 prompt，记录启动 manifest；任务首次可读之前写入 RUNNING 和截止时间。
3. 在独立沙箱内用管道启动 CLI，不分配 TTY；发送唯一公开初始 prompt 后关闭 stdin。
4. 连续消费 stdout/stderr，限制单行长度、总量与内存缓冲；使用控制器接收时间，不信任 CLI 自报时间。
5. CLI 退出或触发截止/取消时，停止全部写入者与后台进程。不能只杀父进程或 `docker exec` 客户端。
6. 超时先冻结截止时文件视图，再清理；可给进程最多 10 秒退出清理，但这段时间不能修改计分产物。
7. 冻结 submission，关闭外层连接，在新的执行区完成 grade 和 report，最后记录残留检查结果。

首版仍为单机串行、单次活动考场。若监督器本身故障，重启后基于 run ID、容器身份与截止时间归并状态，
不重复启动一个参赛进程；重试 `run --run 同一ID` 返回已有作业或冲突，而不是再花一次钱。
需要具备外部兜底终止机制；做不到监督器故障后仍有界收尾时，不通过正式运行验收。

不同 CLI 的原生结束事件不同：Codex 有 `turn.completed/turn.failed/error`，pi 有 `agent_end/message_end`；
Claude 需结合该版本的终态结果与进程状态解析，不能只等到一段最终自然语言。
事件格式来源：[Codex JSONL](https://learn.chatgpt.com/docs/non-interactive-mode#make-output-machine-readable)、
[pi JSON 事件](https://pi.dev/docs/latest/json)、[Claude 输出格式](https://code.claude.com/docs/en/headless#get-structured-output)。

`exit_code=0`、`agent_end`、最后一句“完成了”均只表示某种运行终止信号，不代表任务 PASS。
进程结束但缺失必要终态、JSONL 截断或日志解析不兼容时单列 `telemetry_incomplete`，仍可独立检验代码，
但不得伪造完整开发证据或无提示纳入正式可靠性统计。
事件中的 usage 可能累计或重复出现，必须按对应 CLI 的终态/消息身份去重；缺失费用为 null。

### 7.1 故障与重跑

| 情况 | 处理 |
|---|---|
| 缺 CLI、版本不支持、无授权、配置/隔离检查失败 | 启动前拒绝并记录；不冒充模型失败，也不生成正式试题预读机会 |
| 已启动但 CLI/provider/认证失败 | 保存实际退出与诊断，由冻结归因规则区分配置设施异常和被测执行失败，不由管理 Agent 随意删除 |
| CLI 正常退出但未完成、无改动或主动放弃 | 对已有提交验收，作为有效失败保留 |
| 超时或用户中途停止 | 冻结截止产物，记录 stop_reason；正式计划中不得靠停止较差运行逃避分母 |
| 内部 CLI 重试 | 时间继续计入，usage 可获得时计入；保留日志，不额外延长时间 |
| 管理端想再次启动或改模型重试 | 需要新的 run/配置与剩余授权；不覆盖旧结果，不自动 resume 最近会话 |

没有持久会话的首版不承诺接着原对话恢复。纠错实验从冻结提交派生新 run，发送统一反馈，保留首次成绩。
pi RPC 可在未来为双向交互提供能力，但首版不允许管理 Agent 用 steer/follow_up 暗中指导参赛者。
RPC abort 也不替代外层进程树停止与文件冻结。[pi RPC 协议](https://pi.dev/docs/latest/rpc)

<a id="validation"></a>

## 8. 实施与验收分层

本次完成的是资料核对与接口设计，以下均为 P2/P3 待办，不在这里勾选实现完成。

| 层次 | 验证内容 | 是否调用真实模型 |
|---|---|---|
| 文档与探测 | 帮助/版本、参数对照、profile 示例、版本差异记录 | 否；本次已做 |
| 假 CLI 合约测试 | 三种 stdout/stderr、分片 JSONL、异常退出、伪造完成、usage 去重、prompt/argv 注入 | 否；P2 |
| 沙箱监督器测试 | 限额、非 TTY、后台子进程、截止冻结、取消、监督器重启和幂等 | 否；P2 |
| 三个适配器 smoke | 逐 CLI/profile 证明可登录、读写小任务、调用公开测试、退出并被收卷 | 是，或使用该 CLI 明确支持的本地 mock；单列授权和证据，不用假 CLI 冒充 |
| 正式 benchmark | 冻结 T01/T02、每配置各题 3 次、独立评分和可选个人评审 | 是；P1/P2 准入后，P3 经用户授权 |

新增关键反例：

- 管理 Agent 尝试追加提示、传入 reference 路径或改变任务权重，被控制接口拒绝。
- 日志里要求管理 Agent 执行宿主命令，不会触发任何管理操作。
- 某个 CLI 请求审批、登入或继续会话时，不进入无限等待，不读取既有个人会话。
- 被测 CLI 伪造满分结果、输出成功但源码失败，最终仍由 grader 判为 FAIL。
- CLI 退出后留下后台写入进程，不得影响最终归档；监督器重启不能重复启动收费调用。
- 三个适配器没有 live smoke 证据时，只能声称协议/离线测试通过，不能声称已经兼容正式模型运行。

下一步实施顺序：P1 继续制作题目；P2 先完成确定性控制器与假 CLI，再接 Codex/pi/Claude 适配器及管理 skill。
真实 smoke 与正式题目试跑都需要用户另行授权，本次设计请求不视为计费运行授权。
