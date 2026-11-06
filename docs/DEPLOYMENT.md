# Deployment

Rill is a single-process FastAPI service and can run directly or in Docker.
`rill serve` reads `server.host` and `server.port`; Uvicorn and Docker can
override the bind address explicitly. Configure model credentials, workspace
paths, and enabled tools before exposing the service.

For Docker Compose, provide `config.toml` and a `.env` file beside the project
root, then run:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

The image checks `/health/ready`; `/health/live` reports process liveness.
The default configuration enables terminal, filesystem, and database tools;
chart generation is disabled until `chart.enabled = true`. A missing database
URL does not fail readiness; database calls return a configuration error.
Set `[auth].enabled = true` and configure `ORIONXCORE_AUTH__API_KEYS` before
making a deployment reachable by untrusted clients. Use HTTPS at the ingress.
Set `ORIONXCORE_LLM__API_KEY_FILE=/run/secrets/llm_api_key` to read provider
credentials from a mounted Docker Secret.

For destructive filesystem operations and terminal commands, the caller must
include the tool name in `approved_tools` for that request. Terminal execution
can also be disabled in configuration.

The CLI exposes the same per-request approval with `--approve-tool`; for
example, `rill ask --approve-tool run_terminal_command "show the current directory"`.
