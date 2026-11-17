# 配置

Rill 会读取一个 TOML 文件以及可选的环境变量。默认文件为项目根目录下的
`config.toml`。可通过 `ORIONXCORE_CONFIG_FILE` 或全局 CLI 选项 `--config-file` 指定
其他文件。

## 优先级

配置值按以下顺序应用，优先级从低到高：

1. Rill defaults
2. `config.toml`
3. `.env` beside the selected TOML file
4. Process environment variables
5. Explicit settings passed by an embedding application

环境变量名称使用配置节和字段路径，并以 `__` 分隔：

```text
ORIONXCORE_<SECTION>__<FIELD>=value
```

例如：

```bash
export ORIONXCORE_LLM__MODEL=gpt-4.1-mini
export ORIONXCORE_SERVER__PORT=8080
export ORIONXCORE_TERMINAL__ENABLED=false
```

环境变量会按目标字段类型解析。列表和对象可以使用 JSON 提供，例如：

```bash
export ORIONXCORE_AUTH__API_KEYS='["a-long-random-key"]'
```

完整的起始配置文件见 [`config.toml.example`](../config.toml.example)。

默认运行环境会启用 `terminal`、`filesystem` 和 `database`；`chart` 默认关闭，需显式
启用。数据库配置采用延迟校验：启动时 `database.url` 为空是有效配置，只有调用数据库
工具时才会返回 `database_not_configured`。

## CLI 服务地址

CLI 的 `ask`、`chat` 和 `status` 命令使用 `[client].base_url`。如果未设置，CLI 会
使用 `[server].port` 连接 `http://127.0.0.1`。此客户端地址与服务器绑定地址相互独立。

```toml
[client]
base_url = "http://127.0.0.1:18080"
```

如需为单条命令覆盖地址，请将 `--base-url` 放在命令之前：

```bash
uv run rill --base-url http://127.0.0.1:18080 ask "List workspace files."
```

## 初始设置

```bash
cp config.toml.example config.toml
printf '%s\n' 'ORIONXCORE_LLM__API_KEY=replace-me' > .env
uv run rill config check
uv run rill config show
```

`config show` redacts credentials and sanitizes provider URLs. The CLI also
supports a starter file and atomic edits:

```bash
uv run rill init --non-interactive
uv run rill config set llm.model gpt-4.1-mini
uv run rill config backup
uv run rill config restore
```

Do not pass secrets to `config set`; it rejects paths containing `api_key`,
`password`, `secret`, or `token`.

## 模型与 Provider

The compatibility fields under `[llm]` are the shortest configuration:

```toml
[llm]
provider = "default"
model = "gpt-4.1-mini"
api_base_url = "https://api.openai.com/v1"
http_timeout = 120
retries = 2

[llm.model_aliases]
fast = "gpt-4.1-mini"
```

The API key belongs in `ORIONXCORE_LLM__API_KEY` or a secret file:

```bash
export ORIONXCORE_LLM__API_KEY=replace-me
export ORIONXCORE_LLM__API_KEY_FILE=/run/secrets/llm_api_key
```

Use provider profiles when one Rill instance needs multiple compatible APIs:

```toml
[llm]
provider = "work"

[llm.providers.work]
type = "openai_compatible"
api_base_url = "https://llm.example.com/v1"
default_model = "work-model"
models = ["work-model"]
retries = 2
fallback_provider = "default"

[llm.providers.default]
api_base_url = "https://api.openai.com/v1"
default_model = "gpt-4.1-mini"
```

Set the profile key outside TOML:

```bash
export ORIONXCORE_LLM__PROVIDERS__WORK__API_KEY=replace-me
uv run rill provider list
uv run rill provider test work
uv run rill provider use work
```

Or add a profile through the CLI. The command prompts for the key when run in
an interactive terminal, or reads the matching environment variable:

```bash
export ORIONXCORE_LLM__PROVIDERS__LOCAL__API_KEY=local-key
uv run rill provider add local \
  --base-url http://127.0.0.1:11434/v1 \
  --model llama3.1
uv run rill provider use local
```

Provider URLs must be HTTP(S) URLs without embedded credentials, query strings,
or fragments. The provider name is also available in an Agent request through
the optional `provider` field, and model aliases resolve before the request is
sent.

## Agent 档案与执行

The `[agent]` section controls the server-side loop:

```toml
[agent]
max_iterations = 6
max_tool_calls = 24
tool_timeout_seconds = 60
execution_mode = "auto" # auto, native_tool_call, json_fallback, no_tools
max_concurrent_requests = 4
queue_timeout_seconds = 5.0

[agent.profiles.read_only]
read_only = true
allowed_tools = ["filesystem", "database_query"]
execution_mode = "native_tool_call"
```

Select a profile with the Agent request field `profile`. `auto` uses native
provider tool calls when the provider advertises them, otherwise it uses the
JSON fallback. JSON fallback keeps write tools disabled unless
`tool_policy.fallback_allow_write_tools = true`.

## 认证与 CORS

Authentication is disabled by default for local development. For a reachable
deployment, configure at least one key and turn it on:

```toml
[auth]
enabled = true
allow_local_without_key = false
```

```bash
export ORIONXCORE_AUTH__API_KEYS='["replace-with-a-long-random-key"]'
```

Use either header on protected routes:

```bash
curl -H 'Authorization: Bearer replace-with-a-long-random-key' \
  http://127.0.0.1:8080/v1/tools

curl -H 'X-API-Key: replace-with-a-long-random-key' \
  http://127.0.0.1:8080/v1/tools
```

Keys can be scoped with `agent_api_keys`, `tool_api_keys`, and
`diagnostic_api_keys`. A key in `api_keys` is accepted for every scope. Agent
requests use the `agent` scope; uploads and workspace files use `tool`; health
diagnostics, models, providers, and tool discovery use `diagnostic`.

For a browser client, replace the wildcard with the exact origins you operate:

```toml
[cors]
enabled = true
allow_origins = ["https://app.example.com"]
allow_credentials = false
```

Credentials cannot be enabled while `allow_origins` contains `"*"`.

## 工具策略

Use a global policy to constrain every Agent request:

```toml
[tool_policy]
read_only_mode = true
allowed_tools = ["filesystem", "database_query"]
denied_tools = ["run_terminal_command"]
require_approval_for_risky = true
max_calls_per_request = 12
```

`allowed_tools` is an allowlist when non-empty. `denied_tools` always wins for
the named tool. `read_only_mode` removes write operations from the registry;
tool-specific operation checks still apply. Per-request approval is supplied
with the `approved_tools` array in an Agent request or the CLI option:

```bash
uv run rill ask \
  --approve-tool run_terminal_command \
  --approve-tool filesystem \
  "show the current directory"
```

## Docker 密钥

Mount a secret and point the setting to its file. The file is read at startup
and its contents are never written to the TOML configuration:

```toml
[llm]
api_key_file = "/run/secrets/llm_api_key"
```

The same `api_key_file` field is available on each provider profile. See
[`DEPLOYMENT.md`](DEPLOYMENT.md) for the Compose layout.
