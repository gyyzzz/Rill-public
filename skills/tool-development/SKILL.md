---
name: rill-tool-development
description: Implement or review a Rill runtime tool using its registry, configuration, policy, sandbox, and test conventions.
---

# Rill tool development

Use this skill when adding or changing a runtime tool in `src/rill/tools`.

## Workflow

1. Clarify the tool's purpose, input/output schema, external dependencies, and risk.
2. Read `src/rill/tools/base.py`, `registry.py`, and the closest existing tool.
3. Implement a `Tool` subclass with a stable name, concise description, and JSON Schema input.
4. Declare `read_only`, `risk_level`, `requires_approval`, `timeout_seconds`, and `max_result_bytes` accurately. Override `is_read_only(arguments)` and `approval_required(arguments)` when a tool has both read and write operations.
5. Keep filesystem and process access inside configured workspaces. Never bypass `ToolRegistry` policy checks.
6. Return structured dictionaries. Validate arguments, handle expected failures, and avoid returning secrets or unbounded data.
7. Register the tool in `build_registry`, add a configuration switch when needed, and update `config.toml.example`.
8. Add focused tests for validation, policy denial, successful execution, timeouts, and result limits where applicable.
9. Update API or operator documentation and verify `/v1/tools` exposes the expected metadata.
10. Run `uv run pytest`, `uv run python -m compileall -q src tests`, and inspect `git diff --check`.

## Interface contract

- `name`: unique lower snake case identifier.
- `description`: explain the supported operation and important constraints.
- `input_schema`: JSON Schema object with required fields and `additionalProperties: false` when practical.
- `execute(arguments)`: asynchronous method returning a JSON serializable result with an `ok` boolean and structured output or error.
- Policy metadata must describe actual behavior, not intended behavior. The registry enforces allow/deny lists, read-only mode, explicit approval, per-request call limits, timeout limits, and result size limits.

## Security checks

- Treat model arguments and tool output as untrusted.
- Validate types, required fields, enum values, and paths before side effects.
- Do not place credentials in TOML or log output; load them from environment or secret injection.
- High-risk operations require explicit caller approval and must still honor tool-specific safety checks.
- Do not shell out unless required. If subprocess use is necessary, apply a timeout and bound output.
- Tests must verify that denied and malformed requests do not perform side effects.
