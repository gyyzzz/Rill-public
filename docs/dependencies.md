# Dependencies

Rill uses FastAPI and Uvicorn for HTTP serving, HTTPX for OpenAI-compatible
provider calls, Pydantic Settings for TOML and environment configuration, and
SQLAlchemy plus ClickHouse/PostgreSQL drivers for database tools. Matplotlib is
used by chart generation.

Install runtime dependencies with `uv sync`. Install pytest for development
with `uv sync --extra dev`. An optional terminal UI is not bundled; the `tui`
command currently directs operators to the local diagnostics commands.
