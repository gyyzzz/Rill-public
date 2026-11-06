# Installation

## Local development

```bash
uv sync --extra dev
cp config.toml.example config.toml
export ORIONXCORE_LLM__API_KEY=your_api_key
uv run rill config check
uv run rill serve
```

The default provider uses the OpenAI-compatible API. Configure `llm.api_base_url`,
`llm.model`, and the API key for another compatible service. See
[`config.toml.example`](../config.toml.example) for multi-provider profiles.

## Docker

Copy `config.toml.example` to `config.toml`, set the API key through the
environment, then run:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

The service listens on `server.host` and `server.port`, which default to
`0.0.0.0:8080`. Use `rill config check` before deployment to validate the TOML
configuration. The default runtime enables terminal, filesystem, and database
tools; chart generation is opt-in.

For a non-interactive setup, use `rill init --non-interactive`; set the model
and API base URL with `--model` and `--base-url`, then provide credentials in
`ORIONXCORE_LLM__API_KEY` or a mounted `api_key_file`.
