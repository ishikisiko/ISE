# ISE Benchmark 操作台设计：发起测试与结果分析

> 状态：已实现（2026-09-08，console-v1，P3-F01～F10/F12；P3-F11 真实验证待用户授权）。本文是 [实施计划](../../plan.md) P3-F 的设计依据；实现记录与偏差见 controller `docs/p3f-summary.md`，视觉方案见 controller `docs/console-design.md`。操作台的五条前置障碍已全部解除（见第 1 节），本文只规划前端与其无凭据后端，不改运行状态机、评分与裁判。
> 日期：2026-09-08。设计版本：`console-v1`，对应 `isolation-launcher-v1`、`background-executor-v1`、`cli-orchestration-v1`。
> 上位文档：[三障碍解除设计](isolation_launcher.md)、[后台执行器设计](background_executor.md)、[CLI 调度设计](cli_orchestration.md)、[操作说明](usage.md)、[系统分析](system_analysis.md)。
> 出题方私有资料，不进入被测工作区。

## 1. 前置障碍复核

"发起运行"操作台原有五条障碍，现状如下（均有实测记录，不是设计承诺）：

| 障碍 | 解法 | 证据 |
|---|---|---|
| 编排绑定操作者终端 | `execute_run` 拆为 `launch_run` / `finalize_run`，收尾幂等，由 worker 或 `batch resume` 接手 | P3-D08：`practice-20260907-232558` 由独立 session 的 worker 收尾，PASS 100 |
| 多入口并发 | `orchestration.lock`（flock）互斥，队列领取靠 `os.rename` | controller `tests/test_devbench_executor.py` 锁互斥、重复 finalize 无副作用 |
| 授权闸只在终端进程消费 | 签发仍在终端（`auth grant`），消费移到常驻 launcher；任何无凭据进程只写 `launch` 请求 | P3-E03：`practice-20260908-launcher`、`formal-20260908` 四场均由无凭据进程发起 |
| 凭据进入沙箱 | 宿主凭据代理 + 按运行 docker 内部网络；容器只拿一次性令牌 | P3-E02：`volumes/<run>/proxy/` 台账，容器外网/DNS 探测失败 |
| 只有 pi 真实验证 | `tools/smoke_cli.py` 对 pi/Codex/Claude 各做经代理 smoke | P3-E05：`artifacts/smoke/` 三种 CLI 均 `verified` |

因此操作台可以做成 **无凭据进程**，与 worker 同一隔离等级；它不需要、也不能拿到任何凭据或签发权。

## 2. 结论与范围

目标：维护者在浏览器里完成日常闭环——发起测试、看进度、看单次结果、分析批次、比较、评审、纠错、发布——不再手敲带二十多个参数的命令，也不引入新的信任边界。

结论：

1. **只读记录 + 只写不含凭据的请求。** 读直接读磁盘记录（后台执行器设计第 6 节已认定这些文件是"只读看板的数据来源"）；写一律通过现有 `python -m devbench …` 命令的子进程完成（静态 argv 白名单、`shell=False`），不另造第二套业务逻辑，行为、拒绝理由和退出码与操作说明完全一致。
2. **代码放 controller 的 `devbench/console/`**，作为 `python -m devbench console` 子命令；Flask（env1 已有 3.1.3）+ 原生 ES2015 静态页，无构建步骤、无外部 CDN（主机可能无外网）。不进 ISE 仓库：ISE 是被测对象，其源 SHA 快照是题目起点，管理资料按投影规则（P1-A03）排除。
3. **三类动作仍只在操作者终端**：`auth grant` 签发授权；`smoke --execute`、`batch execute --credentials-from-host/--secrets-env` 这类读凭据的启动；`retention cleanup --execute` 删除。操作台对它们只显示状态，并给出可复制的终端命令。
4. **操作台不跑长任务、不碰 docker。** `--inline` 收尾与就地重评不开放；docker 验收由 worker 承担，模型启动由 launcher 承担。

不在范围：公开排行榜站点（发布包是文件，公开方式另定）；多用户与账号体系（单维护者、回环地址）；改 run 状态机、评分、裁判或 prompt；给管理 Agent 增加新的动作面（管理 skill 仍只调用命令）。

## 3. 信任边界与进程

| 进程 | 读凭据 | 签发授权 | 做什么 |
|---|---|---|---|
| 操作者终端 | 是 | **是** | `auth grant`、真实 smoke、`--credentials-from-host`、删除清理、发布任务版本 |
| `devbench console`（systemd，凭据不可达） | 否 | 否 | 读记录；写 batch 登记/冻结/预登记、launch 请求、stop、`resume --enqueue`、评审、纠错派生、汇总、比较、报告重生成、发布包 |
| `devbench launcher`（systemd） | 是 | 否 | 领取 launch 请求，核验授权后取凭据启动 |
| `devbench worker`（systemd，凭据不可达） | 否 | 否 | finalize：监督、收卷、验收、报告、结局、拆代理 |

边界规则：

1. **只绑定回环。** `--bind` 只接受 `127.0.0.1` / `[::1]`，其余拒绝启动；远程使用走 SSH 端口转发。systemd 单元加 `IPAddressDeny=any` + `IPAddressAllow=localhost`。
2. **请求防伪。** 每次进程启动生成随机 token 注入页面；所有写请求（POST）必须带 `X-Console-Token` 且 `Origin`/`Host` 为回环地址；不用 cookie，不做登录。
3. **代码里没有读凭据的路径。** `devbench.console` 不 import `devbench.manage.credentials`（测试锁定）；启动时 `jobs.credential_exposure()` 自检，单元以 `--require-credential-isolation` 运行，凭据可读即退出码 3；终端调试模式不带该 flag 时页面顶栏常驻显示"凭据可读，未隔离"。
4. **ID 与路径边界。** run/batch/profile/evaluation ID 只允许 `[A-Za-z0-9._-]`；所有文件访问按登记的根目录（controller、artifacts、prep、volumes、jobs）在根内解析，拒绝符号链接与 `..`（沿用 P1-A13 与 `safe_path`）；页面永远不接受用户给的路径。
5. **候选产物一律不可信。** stdout/stderr、evidence 文本、diff、清单、截图只作为数据展示：文本只进文本节点，不识别链接、不渲染 Markdown/HTML；`present_log_text` 的去 ANSI、截断与告警照搬；评审包 `summary.html` 只在 `sandbox=""` 的 iframe（无脚本、无同源、无外链）里显示；响应头 `Content-Security-Policy: default-src 'self'; img-src 'self' data:`、`X-Content-Type-Options: nosniff`，图片按登记扩展名固定 `Content-Type`。
6. **命令白名单是代码里的静态表**（第 6 节）；表外命令没有可调用入口。字段值不得以 `-` 开头，只能进入登记的参数位；自由文本只进 `--reason/--purpose/--description/--text/--notes` 这类本就接受自由文本的参数。
7. **每条写命令记审计行** `runtime/console/commands.jsonl`（时间、操作者、argv、退出码、stdout 摘要）；页面同时显示"等价终端命令"，终端始终是完整的备用路径。
8. **写命令串行**（进程内一把锁）、超时 120 秒；超时视为失败并如实显示，不重试。

## 4. 配置与数据来源

操作台配置 `management/console.json`（不含凭据）：controller、artifacts、prep、volumes、jobs 根目录；按 CLI 种类登记的来源根目录（`cli_sources`，如 `~/.hermes/node`）；`operator` 名称（写入 `--requested-by console:<operator>` 与审计）；`bind`。启动时校验路径存在且不是符号链接逃逸。

只读数据来源（均已存在，不新增写入方）：

| 来源 | 页面用途 |
|---|---|
| `artifacts/batches/<id>/batch.json`、`frozen.json`、`plan.json`、`outcomes/*.json`、`revisions/*.json`、`aggregate.json` | 批次状态、计划矩阵、结局台账、修订、榜单 |
| `artifacts/runs/<run>/run.json`、`evaluations/<eval>/{report,evaluation}.json`、`evidence/`、`submissions/<digest>/manifest.json`、`reviews/` | 运行状态与时间戳、验收点、费用/token/时间、证据、提交清单、评审记录 |
| `runtime/volumes/<run>/orchestration.json`、`supervision/state.json`、`supervision/logs/*.log`、`proxy/summary.json`、`cli-outcome.json`、`launch-context.json` | 编排阶段与持有者、进程存活/截止、日志尾部、代理计数、网络与凭据交付方式 |
| `runtime/jobs/{queued,running,done,failed}/*.json` | launch/finalize 队列、失败原因 |
| `artifacts/authorizations.json` | 授权引用、绑定、剩余次数、过期（只有引用与计数，无凭据值） |
| `artifacts/smoke/`、`profiles/*.json`、`participants/*.json`、`suites/ise-v1.yaml` + `frozen.json`、`tasks/<T>/task.yaml`、`rubrics/` | 配置就绪、smoke 验证、版本与锁、rubric 覆盖版本 |
| `systemctl is-active ise-devbench-launcher.service ise-devbench-worker.service` | 服务存活（只读） |

派生数据用只读命令取，不自行复算规则：`batch status --volumes`、`run status --volumes`、`batch plan`、`smoke status`、`smoke check`、`profile check`、`review status`、`correction status`、`agent status`、`agent logs`。

刷新：活动页每 5 秒轮询（文件读取廉价）；首版不做 SSE。

## 5. 页面与功能

### 5.1 总览

- 服务：launcher / worker 是否 active；队列计数（按 kind 分 queued/running/failed）；失败任务列表（error 摘要）→ "重新入队收尾"（`batch resume --enqueue --run`）。
- 进行中的运行：stage、持有者、CLI 存活、已用时/截止倒计时、代理转发与拒绝计数。
- 批次列表：state、planned/recorded、complete / provisional / ranking_ready、blockers、open gaps、unbackfilled invalid。
- 配置就绪：每个 profile 的 digest、状态、按隔离级别的 smoke 验证结果、CLI 暂存版本；未验证的标红并给出 smoke 的终端命令（不在页面执行）。

### 5.2 发起测试（向导）

四步各对应一条既有命令，可从任何已存在批次的当前阶段续接（draft → frozen → planned → 逐 run 发起）：

1. **登记**：suite（默认冻结的 `ise-v1` 最新版，只能选 `suites/` 里静态登记的文件）、任务（从该 suite 的 frozen 任务里勾选子集 → `--tasks`）、参测配置（从 `participants/*.json` 选，或从 `profiles/*.json` 组合并写出新的 participants 文件）、隔离级别、重复数、purpose → `batch register`。要新的 CLI / 版本 / 模型 / effort 组合时，「组合新 profile」面板只产出预填好的 `tools/smoke_cli.py --effort … --execute` 终端命令，页面轮询 `profiles/` 直到 profile 出现；操作台不写 profile，effort 进入 profile 摘要，换档位即新 profile、需各自 smoke（实现记录见 controller `docs/p3f-summary.md` 第 8 节）。
2. **冻结与预登记**：approver、approval-reference 由人填写（不预填示例、不接受空值）→ `batch freeze` → `batch preregister` → 计划表（participant × task × repeat → run_id）。
3. **授权**：逐 run 显示授权状态（读 `authorizations.json`：是否存在、profile 摘要与任务是否匹配、剩余次数、过期时间）。缺失时显示预填好 id / profile / digest / task / max-runs / expires-in 的 `auth grant` 终端命令，approval-reference 留给人填；页面轮询直到记录出现。**操作台不签发**。
4. **发起**：前置检查全部通过（任务 frozen、profile frozen 且该 digest 在该隔离级别 `verified`、CLI 来源存在且版本与 profile 一致、launcher active、授权绑定匹配且未用尽）→ `batch execute --execute --enqueue-launch`，网络与凭据模式按隔离级别默认（正式 = proxied/proxied，练习 = bridge/scoped，可在允许范围内改为 proxied），`--requested-by console:<operator>`；显示 launch 请求文件、launcher 处理结果与随后入队的 finalize。并行数由操作者逐个点击决定，页面提示主机配额（本机 4 核 7 GB，同题两场并行是已验证的上限）。

规则：计划外 run 没有入口；不能删改计划；INVALID 的补跑先走 `batch backfill` 表单登记，再对派生 run 走第 3、4 步。

### 5.3 运行详情

- 时间线：`run.json` 时间戳 + `orchestration.json` history（launched → supervised → submitted → graded → reported → finished / abandoned），attempt 与 last_error。
- 监督：alive、pid、elapsed、deadline、exit、stdout/stderr 字节数；代理计数；`launch-context.json` 的网络策略与凭据交付方式（proxied / scoped，`readable_by_candidate`）。
- 日志：尾部（默认 4 KB，可按字节范围继续加载，只读）、不可信横幅、`present_log_text` 告警；不提供"复制为命令"。
- 操作：停止（确认 + reason → `agent stop`，页面说明停止不等于已有报告）；卡住时"重新入队收尾"（`batch resume --enqueue --run`）；`--inline` 不开放。
- 结果：verdict / base_score / group_scores / required_failed / reason；验收点表（id、group、weight、required、passed、failed_cases、error）；费用 / token / 时间（缺失显示 `null` 与"未采集"，不写 0；费用标注"CLI 报告，非账单"）；evaluations 列表（eval-001…，created_at、`grader_task_version`、重评标记）；结局台账；评审 / 纠错 / 争议状态与 `ranking_paused`。
- 证据：evaluation `evidence/` 文件列表；文本类内联（不可信、只作文本）；PNG 内联；其余只给大小与摘要。提交清单与 diff 统计。
- 报告重生成：`report --run --task-manifest tasks/<T>/task.yaml --volumes [--evaluation-id]`；退出码 0/1/2 都算"生成了报告"，只有 3 是报告器失败，页面按此区分。

### 5.4 批次分析

- 头部：口径（suite / version / track / execution / isolation / repeats / budget）、状态（complete、provisional、ranking_ready、blockers、open_gaps、unbackfilled_invalid）、批次修订台账。
- 计划矩阵：participant × task × repeat，格子显示 stage / verdict / score / outcome，点击进运行详情。
- 客观榜与辅助指标：读 `aggregate.json`；"重新汇总"运行 `aggregate --leaderboards --out-json --out-md` 写回批次目录；个人加分榜分开显示，未评审 `null` 与 0 分分开；disclaimers 原样显示，页面不加任何"显著"措辞。
- 验收点热力表：participant × 验收点（`T01-F1 … T02-R1`），失败格显示 failed_cases（如 `F1-08`、`F2-05`）；跨参测对象共同失败的用例高亮并提示"可能是契约点或任务书表述问题"，引导到 `batch gap` 表单，而不是给单次运行改条件（`formal-20260908` 两家都在 F1-08 与 F2-05 失败即此类情况）。
- 成本 / 时间 / token：每 run 数值与参测对象均值；缺失为 `null`；费用来源标注。
- 台账：outcomes（含"重评：…"）、revisions、gaps、interventions、claims、corrections。

### 5.5 管理写入、比较与发布

- 表单（都要 actor 与 reason / approval-reference）：`batch outcome`、`backfill`、`intervention`、`claim`、`gap`（含 `--resolution` 关闭）、`revise`（其前置的任务版本发布步骤 `grader_digest --check`、`check-update`、`validate`、锁与标签仍在终端）。
- 统一重评：`batch resume --regrade --run` 经队列执行（第 7 节 G1），页面要求填写理由并显示旧 evaluation 保留。
- 比较：选 2 个以上批次 → `compare`；显示 compat 表与拒绝原因；`--acknowledge-differences` 需勾选并显示"暂定、不含名次"。
- 发布：`publish --out artifacts/publications/<batch>`；泄漏检查失败只显示原因、不写出；`publication.md` 在页面预览，provisional / ranking_ready 按数据如实显示。

### 5.6 人工评审

- `review prepare [--reviewer]` → 包目录；`summary.html` 在 sandbox iframe 显示；只列 `bundle/` 与 `form.json`，页面不显示 `binding.json` 内容。
- 表单：三分项（可维护性 0–4、范围控制 0–3、交付清晰度 0–3）、理由、证据 ID（从评审包证据索引选）、reviewer、`blinded`（false / partial / true）及理由、`human_confirmed`。操作台写出 form.json 后调用 `review record`，`recording_source=human_direct`、`recorded_by=reviewer`：人亲自在页面填写，操作台是录入工具，不是代录者，不存在自动补分入口。FAIL / INVALID 只显示诊断包，无评分表（与 P3-A 规则一致）。
- 争议：`review dispute --action open|resolve`（actor / reason / evidence / outcome）；页面显示排名暂停。
- rubric 覆盖：按 T02 1.0.2 启动的运行在新 rubric 版本发布前标记"不可评审"（T02-GRADER-02 遗留待办）。

### 5.7 一轮纠错

`correction preview`（显示机械反馈与泄漏检查结果）→ `derive`（actor / reason / batch）→ 授权状态（同 5.2 第 3 步）→ `correction launch --enqueue-launch`（第 7 节 G2）→ `status`。澄清用 `feedback --kind clarification --text`，被拒绝时提示改走 `batch gap`。

### 5.8 维护

smoke 记录（按 profile digest 与隔离级别）、profile / suite / task 版本与摘要锁、rubric 版本覆盖、retention 预览（`retention cleanup` 不带 `--execute`；执行只在终端）、审计日志（`commands.jsonl`）、失败队列。

## 5.9 视觉风格：按官方 `frontend-design` skill 两段式产出，不复用 ISE 主题

风格不在本文预先拍板，施工时按 Anthropic 官方 `frontend-design` skill（`claude-plugins-official` marketplace 的 `frontend-design` 插件，与 `anthropics/skills` 仓库 `skills/frontend-design/SKILL.md` 内容一致，已安装到本机用户级）的流程产出：先写设计计划（4–6 个命名色值、字体及其角色、一句话布局概念加 ASCII 线框、对齐方式、原则），对照下面的设计简报审一遍"是不是任何同类页面都会得到的默认方案"，改掉默认项并说明理由，再写代码；分析页的热力表、统计块和对比图另按 `dataviz` skill 的配色公式与图形规范做，仍只用内联 SVG。

设计简报（施工时 skill 的输入，简报文字优先于 skill 的一般建议）：

- 主题：一个编码模型开发能力 benchmark 的维护者控制台。它的世界是台账、清单、摘要、判定、证据索引；是评卷室，不是聊天窗，也不是 SaaS 仪表盘。
- 受众与场景：单一维护者，中文界面，长时间盯着密集表格与状态；通过 SSH 端口转发在浏览器里用，无外网。
- 首要任务：不误发起（授权、前置检查一目了然）、看得清进度（阶段、截止、持有者）、读得懂结果（PASS/FAIL/INVALID、暂定、未评审 `null` 与 0 分的区别）、比得出差异（跨参测对象的共同失败点）。
- 语气：克制、证据感、不营销；文案按 skill 的写作规则——按钮说明会发生什么（"写入 launch 请求"而非"提交"），错误说明发生了什么与怎么修，空状态给出下一步。
- 硬约束：无外部资源，字体只能用随页面打包的开源字体文件或明确选定的本机字体栈，中文字形靠系统回退；候选产物只作文本；键盘焦点可见、尊重 reduced motion、对比度达标；动效只用于回应操作（展开、确认、状态变化），不做入场动画。
- 明确排除：ISE 聊天前端的纸色/青绿主题不复用；skill 列出的五种生成式默认样式（奶油底加陶土强调色、近黑底加单一酸绿/朱红、报纸式细线零圆角、统一圆角卡片套件、全大写眉标与中点串联的模板装饰）不作为无理由的选择。

## 6. 命令白名单

| 页面动作 | 命令 | 固定或派生的参数 |
|---|---|---|
| 登记批次 | `batch register` | `--controller .`；`--participants` 为操作台写出的文件 |
| 冻结 / 预登记 / 读计划 | `batch freeze`、`batch preregister`、`batch plan` | `--allow-draft-suite` 不开放 |
| 发起 | `batch execute` | `--execute --enqueue-launch --auth-store artifacts/authorizations.json --prep --volumes --requested-by console:<operator>`；`--cli-source/--cli-version` 从配置与 profile 派生 |
| 收尾兜底 / 重评 | `batch resume` | `--enqueue`（默认）；`--regrade --enqueue --run`（G1 后）；`--inline` 不开放 |
| 台账 | `batch outcome/backfill/intervention/claim/gap/revise` | actor 必填 |
| 状态 | `batch status --volumes`、`run status --volumes`、`agent status/logs`、`smoke status/check`、`profile check`、`task validate`、`review status`、`correction status` | 只读 |
| 停止 | `agent stop` | `--run-dir` 由 volumes 根 + run_id 派生 |
| 报告 | `report` | `--task-manifest tasks/<T>/task.yaml` 由 run.json 派生 |
| 汇总 / 比较 / 发布 | `aggregate --leaderboards`、`compare`、`publish` | 输出路径固定在批次目录与 `artifacts/publications/<batch>` |
| 评审 | `review prepare/record/dispute` | `--file` 为操作台写出的 form.json |
| 纠错 | `correction preview/feedback/derive/launch/status` | `launch` 只用 `--enqueue-launch` |
| 保留 | `retention cleanup`（预览）、`retention extend` | `--execute` 不开放 |

明确没有入口：`auth grant`、`smoke` 的执行工具、`batch execute --credentials-from-host/--secrets-env/--detach`、`batch resume --inline`、`retention cleanup --execute`、`worker`、`launcher`、`agent launch/tick/recover`、`prepare`、`submit`、`grade`、`qualify`、`snapshot`、`workspace`、`sandbox`。

## 7. 后端补缺（controller 小改动）

| 编号 | 改动 | 原因 |
|---|---|---|
| G1 | `batch resume --regrade --enqueue`：新增 `regrade` 任务种类（payload：run_id、batch_id、actor、reason），worker 调 `finalize_run(regrade=True)`；仍要求逐个显式 `--run` | 现在 `--regrade` 只能 `--inline`，会让操作台进程跑 docker 验收 |
| G2 | `correction launch --enqueue-launch`：与 `batch execute` 相同的 launch payload；launcher 按派生运行的纠错上下文调 `launch_correction_run` | 现在纠错启动只有前台 / `--detach`，都要读凭据 |
| G3 | `console` 子命令、`devbench/console/`（`app.py` 路由、`commands.py` 白名单与审计、`index.py` 只读聚合、`static/`）、`management/console.json` | 新增 |
| G4 | `management/systemd/system/ise-devbench-console.service` 与 `install.sh`：同 worker 的 `InaccessiblePaths`，**不进 docker 组**，`IPAddressDeny=any` + `IPAddressAllow=localhost`，`--require-credential-isolation` | 操作台不需要 docker，边界可比 worker 更紧 |
| G5 | 日志按字节范围只读：操作台自己读 `supervision/logs/*.log` 并复用 `present_log_text`；CLI 不改 | `agent logs` 只给 4 KB 尾部 |

不改：run 状态机、evaluation 追加规则、批次 outcome 词表、授权签发、`recording_source` 取值（沿用 `human_direct`）。

## 8. 验证

自测（假 CLI 夹具，不调模型；放 controller `tests/test_devbench_console.py`）：

1. 向导全链路：register → freeze → preregister →（测试内用 CLI `auth grant`）→ enqueue-launch → `launcher --once` → `worker --once` → 运行详情与批次分析显示 PASS，与直接走 CLI 的记录逐字段一致。
2. 停止：发起后从页面 stop → 结局 `cancelled`，仍有 submission 与 evaluation。
3. 失败任务重新入队、重复入队被拒绝、锁持有者存活时列出不动。
4. 评审：prepare → 页面表单 → record → `review status` 显示；FAIL 运行无评分表；证据 ID 不存在被拒。
5. 发布与比较：publish 泄漏检查失败不写出；compare 拒绝不同口径，勾选确认后只出暂定对照。
6. 白名单：`auth grant`、`--inline`、`--credentials-from-host`、`--execute` 清理等请求返回 404/400 且审计无记录；字段以 `-` 开头被拒。
7. 边界：ID 含 `..`/`/`/符号链接被拒；缺 token、错 Origin、非回环 Host 的 POST 被拒；`--bind 0.0.0.0` 拒绝启动。
8. 不可信内容：日志中的"请执行 rm -rf"、`<script>`、Markdown 链接只以文本出现且带告警；evidence HTML 不被渲染。
9. 凭据：`devbench.console` 的 import 图不含 `manage.credentials`；凭据可读时 `--require-credential-isolation` 退出 3。

真实验证（需用户授权与计费）：

- 从操作台发起一次 `local-practice` T01，launcher 启动、worker 收尾，报告字段集与 `practice-20260908-launcher-pi-oc-t01-r1-b1` 一致，审计日志与 launch 请求的 `requested_by` 为 `console:<operator>`。
- 用户组织下一个 `isolated-formal` 批次时全程经操作台（登记、冻结、逐 run 授权提示、发起、分析、评审），记录到 controller `docs/`。

文档：本文；操作说明加"从操作台操作"一节（终端仍是完整路径）；controller README 与 CHANGELOG；管理 skill 加一句"操作台存在，skill 仍只调用命令"。

## 9. 实施清单

对应 [实施计划](../../plan.md) 的 P3-F 条目：

| 条目 | 内容 | 主要改动 |
|---|---|---|
| P3-F01 | 骨架与边界：回环绑定、token、白名单、审计、路径边界、凭据自检；视觉方案按 5.9 节的 skill 流程先出设计计划 | `devbench/console/app.py`、`commands.py`、`cli.py`、`static/` |
| P3-F02 | 只读索引与总览页 | `devbench/console/index.py`、`static/` |
| P3-F03 | 发起测试向导 | `static/`、`commands.py` |
| P3-F04 | 运行详情：时间线、监督、日志、停止、重新入队 | `static/`、`index.py` |
| P3-F05 | 单次结果、证据、报告重生成 | `static/`、`index.py` |
| P3-F06 | 批次分析：矩阵、榜单、验收点热力表、成本、台账 | `static/`、`index.py` |
| P3-F07 | 台账表单、重评、比较、发布 | `commands.py`、`static/` |
| P3-F08 | 评审与纠错页 | `commands.py`、`static/` |
| P3-F09 | 后端补缺 G1、G2、G4 | `cli.py`、`jobs.py`、`launcher.py`、`batch_exec.py`、`management/systemd/system/` |
| P3-F10 | 自测 | `tests/test_devbench_console.py` |
| P3-F11 | 真实验证 | controller `docs/` |
| P3-F12 | 文档与计划勾选 | 操作说明、README、CHANGELOG、管理 skill |

## 10. 边界与不声称的事

- 操作台不是隔离边界。它与 worker 同级：进程正常路径读不到凭据、代码里没有读凭据的路径；不是对恶意本机进程的防护。
- 页面上显示的"授权有效"只是读到了终端签发的记录，不是许可的来源；launcher 仍独立核验一次。
- 页面上的费用来自 CLI 报告或估算，不是账单；缺失显示 `null`。
- 单维护者、无账号、只在回环地址；暴露到其他地址不在本设计的安全假设内。
- 操作台不改变任何结果口径：暂定、排名就绪、未评审、INVALID 的表达与命令输出一致；页面不加自己的排名或"显著差异"措辞。
