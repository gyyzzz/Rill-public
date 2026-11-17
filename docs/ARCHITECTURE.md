# 架构

## API 层

- `/v1/chat/completions` makes one OpenAI-compatible model turn and returns tool
  calls to the client without executing them.
- `/v1/agent/respond` and `/v1/agent/stream` run the server-side Agent loop.
- `/v1/models` and `/v1/providers` expose configured model/provider profiles.
- `/v1/upload` and `/v1/workspace/*` provide bounded workspace file access.
- `/health/live` and `/health/ready` support process and deployment checks.

## 运行时与模型层

`AgentService` owns iteration, event creation, limits, and tool execution.
`ModelRouter` selects a provider profile and model. `ProviderAdapter` defines
the adapter boundary; `OpenAICompatibleClient` implements the HTTP protocol,
retry behavior, model listing, and provider check. Capability profiles select
native Tool Call, JSON fallback, or no-tools mode.

## 工具层

`ToolRegistry` discovers tools, applies requested and configured allow/deny
policies, validates required arguments and basic JSON Schema types, limits
execution time and result size, and records execution events. Built-in terminal
and filesystem tools preserve their workspace boundaries. Terminal, filesystem,
and database tools are enabled by default; chart generation is opt-in. The
database tool can be enabled without a connection URL and reports a structured
configuration error when called.

## 配置优先级

Defaults are overridden by TOML, then `.env`, then process environment
variables, then explicit settings passed by the caller. Nested environment
variables use `ORIONXCORE_SECTION__FIELD`. The original `[llm]` fields remain
supported while provider profiles are introduced. Secrets may be supplied
through environment variables or `api_key_file` paths such as
`/run/secrets/llm_api_key`.
