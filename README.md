# Rill

```text
 █▀█ █ █   █
 █▀▄ █ █   █
 ▀ ▀ ▀ ▀▀▀ ▀▀▀
```

**Less is more. Simplicity is beauty.**

Rill is a small self-hosted Agent Gateway. Give it an OpenAI-compatible model
and API key; Rill runs one or more model turns, executes approved tools, and
returns the result over HTTP, SSE, or the CLI:

```text
client -> Rill -> model
                 |
                 +-> tools
```

One service, one config file, one model endpoint. `/v1/chat/completions` makes
one compatible model turn; `/v1/agent/*` runs the server-side Agent loop.

## Why Rill?

| Need | Recommendation | Why |
| --- | --- | --- |
| A polished AI coding agent for your terminal | **Claude Code or Codex** | Better client experience and coding workflows |
| An IDE coding assistant | **GitHub Copilot or an IDE-native agent** | Deep editor integration |
| A self-hosted Agent API for your own app | **Rill** | One small service, any OpenAI-compatible model |
| A server-side tool loop behind a Web UI, script, or plugin | **Rill** | HTTP/SSE in, tool events out |
| A private ClickHouse natural-language query service | **Rill** | Built-in schema-aware Text-to-SQL tool |
| A small-team operations assistant or automation service | **Rill** | Small deployment and explicit tool policy |
| A high-concurrency distributed Agent platform | **A managed platform** | Rill has no distributed scheduler or elastic cluster |
| A fully managed AI platform | **A managed provider** | Less infrastructure to operate |

Rill is intentionally not a replacement for Claude Code or Codex. It is the
small service to put behind your own clients, models, and tools.

## Architecture

```text
CLI / Web UI / IDE plugin / script
                 |
            REST / SSE
                 v
        +-------------------+
        |       Rill        |
        | API + Agent loop  |
        | Provider adapter  |
        | Tool registry     |
        +---------+---------+
                  |
        +---------+----------+
        |                    |
   OpenAI-compatible API  Tools
                         terminal
                         filesystem
                         ClickHouse
                         charts
```

The compatibility API makes one model turn. The Agent API runs the loop and
returns execution events. See [Architecture](docs/ARCHITECTURE.md) for the
full boundary and event model.

## Quickstart

### Local

Copy and run this from the repository root:

```bash
uv sync
cp config.toml.example config.toml
cp .env.example .env
```

Set `[llm].model` and `[llm].api_base_url` in `config.toml`, then put the
provider key in `.env`:

```bash
ORIONXCORE_LLM__API_KEY=replace-me
```

Start the service from the configuration:

```bash
uv run rill config check
uv run rill serve
```

`rill serve` reads `[server].host` and `[server].port` and defaults to
`0.0.0.0:8080`. Uvicorn can still override them explicitly.

In another terminal:

```bash
uv run rill ask "List the files in the current workspace."
uv run rill chat
curl http://127.0.0.1:8080/health/ready
open http://127.0.0.1:8080/playground
```

Replace `replace-me` with an API key for the provider in `config.toml`.

### Docker

```bash
cp config.toml.example config.toml
printf '%s\n' 'ORIONXCORE_LLM__API_KEY=replace-me' > .env
docker compose -f deploy/docker-compose.yml up -d --build
curl http://127.0.0.1:8080/health/ready
```

Stop it with:

```bash
docker compose -f deploy/docker-compose.yml down
```

More deployment options: [Deployment](docs/DEPLOYMENT.md).

## Configuration

The smallest useful configuration is already in `config.toml.example`:

```toml
[llm]
model = "gpt-4.1-mini"
api_base_url = "https://api.openai.com/v1"
```

Secrets stay in `.env` or a Docker Secret. Validate before starting:

```bash
uv run rill config check
uv run rill config show
```

For provider profiles, model aliases, authentication, tool policy, and Docker
Secrets, see [Configuration](docs/CONFIGURATION.md).

## Tools

Terminal, filesystem, and database tools are enabled by default. The chart tool
is opt-in:

```bash
uv run rill tool list
uv run rill tool enable generate_chart
```

Database starts without a connection URL and reports a configuration error only
when called. Terminal execution and filesystem writes require request approval:

```bash
uv run rill ask --approve-tool run_terminal_command "show the current directory"
```

To add a tool, implement `Tool`, register it in `build_registry`, add its
configuration switch, and test its policy metadata:

```text
src/rill/tools/<your_tool>.py
```

Read [Tool development](docs/TOOLS.md) or the project skill at
[`skills/tool-development/SKILL.md`](skills/tool-development/SKILL.md).

## API

```text
GET  /health/live
GET  /health/ready
GET  /v1/models
GET  /v1/tools
POST /v1/agent/respond
POST /v1/agent/stream
POST /v1/chat/completions
```

Example Agent request:

```bash
curl -sS -X POST http://127.0.0.1:8080/v1/agent/respond \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"List the files in the workspace."}]}'
```

## Docs

- [Configuration](docs/CONFIGURATION.md)
- [Installation](docs/INSTALL.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Tools](docs/TOOLS.md)
- [API](docs/API.md)
- [Dependencies](docs/dependencies.md)
- [Product plan](docs/PRODUCT_PLAN.md)

Chinese overview: [README_zh.md](README_zh.md).
