# Tools

Rill builds a `ToolRegistry` at startup. Only enabled tools are exposed to the
model and to `GET /v1/tools`. The registry validates JSON Schema arguments,
applies policy, enforces timeouts and result limits, and records execution
events.

## Built-in tools

| Name | Config switch | Default | Risk and behavior |
| --- | --- | --- | --- |
| `run_terminal_command` | `[terminal].enabled` | on | High risk; runs in `terminal.workdir`; confirmation is on by default |
| `filesystem` | `[filesystem].enabled` | on | Read and write operations in its workspace; writes and deletes require approval |
| `database_query` | `[database].enabled` | on | Read-only ClickHouse queries and schema inspection |
| `generate_chart` | `[chart].enabled` | off | Generates chart files in the configured workspace |

The example configuration enables terminal, filesystem, and database tools.
Chart generation is opt-in. Docker Compose mounts `./workspace` as `/workspace`.
The database tool does not require a URL during startup; an invocation without
`database.url` returns `database_not_configured` with the required setting.

Inspect the effective registry:

```bash
uv run rill tool list
curl http://127.0.0.1:8080/v1/tools
```

## Enable a built-in tool

The CLI updates the corresponding TOML section and creates a `.bak` before the
change:

```bash
uv run rill tool enable generate_chart
uv run rill tool disable database_query
uv run rill config check
```

Database access also needs a ClickHouse URL:

```toml
[database]
enabled = true
url = "clickhouse://user:password@host:8443/database"
max_rows = 200
query_timeout_seconds = 30
allow_mutation = false
allowed_databases = ["analytics"]
```

Keep database credentials outside shared configuration when the URL can contain
secrets; use the provider or deployment secret mechanism supported by the
database driver and restrict the file permissions.

## Approval and policy

Tools declare `read_only`, `risk_level`, and `requires_approval`. These values
are returned by `/v1/tools` and are enforced again during execution.

- `run_terminal_command` is high risk. `terminal.require_confirmation = true`
  requires `approved_tools` for every request that invokes it. Risky shell
  commands remain blocked unless `terminal.allow_risky_commands = true`.
- `filesystem` supports read-only operations (`read_file`, `list_dir`, `stat`,
  `search`). Writes, deletes, moves, and directory creation require approval.
- `database_query` accepts only read-only ClickHouse statements and blocks
  mutation statements. Keep `database.allow_mutation = false`; the setting is
  retained for compatibility with the configuration model.
- `tool_policy.allowed_tools` and `denied_tools` constrain the registry before
  the model sees tool definitions. `read_only_mode` blocks write operations.

For one CLI request:

```bash
uv run rill ask \
  --approve-tool run_terminal_command \
  "list the files in the workspace"
```

`rill chat` sends the complete message history for the current session on each
turn. Use `--approve-tool` when starting chat to approve a tool for every turn:

```bash
uv run rill chat --approve-tool run_terminal_command --approve-tool filesystem
```

For HTTP clients, include tool names in `approved_tools` on every Agent request.
An interactive approval prompt is a client responsibility: a client can retry
the request with the same messages and the requested approval after showing the
tool call to a user. The server never treats a previous request's approval as a
future grant.

The OpenAI-compatible `/v1/chat/completions` endpoint does not run tools on the
server. The client must execute returned tool calls and send tool results back;
use `/v1/agent/respond` or `/v1/agent/stream` for a server-side tool loop.

For HTTP, include the same names in the request body:

```json
{
  "messages": [{"role": "user", "content": "Read README.md"}],
  "approved_tools": ["filesystem"]
}
```

The approval list is request scoped. It does not change the configuration or
grant future requests access.

## Add a custom tool

1. Read [`skills/tool-development/SKILL.md`](../skills/tool-development/SKILL.md)
   and the closest built-in implementation.
2. Create `src/rill/tools/<your_tool>.py` with a `Tool` subclass.
3. Define a unique lower snake case `name`, a concise `description`, and a
   JSON Schema `input_schema` with `additionalProperties: false` where useful.
4. Implement `async def execute(arguments)` and return a JSON serializable
   dictionary containing `ok` plus structured output or an error.
5. Set policy metadata accurately. Override `is_read_only(arguments)` and
   `approval_required(arguments)` when the tool has both read and write modes.
6. Import and append it in `build_registry(settings)` in
   `src/rill/tools/registry.py`. Add a settings section and an
   `enabled` switch when the tool needs configuration.
7. Add its switch and safe defaults to `config.toml.example`.
8. Add focused tests for malformed arguments, policy denial, successful
   execution, timeouts, and result limits that apply to the tool.
9. Verify metadata and the complete runtime:

```bash
uv run rill tool list
uv run pytest
uv run python -m compileall -q src tests
git diff --check
```

Minimal shape:

```python
from typing import Any

from rill.config import Settings
from rill.tools.base import Tool


class ExampleTool(Tool):
    name = "example_tool"
    description = "Return one bounded example value."
    input_schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    read_only = True
    risk_level = "low"

    def __init__(self, settings: Settings) -> None:
        self.timeout_seconds = settings.agent.tool_timeout_seconds
        self.max_result_bytes = settings.agent.max_result_bytes

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "value": arguments["value"]}
```

Keep external calls bounded, validate paths before side effects, and never put
credentials in tool arguments, TOML, or logs. A tool must remain behind the
registry so allow/deny policy, approval, timeout, and result-size checks apply
consistently.
