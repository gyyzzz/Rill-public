# Rill API 接口

默认地址是 `http://127.0.0.1:8080`。如果使用其他端口，替换下面示例中的
`RILL_URL`：

```bash
export RILL_URL=http://127.0.0.1:8080
```

## 认证

认证由 `[auth]` 配置控制，默认关闭。启用后，使用全局 API Key 或对应权限的
Scoped Key，并选择一种请求头：

```bash
curl "$RILL_URL/v1/models" \
  -H 'Authorization: Bearer replace-with-a-long-random-key'

curl "$RILL_URL/v1/models" \
  -H 'X-API-Key: replace-with-a-long-random-key'
```

权限范围如下：

| 权限 | 保护的接口 |
| --- | --- |
| `agent` | `/v1/agent/*`、`/v1/chat/completions` |
| `tool` | `/v1/upload`、`/v1/workspace/*` |
| `diagnostic` | `/v1/tools`、`/v1/models`、`/v1/providers`、`/v1/status`、能力探测 |

`/health`、`/health/live`、`/health/ready` 和 `/playground` 不要求 API Key。
本地回环请求是否可以免 Key 由 `auth.allow_local_without_key` 控制。

## 健康检查

```http
GET /health
GET /health/live
GET /health/ready
```

`/health/live` 只表示进程存活。`/health/ready` 检查模型配置、终端工作目录和
文件系统工作目录；数据库没有配置 URL 不会导致服务未就绪。

示例：

```bash
curl "$RILL_URL/health/ready"
```

## 发现和状态

```http
GET  /v1/tools
GET  /v1/models
GET  /v1/providers
GET  /v1/status
POST /v1/providers/{provider_name}/check
POST /v1/capabilities/refresh
```

这些接口需要 `diagnostic` 权限。能力刷新请求体：

```json
{
  "provider": "default",
  "model": "gpt-4.1-mini"
}
```

`GET /v1/tools` 返回工具的名称、JSON Schema、只读标记、风险级别、审批要求、
超时和结果大小限制。调用方可以先读取它，再决定是否展示审批界面。

## Agent API

### `POST /v1/agent/respond`

服务端执行完整 Agent 循环：模型调用、工具调用、工具结果回传，直到得到最终
回答或达到 `agent.max_iterations`、`agent.max_tool_calls` 等限制。

最小请求：

```bash
curl -sS -X POST "$RILL_URL/v1/agent/respond" \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [
      {"role": "user", "content": "列出当前工作区中的文件。"}
    ]
  }'
```

常用请求字段：

| 字段 | 说明 |
| --- | --- |
| `messages` | 对话消息数组，必填 |
| `session_id` | 客户端会话标识，原样返回；服务端不自动保存历史 |
| `model` / `provider` | 覆盖本次请求的模型或 Provider |
| `profile` | 使用 `[agent.profiles.<name>]` |
| `execution_mode` | `auto`、`native_tool_call`、`json_fallback` 或 `no_tools` |
| `tools` | 限制本次请求可见的工具定义 |
| `approved_tools` | 本次请求允许执行的工具名称数组 |
| `tool_choice` | 传递给支持该能力的模型 |
| `parallel_tool_calls` | 请求并行工具调用 |

审批示例：

```json
{
  "messages": [
    {"role": "user", "content": "读取 README.md 并总结。"}
  ],
  "approved_tools": ["filesystem"]
}
```

文件系统的读取操作通常不需要审批；写入、删除、移动和创建目录需要
`filesystem`。终端工具默认需要 `run_terminal_command`。审批只对当前请求有效。
`rill chat` 会在终端展示审批提示；其他客户端应在展示工具调用后，使用相同
消息重新请求并填入审批列表。

响应包含 `message`、`events`、`iterations`、`session_id`、`request_id`、
`provider`、`model`、`execution_mode` 和 `stop_reason`。事件包括模型调用、工具
调用、工具结果、迭代和最终响应。

### `POST /v1/agent/stream`

请求体与 `/v1/agent/respond` 相同，响应类型是 `text/event-stream`。每个事件由
两行组成：

```text
event: tool_call
data: {"id":"call-1","name":"filesystem","arguments":{...}}

```

常见事件类型：`request_started`、`model_started`、`model_completed`、
`tool_call`、`tool_result`、`iteration`、`final` 和 `error`。

```bash
curl -N -X POST "$RILL_URL/v1/agent/stream" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"检查工作区。"}]}'
```

## OpenAI 兼容 API

### `POST /v1/chat/completions`

该接口执行一轮模型调用，适合已有 OpenAI SDK 的客户端。非流式请求：

```bash
curl -sS -X POST "$RILL_URL/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-4.1-mini",
    "messages": [{"role": "user", "content": "总结这个项目。"}]
  }'
```

设置 `"stream": true` 时返回 SSE 数据块，并以 `data: [DONE]` 结束：

```bash
curl -N -X POST "$RILL_URL/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-4.1-mini",
    "stream": true,
    "messages": [{"role": "user", "content": "总结这个项目。"}]
  }'
```

该接口不会在服务端自动执行工具。若响应包含 `tool_calls`，客户端负责执行
工具并把 assistant 工具调用消息和 tool 结果消息追加到下一次请求；需要服务端
自动完成工具循环时使用 Agent API。

## 文件接口

```http
POST /v1/upload
GET  /v1/workspace/{filepath}
```

两者需要 `tool` 权限。上传使用 `multipart/form-data` 的 `file` 字段：

```bash
curl -X POST "$RILL_URL/v1/upload" \
  -H 'X-API-Key: replace-with-a-long-random-key' \
  -F 'file=@README.md'
```

上传大小、扩展名和工作区由 `[upload]` 控制；工作区文件接口拒绝逃逸工作区根
目录的路径。

## 错误

错误响应统一为：

```json
{
  "error": {
    "code": "invalid_request",
    "message": "Request validation failed."
  }
}
```

常见状态码：

| 状态码 | 含义 |
| --- | --- |
| `400` | 请求字段、Provider 或 Agent 配置无效 |
| `401` | 缺少或无效 API Key |
| `403` | 接口或文件操作被策略禁止 |
| `404` | Provider 或工作区文件不存在 |
| `413` | 上传文件超过大小限制 |
| `422` | 请求体 Schema 校验失败 |
| `429` | Agent 并发容量或排队超时 |
| `502` | 上游模型 Provider 不可用 |
| `503` | 服务尚未就绪 |

每个响应包含 `x-request-id`，可用于关联日志和 Agent 事件。
