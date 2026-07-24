# process-audit-log Specification

## Purpose
Define a toggleable, persisted per-turn process audit: workflow step events, routing/postcheck control metadata, search queries, timing payloads, warnings, and optional answer text, written as JSONL keyed by `conversation_id`, covering both the CLI (`main.py`) and Web (`server.py`) entry points, with bounded retention and strictly best-effort writes.

## ADDED Requirements

### Requirement: 审计开关解析
系统 SHALL 按优先级解析审计开关：CLI `--audit off|file` 单次覆盖 > `config.json` 的 `audit.enabled` > 默认关闭。配置块缺省时系统 SHALL 视为关闭。CLI 传 `--audit off` 时即使配置启用，本次 CLI 运行 SHALL NOT 产生任何审计写盘。

#### Scenario: 默认关闭
- **WHEN** 用户未传 `--audit` 且 config 无 `audit` 块
- **THEN** 系统 SHALL 维持现状行为（CLI 只输出答案，不产生审计文件）
- **AND** 编排器 SHALL 使用 `NullWorkflowTracer`（CLI 侧），无额外文件 I/O

#### Scenario: CLI 覆盖配置
- **WHEN** config `audit.enabled=true` 且用户传 `--audit off`
- **THEN** 本次 CLI 运行 SHALL NOT 写入审计记录

#### Scenario: 配置启用
- **WHEN** config `audit.enabled=true` 且 CLI 未传 `--audit`
- **THEN** CLI 运行 SHALL 写入审计记录

### Requirement: CLI 审计接线
审计开启时，`main.py` SHALL 创建真实 `WorkflowTracer` 并传入 `orchestrator.answer()`，且 SHALL 强制启用耗时采集（等效 `show_timings=True`），并在 `answer()` 返回后写出本轮审计记录。同一轮 CLI 运行 SHALL 恰好产生一条审计记录（编排器内钩子不得重复写）。

#### Scenario: CLI 成功轮落盘
- **WHEN** 用户以 `--audit file` 运行 CLI 且问答成功
- **THEN** 审计目录下该 `conversation_id` 对应的 JSONL 文件 SHALL 新增恰好一行
- **AND** stdout 的答案输出与现有格式保持一致

#### Scenario: CLI 审计含耗时
- **WHEN** 用户以 `--audit file` 运行 CLI 且未传 `--pretty`
- **THEN** 审计记录中的 `response_times` SHALL 非空（含 `total_ms`）

#### Scenario: 同一轮不重复写
- **WHEN** config `audit.enabled=true` 且 CLI 以 `--audit file` 运行
- **THEN** 本轮 SHALL 恰好存在一条审计记录（CLI 钩子写入，编排器钩子跳过）

### Requirement: 编排器审计接线与全路径覆盖
当 `audit.enabled=true` 时，LangChain 编排器 SHALL 对每一次 `answer()` 调用持久化审计记录，覆盖全部返回路径（visual、small talk、direct answer、domain、search RAG、local RAG、以及 conversation resume 续跑路径）。Web 入口（JSON 与 SSE）的请求 SHALL 同样落盘。

#### Scenario: Web 请求落盘
- **WHEN** config `audit.enabled=true` 且 Web 请求（`/api/answer` 或 `/api/answer/stream`）完成应答
- **THEN** 该轮 SHALL 在审计目录新增一条记录

#### Scenario: 续跑路径落盘
- **WHEN** 一轮请求命中 conversation resume（多轮追问）路径且审计开启
- **THEN** 该轮 SHALL 产生审计记录
- **AND** 记录的 `steps` SHALL 包含 `conversation_resume` 步骤事件

### Requirement: 审计记录内容与格式
每行 SHALL 是一个完整 JSON 对象，至少包含：`ts`（ISO 时间戳）、`conversation_id`、`query`、`allow_search`、`steps`（tracer 事件数组，保留 `seq/id/title/status/detail/duration_ms/items/badge` 字段）、`control`（路由与 postcheck 元数据，若存在）、`search_query`（若存在）、`response_times`（若存在）、`search_warnings`（若存在）。当 `audit.include_answer=true`（默认）时 SHALL 含 `answer` 全文；为 `false` 时 SHALL 省略。单行序列化超过 `audit.max_bytes_per_record` 时，系统 SHALL 截断最大字符串字段（answer 优先）并标记 `truncated: true`。

#### Scenario: 记录自包含
- **WHEN** 一轮带搜索的问答完成且 `include_answer=true`
- **THEN** 该行 SHALL 含 `steps`、`control.postcheck`、`search_query`、`response_times.total_ms`、`answer`

#### Scenario: 精简模式
- **WHEN** `include_answer=false`
- **THEN** 记录 SHALL NOT 含 `answer` 字段，其余字段保持不变

#### Scenario: 超长截断
- **WHEN** 单行序列化长度超过 `max_bytes_per_record`
- **THEN** 系统 SHALL 截断 answer 字段并写入 `"truncated": true`

### Requirement: 审计文件布局与保留
系统 SHALL 以 `audit.dir`（默认 `runtime/audit`）下 `<conversation_id>.jsonl` 每会话一文件追加写入；写入前 SHALL 创建目录。每次写入后，若目录内文件数超过 `audit.max_files`（默认 200），系统 SHALL 按修改时间淘汰最旧文件。`runtime/` 已被 gitignore，审计文件 SHALL NOT 新增需提交内容。

#### Scenario: 多轮追加同文件
- **WHEN** 同一 `conversation_id` 完成第 2 轮追问
- **THEN** 两轮记录 SHALL 位于同一 JSONL 文件的两行

#### Scenario: LRU 淘汰
- **WHEN** 写入后目录文件数超过 `max_files`
- **THEN** 最旧的文件 SHALL 被删除，文件数不超过上限

### Requirement: 失败安全
审计写盘 SHALL 为 best-effort：任何审计相关异常 SHALL NOT 中断、改变或延迟答案产出，至多输出一条 `[audit]` 警告。

#### Scenario: 目录不可写
- **WHEN** 审计目录不可写导致写入失败
- **THEN** 问答 SHALL 正常完成并输出答案
- **AND** 系统至多打印一条 `[audit]` 警告
