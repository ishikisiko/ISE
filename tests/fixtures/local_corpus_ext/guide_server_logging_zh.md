# Durable Server Logs

当 Web 部署需要长期保留每一次可观测的 answer 请求与进程消息时，启用下面两个配置块。值为 `0` 表示关闭审计记录大小与文件数量上限。

```json
{
  "audit": {
    "enabled": true,
    "dir": "runtime/audit",
    "include_answer": true,
    "include_full_result": true,
    "max_files": 0,
    "max_bytes_per_record": 0
  },
  "server_logging": {
    "enabled": true,
    "dir": "runtime/server",
    "capture_stdio": true,
    "include_request_payload": true,
    "include_response_payload": true
  }
}
```

## 落盘内容

`runtime/server/` 包含：

- `server.log` / `stdout.log` / `stderr.log`
- `access.jsonl`
- `requests/<request-id>.jsonl`：每个 answer 请求的完整事件流

每个请求流记录：收到的 payload、归一化后的 context、每一个 workflow event、最终响应以及错误 traceback。

`runtime/audit/<conversation-id>.jsonl` 保留按会话的最终记录。

两个目录均 gitignored。类凭据字段与 URL query string 在写入 JSONL 前会被脱敏。
