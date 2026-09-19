# process-audit-log Specification

> **Status:** active — 当前契约，且在目标架构中存续。 分类依据见 `docs/agentic_loop_roadmap.md`。

## Purpose
Define a toggleable, persisted per-turn process audit for CLI and Web requests,
with bounded JSONL retention and best-effort writes.

## Requirements

### Requirement: Audit settings SHALL resolve deterministically
系统 SHALL 按优先级解析审计开关：CLI `--audit off|file` 单次覆盖 > `config.json` 的 `audit.enabled` > 默认关闭。配置块缺省时系统 SHALL 视为关闭。CLI 传 `--audit off` 时即使配置启用，本次 CLI 运行 SHALL NOT 产生任何审计写盘。

#### Scenario: Default is off
- **WHEN** 用户未传 `--audit` 且 config 无 `audit` 块
- **THEN** 系统 SHALL 维持现状行为（CLI 只输出答案，不产生审计文件）
- **AND** 编排器 SHALL 使用 `NullWorkflowTracer`（CLI 侧），无额外文件 I/O

#### Scenario: CLI overrides config
- **WHEN** config `audit.enabled=true` 且用户传 `--audit off`
- **THEN** 本次 CLI 运行 SHALL NOT 写入审计记录

#### Scenario: Config enables audit
- **WHEN** config `audit.enabled=true` 且 CLI 未传 `--audit`
- **THEN** CLI 运行 SHALL 写入审计记录

### Requirement: CLI audit wiring SHALL preserve output behavior
审计开启时，`main.py` SHALL 创建真实 `WorkflowTracer` 并传入 `orchestrator.answer()`，且 SHALL 强制启用耗时采集，随后在 `answer()` 返回后写出本轮审计记录。同一轮 CLI 运行 SHALL 恰好产生一条审计记录（编排器内钩子不得重复写）。

#### Scenario: Successful CLI turn is persisted
- **WHEN** 用户以 `--audit file` 运行 CLI 且问答成功
- **THEN** 审计目录下该 `conversation_id` 对应的 JSONL 文件 SHALL 新增恰好一行
- **AND** stdout 的答案输出与现有格式保持一致

#### Scenario: Audit includes timings
- **WHEN** 用户以 `--audit file` 运行 CLI 且未传 `--pretty`
- **THEN** 审计记录中的 `response_times` SHALL 非空（含 `total_ms`）

#### Scenario: A turn is never duplicated
- **WHEN** config `audit.enabled=true` 且 CLI 以 `--audit file` 运行
- **THEN** 本轮 SHALL 恰好存在一条审计记录（CLI 钩子写入，编排器钩子跳过）

### Requirement: Orchestrator audit SHALL cover every answer path
当 `audit.enabled=true` 时，LangChain 编排器 SHALL 对每一次 `answer()` 调用持久化审计记录，覆盖 visual、small talk、direct answer、domain、search RAG、local RAG 和 conversation resume 路径。Web 入口（JSON 与 SSE）的请求 SHALL 同样落盘。

#### Scenario: Web request is persisted
- **WHEN** config `audit.enabled=true` 且 Web 请求（`/api/answer` 或 `/api/answer/stream`）完成应答
- **THEN** 该轮 SHALL 在审计目录新增一条记录

#### Scenario: Resume path is persisted
- **WHEN** 一轮请求命中 conversation resume 路径且审计开启
- **THEN** 该轮 SHALL 产生审计记录
- **AND** 记录的 `steps` SHALL 包含 `conversation_resume` 步骤事件

### Requirement: Audit records SHALL be self-contained and bounded
每行 SHALL 是一个完整 JSON 对象，至少包含 `ts`、`conversation_id`、`query`、`allow_search`、`steps`、`control`、`search_query`、`response_times` 和 `search_warnings`（字段存在时）。当 `audit.include_answer=true` 时 SHALL 含 `answer`；为 `false` 时 SHALL 省略。单行序列化超过 `audit.max_bytes_per_record` 时，系统 SHALL 截断最大字符串字段并标记 `truncated: true`。

#### Scenario: Complete search record
- **WHEN** 一轮带搜索的问答完成且 `include_answer=true`
- **THEN** 该行 SHALL 含 `steps`、`control.execution_trace`、`search_query`、`response_times.total_ms`、`answer`

#### Scenario: Compact record omits answer
- **WHEN** `include_answer=false`
- **THEN** 记录 SHALL NOT 含 `answer` 字段，其余字段保持不变

#### Scenario: Oversized record is marked
- **WHEN** 单行序列化长度超过 `max_bytes_per_record`
- **THEN** 系统 SHALL 截断 answer 字段并写入 `"truncated": true`

### Requirement: Audit files SHALL use bounded per-conversation JSONL retention
系统 SHALL 以 `audit.dir`（默认 `runtime/audit`）下 `<conversation_id>.jsonl` 每会话一文件追加写入；写入前 SHALL 创建目录。每次写入后，若目录内文件数超过 `audit.max_files`，系统 SHALL 按修改时间淘汰最旧文件。审计文件 SHALL NOT 新增需提交内容。

#### Scenario: Conversation appends one line per turn
- **WHEN** 同一 `conversation_id` 完成第 2 轮追问
- **THEN** 两轮记录 SHALL 位于同一 JSONL 文件的两行

#### Scenario: Oldest audit files are evicted
- **WHEN** 写入后目录文件数超过 `max_files`
- **THEN** 最旧的文件 SHALL 被删除，文件数不超过上限

### Requirement: Audit persistence SHALL be failure-safe
审计写盘 SHALL 为 best-effort：任何审计相关异常 SHALL NOT 中断、改变或延迟答案产出，至多输出一条 `[audit]` 警告。

#### Scenario: Audit directory is not writable
- **WHEN** 审计目录不可写导致写入失败
- **THEN** 问答 SHALL 正常完成并输出答案
- **AND** 系统至多打印一条 `[audit]` 警告
