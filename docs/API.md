# OrionXCore API 接口文档

本文档描述 OrionXCore 服务提供的所有 HTTP 接口，供客户端对接参考。

## 目录

- [服务信息](#服务信息)
- [Agent 接口](#agent-接口)
- [Chat Completions 接口](#chat-completions-接口)
- [工具接口](#工具接口)
- [事件类型说明](#事件类型说明)
- [多轮对话对接指南](#多轮对话对接指南)
- [错误处理](#错误处理)

---

## 服务信息

### 根路径

```
GET /
```

返回服务基本信息。

**响应示例**：

```json
{
  "service": "OrionXCore",
  "docs_hint": "Use /health, /v1/tools, /v1/agent/respond, /v1/agent/stream, or /playground."
}
```

### 健康检查

```
GET /health
```

返回服务健康状态和工具启用情况。

**响应示例**：

```json
{
  "name": "OrionXCore",
  "status": "ok",
  "terminal_enabled": true,
  "database_enabled": true
}
```

### Playground

```
GET /playground
```

返回一个交互式调试页面，可在浏览器中测试 API。

---

## Agent 接口

### 同步请求

```
POST /v1/agent/respond
```

发送消息给 Agent，Agent 会自动执行多轮工具调用直到完成任务。

**请求体**：

```json
{
  "messages": [
    {"role": "user", "content": "列出 monitor 库中的表"}
  ],
  "session_id": "optional-session-id",
  "metadata": {}
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `messages` | array | ✅ | 消息列表，OpenAI 格式 |
| `session_id` | string | ❌ | 会话 ID，用于追踪 |
| `metadata` | object | ❌ | 元数据，扩展字段 |

**响应体**：

```json
{
  "message": {
    "role": "assistant",
    "content": "monitor 库包含以下表：..."
  },
  "events": [
    {"type": "iteration", "payload": {"iteration": 1}},
    {"type": "tool_call", "payload": {"name": "database_query", "arguments": {...}}},
    {"type": "tool_result", "payload": {"name": "database_query", "result": {...}}}
  ],
  "iterations": 2,
  "session_id": "optional-session-id"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `message` | object | Agent 最终回复消息 |
| `events` | array | 执行过程中的事件列表 |
| `iterations` | int | 执行迭代次数 |
| `session_id` | string | 会话 ID |

### 流式请求

```
POST /v1/agent/stream
```

以 SSE (Server-Sent Events) 格式返回 Agent 执行过程中的实时事件。

**请求体**：与 `/v1/agent/respond` 相同。

**响应格式**（SSE）：

```
event: iteration
data: {"iteration": 1}

event: tool_call
data: {"name": "database_query", "arguments": {"operation": "text_to_sql", "question": "..."}}

event: tool_result
data: {"name": "database_query", "result": {"ok": true, "row_count": 5, ...}}

event: database_trace
data: {"question": "...", "database": "monitor", "tables": [...], "final_sql": "SELECT ..."}

event: database_result_summary
data: {"ok": true, "row_count": 5, "columns": [...], "generated_sql": "SELECT ..."}

event: assistant
data: {"content": "monitor 库包含以下表：..."}

event: final
data: {"message": {...}, "iterations": 2, "session_id": "..."}
```

---

## Chat Completions 接口

OpenAI 兼容接口，支持工具调用。

### 同步请求

```
POST /v1/chat/completions
```

**请求体**：

```json
{
  "model": "gpt-4",
  "messages": [
    {"role": "user", "content": "检查当前目录"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "run_terminal_command",
        "description": "执行 Shell 命令",
        "parameters": {
          "type": "object",
          "properties": {
            "command": {"type": "string"}
          },
          "required": ["command"]
        }
      }
    }
  ],
  "tool_choice": "auto"
}
```

**响应体**：

```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "gpt-4",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_xxx",
            "type": "function",
            "function": {
              "name": "run_terminal_command",
              "arguments": "{\"command\": \"ls -la\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ],
  "usage": {
    "prompt_tokens": 50,
    "completion_tokens": 20,
    "total_tokens": 70
  }
}
```

### 流式请求

设置 `stream: true` 即可启用 SSE 流式输出。

**请求体**：

```json
{
  "model": "gpt-4",
  "messages": [...],
  "stream": true
}
```

**响应格式**（SSE）：

```
data: {"id": "chatcmpl-xxx", "object": "chat.completion.chunk", ...}

data: {"id": "chatcmpl-xxx", "delta": {"content": "Hello"}, ...}

data: [DONE]
```

---

## 工具接口

### 获取工具列表

```
GET /v1/tools
```

返回当前启用的工具及其参数 schema。

**响应示例**：

```json
[
  {
    "name": "run_terminal_command",
    "description": "Execute a shell command in the configured workspace...",
    "input_schema": {
      "type": "object",
      "properties": {
        "command": {"type": "string", "description": "Shell command to execute."},
        "timeout": {"type": "integer", "minimum": 1}
      },
      "required": ["command"]
    }
  },
  {
    "name": "database_query",
    "description": "Run read-only queries and schema introspection...",
    "input_schema": {
      "type": "object",
      "properties": {
        "operation": {"type": "string", "enum": ["query", "list_tables", "describe_table", "text_to_sql"]},
        "question": {"type": "string"},
        "statement": {"type": "string"},
        "database": {"type": "string"},
        "table": {"type": "string"}
      },
      "required": []
    }
  },
  {
    "name": "filesystem",
    "description": "Perform safe file system operations...",
    "input_schema": {
      "type": "object",
      "properties": {
        "operation": {"type": "string", "enum": ["read_file", "write_file", "list_dir", ...]},
        "path": {"type": "string"},
        "content": {"type": "string"}
      },
      "required": ["operation"]
    }
  }
]
```

---

## 事件类型说明

Agent 执行过程中会产生以下事件类型：

| 事件类型 | 说明 | payload 内容 |
|----------|------|-------------|
| `iteration` | 迭代开始 | `{"iteration": 1}` |
| `tool_call` | 工具调用 | `{"name": "工具名", "arguments": {...}}` |
| `tool_result` | 工具返回 | `{"name": "工具名", "result": {...}}` |
| `assistant` | AI 回复 | `{"content": "回复内容"}` |
| `final` | 最终结果 | `{"message": {...}, "iterations": N}` |

### 数据库特有事件

| 事件类型 | 说明 |
|----------|------|
| `database_trace` | SQL 执行追踪信息 |
| `database_schema_context` | Schema 上下文 |
| `database_sql_attempt` | SQL 尝试记录 |
| `database_result_summary` | 查询结果摘要 |

**database_result_summary 示例**：

```json
{
  "ok": true,
  "row_count": 10,
  "columns": ["hostname", "ip", "status"],
  "generated_sql": "SELECT hostname, ip, status FROM monitor.hosts LIMIT 10"
}
```

---

## 多轮对话对接指南

### 方案一：使用 Agent 接口（推荐）

Agent 接口自动处理工具调用循环，客户端只需发送消息。

```python
import requests

def chat(messages, session_id=None):
    response = requests.post(
        "http://localhost:8080/v1/agent/respond",
        json={"messages": messages, "session_id": session_id}
    )
    return response.json()

# 多轮对话
messages = [{"role": "user", "content": "列出数据库中的表"}]
result = chat(messages)

# 继续对话
messages.append({"role": "assistant", "content": result["message"]["content"]})
messages.append({"role": "user", "content": "查询 hosts 表前5条"})
result = chat(messages)
```

### 方案二：使用 Chat Completions 接口

需要客户端自行处理工具调用循环。

```python
def chat_with_tools(messages):
    response = requests.post(
        "http://localhost:8080/v1/chat/completions",
        json={
            "messages": messages,
            "tools": tools_schema,
            "tool_choice": "auto"
        }
    )
    result = response.json()
    
    # 如果需要工具调用
    if result["choices"][0]["finish_reason"] == "tool_calls":
        tool_calls = result["choices"][0]["message"]["tool_calls"]
        
        # 执行工具（需要调用 /v1/agent/respond 或本地实现）
        for call in tool_calls:
            # ... 执行工具并获取结果
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "name": call["function"]["name"],
                "content": tool_result
            })
        
        # 继续调用
        return chat_with_tools(messages)
    
    return result
```

### 历史消息传递

传递历史消息时，确保格式正确：

```python
history = [
    {"role": "user", "content": "问题1"},
    {"role": "assistant", "content": "回答1"},
    {"role": "user", "content": "问题2"},
    {"role": "assistant", "content": "回答2"},
    {"role": "user", "content": "新问题"}
]

response = requests.post(
    "http://localhost:8080/v1/agent/respond",
    json={"messages": history}
)
```

---

## 错误处理

### HTTP 状态码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 500 | 服务内部错误 |

### 工具执行错误

工具执行失败不会导致 HTTP 500，而是返回 `ok: false`：

```json
{
  "ok": false,
  "error": "SQL execution error: Missing column 'hostname'",
  "sql": "SELECT hostname FROM ..."
}
```

Agent 会根据错误信息尝试重试或调整策略。

### SQL 自动重试

`text_to_sql` 操作会自动重试：

1. 第一次生成 SQL 并执行
2. 失败后，携带错误信息重新生成 SQL
3. 第二次执行，返回结果（无论成功或失败）

响应中包含 `attempts` 信息：

```json
{
  "ok": false,
  "generated_sql": "SELECT ...",
  "attempts": 2,
  "previous_sql": "第一次的 SQL",
  "previous_error": "第一次的错误",
  "trace": {
    "attempt_count": 2,
    "attempts": [
      {"attempt": 1, "sql": "...", "error": "..."},
      {"attempt": 2, "sql": "..."}
    ]
  }
}
```

---

## 安全说明

### Terminal 工具风险

Terminal 工具可执行任意命令，存在绕过风险检测的可能：

- 风险检测仅覆盖极端命令（`rm -rf /`、`sudo` 等）
- 命令可用绝对路径访问系统文件
- 命令可组合执行危险操作

**建议**：生产环境禁用 Terminal，或使用容器隔离。

### FileSystem 沙箱

FileSystem 工具有强制路径沙箱：

- 所有操作限制在 `workdir` 内
- 路径穿越（`..`）会被拒绝
- 相比 Terminal 更安全

### Database 只读

默认只允许只读 SQL：

- `SELECT`、`SHOW`、`DESCRIBE` 等允许
- `INSERT`、`UPDATE`、`DELETE` 需要配置 `ALLOW_MUTATION=true`

---

## 附录：完整请求示例

### cURL 示例

```bash
# 健康检查
curl http://localhost:8080/health

# 获取工具列表
curl http://localhost:8080/v1/tools

# Agent 请求
curl -X POST http://localhost:8080/v1/agent/respond \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "列出 monitor 库的表"}]}'

# 流式请求
curl -N -X POST http://localhost:8080/v1/agent/stream \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "检查当前目录"}]}'
```

### Python 示例

```python
import requests
import json

BASE_URL = "http://localhost:8080"

# Agent 请求
def agent_request(messages, session_id=None):
    response = requests.post(
        f"{BASE_URL}/v1/agent/respond",
        json={"messages": messages, "session_id": session_id},
        timeout=120
    )
    return response.json()

# 流式请求
def agent_stream(messages):
    response = requests.post(
        f"{BASE_URL}/v1/agent/stream",
        json={"messages": messages},
        stream=True,
        timeout=120
    )
    for line in response.iter_lines():
        if line:
            event_type = line.decode().split("event: ")[1] if "event:" in line.decode() else None
            data_line = next(response.iter_lines())
            if data_line and "data:" in data_line.decode():
                data = json.loads(data_line.decode().split("data: ")[1])
                yield event_type, data

# 使用示例
result = agent_request([{"role": "user", "content": "列出数据库表"}])
print(result["message"]["content"])
```

### JavaScript 示例

```javascript
const BASE_URL = 'http://localhost:8080';

// Agent 请求
async function agentRequest(messages, sessionId = null) {
  const response = await fetch(`${BASE_URL}/v1/agent/respond`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, session_id: sessionId })
  });
  return response.json();
}

// 流式请求
async function agentStream(messages, onEvent) {
  const response = await fetch(`${BASE_URL}/v1/agent/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages })
  });
  
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    
    const text = decoder.decode(value);
    const lines = text.split('\n');
    
    for (const line of lines) {
      if (line.startsWith('event:')) {
        const eventType = line.slice(7);
        const dataLine = lines[lines.indexOf(line) + 1];
        if (dataLine?.startsWith('data:')) {
          const data = JSON.parse(dataLine.slice(5));
          onEvent(eventType, data);
        }
      }
    }
  }
}

// 使用示例
agentRequest([{ role: 'user', content: '列出数据库表' }])
  .then(result => console.log(result.message.content));
```