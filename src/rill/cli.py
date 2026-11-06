import argparse
import asyncio
import getpass
import json
import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

import httpx

from rill.config import Settings, get_settings
from rill.llm.router import ModelRouter, ProviderRegistry
from rill.llm.router import sanitize_base_url
from rill.tools.registry import build_registry

# Compact terminal wordmark, shared with the README.
LOGO = """
 █▀█ █ █   █
 █▀▄ █ █   █
 ▀ ▀ ▀ ▀▀▀ ▀▀▀
"""

VERSION = "0.2.0"


def print_banner() -> None:
    """Print the Rill logo and version."""
    print(LOGO)
    print(f"  Rill CLI v{VERSION}")
    print("")

# Configure readline for proper backspace/delete handling
# macOS uses libedit which requires different syntax than GNU readline
try:
    import readline
    if "libedit" in (readline.__doc__ or ""):
        # macOS libedit bindings
        readline.parse_and_bind("bind ^? ed-delete-prev-char")  # Backspace
        readline.parse_and_bind("bind ^H ed-delete-prev-char")  # Ctrl-H
        readline.parse_and_bind("bind ^[[3~ ed-delete-next-char")  # Delete key
    else:
        # GNU readline bindings
        readline.parse_and_bind("set enable-keypad on")
except ImportError:
    pass  # readline not available on some platforms


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rill", description="Rill command line client.")
    parser.add_argument("--config-file", default=None, help="Path to a TOML configuration file.")
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override the configured Rill service URL.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="HTTP timeout in seconds.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("serve", help="Start the service using server.host and server.port from configuration.")

    ask_parser = subparsers.add_parser("ask", help="Send a prompt to /v1/agent/respond.")
    ask_parser.add_argument("prompt", help="Natural language prompt to send.")
    ask_parser.add_argument("--session-id", help="Optional session ID.")
    ask_parser.add_argument("--approve-tool", action="append", default=[], help="Approve a tool by name for this request; may be repeated.")
    ask_parser.add_argument(
        "--raw",
        action="store_true",
        help="Print raw JSON response instead of formatted output.",
    )
    ask_parser.add_argument(
        "--json",
        action="store_true",
        help="Alias for --raw.",
    )
    ask_parser.add_argument(
        "--no-events",
        action="store_true",
        help="Hide Events output, show only the final response.",
    )

    chat_parser = subparsers.add_parser("chat", help="Start an interactive chat session.")
    chat_parser.add_argument("--session-id", help="Optional session ID. Generated automatically if omitted.")
    chat_parser.add_argument("--approve-tool", action="append", default=[], help="Approve a tool by name for every turn; may be repeated.")
    chat_parser.add_argument(
        "--raw",
        action="store_true",
        help="Print raw JSON responses for each turn.",
    )

    init_parser = subparsers.add_parser("init", help="Create a starter TOML configuration.")
    init_parser.add_argument("--force", action="store_true", help="Replace an existing configuration.")
    init_parser.add_argument("--non-interactive", action="store_true", help="Write defaults without prompting.")
    init_parser.add_argument("--base-url", default=None, help="OpenAI-compatible API base URL.")
    init_parser.add_argument("--model", default=None, help="Default model name.")

    config_parser = subparsers.add_parser("config", help="Validate and manage TOML configuration.")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    config_subparsers.add_parser("check", help="Validate configuration.")
    config_subparsers.add_parser("show", help="Show configuration with secrets redacted.")
    config_subparsers.add_parser("backup", help="Back up the selected TOML configuration.")
    config_subparsers.add_parser("restore", help="Restore the latest TOML configuration backup.")
    set_parser = config_subparsers.add_parser("set", help="Set a dotted configuration value.")
    set_parser.add_argument("path")
    set_parser.add_argument("value")

    provider_parser = subparsers.add_parser("provider", help="Manage provider profiles.")
    provider_subparsers = provider_parser.add_subparsers(dest="provider_command", required=True)
    provider_subparsers.add_parser("list")
    provider_add = provider_subparsers.add_parser("add")
    provider_add.add_argument("name")
    provider_add.add_argument("--base-url", required=True)
    provider_add.add_argument("--model", required=True)
    provider_test = provider_subparsers.add_parser("test")
    provider_test.add_argument("name", nargs="?", default=None)
    provider_use = provider_subparsers.add_parser("use")
    provider_use.add_argument("name")

    model_parser = subparsers.add_parser("model", help="List configured models.")
    model_parser.add_argument("action", choices=["list"], nargs="?", default="list")

    tool_parser = subparsers.add_parser("tool", help="Inspect and manage tools.")
    tool_subparsers = tool_parser.add_subparsers(dest="tool_command", required=True)
    tool_subparsers.add_parser("list")
    tool_enable = tool_subparsers.add_parser("enable")
    tool_enable.add_argument("name")
    tool_disable = tool_subparsers.add_parser("disable")
    tool_disable.add_argument("name")

    subparsers.add_parser("doctor", help="Validate local configuration and dependencies.")
    subparsers.add_parser("status", help="Show local service configuration status.")
    subparsers.add_parser("tui", help="Open the optional terminal observer.")
    chat_parser.add_argument(
        "--no-events",
        action="store_true",
        help="Hide Events output, show only the final response.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        return run_serve(args)
    if args.command == "ask":
        return run_ask(args)
    if args.command == "chat":
        return run_chat(args)
    if args.command == "init":
        return run_init(args)
    if args.command == "config":
        return run_config(args)
    if args.command == "provider":
        return run_provider(args)
    if args.command == "model":
        return run_model(args)
    if args.command == "tool":
        return run_tool(args)
    if args.command in {"doctor", "status", "tui"}:
        return run_diagnostic(args)

    parser.error(f"Unknown command: {args.command}")
    return 2


def run_serve(args: argparse.Namespace) -> int:
    import uvicorn

    try:
        settings = _settings(args)
    except Exception as exc:
        print(f"Configuration invalid: {exc}", file=sys.stderr)
        return 1
    os.environ["ORIONXCORE_CONFIG_FILE"] = str(_config_path(args))
    get_settings.cache_clear()
    uvicorn.run("rill.main:app", host=settings.server.host, port=settings.server.port)
    return 0


def run_ask(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {
        "messages": [
            {
                "role": "user",
                "content": args.prompt,
            }
        ]
    }
    if args.session_id:
        payload["session_id"] = args.session_id
    if args.approve_tool:
        payload["approved_tools"] = args.approve_tool

    response = post_json(
        base_url=_base_url(args),
        path="/v1/agent/respond",
        payload=payload,
        timeout=args.timeout,
        api_key=os.environ.get("RILL_API_KEY"),
    )

    if args.raw or args.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0

    render_agent_response(response, show_events=not args.no_events)
    return 0


def run_chat(args: argparse.Namespace) -> int:
    print_banner()
    session_id = args.session_id or f"chat-{uuid4().hex[:8]}"
    print(f"Session: {session_id}")
    print("Type 'exit' or 'quit' to stop.")
    print("")
    show_events = not args.no_events
    base_url = _base_url(args)
    messages: list[dict[str, Any]] = []

    while True:
        try:
            prompt = input("you> ").strip()
        except EOFError:
            print("")
            return 0
        except KeyboardInterrupt:
            print("")
            return 0

        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit"}:
            return 0

        turn_messages = messages + [{"role": "user", "content": prompt}]
        response = post_json(
            base_url=base_url,
            path="/v1/agent/respond",
            payload={
                "messages": turn_messages,
                "session_id": session_id,
                "approved_tools": args.approve_tool,
            },
            timeout=args.timeout,
            api_key=os.environ.get("RILL_API_KEY"),
        )
        messages = turn_messages + [{"role": "assistant", "content": response["message"]["content"]}]

        if args.raw:
            print(json.dumps(response, ensure_ascii=False, indent=2))
        else:
            render_agent_response(response, assistant_label="assistant>", show_events=show_events)


def post_json(base_url: str, path: str, payload: dict[str, Any], timeout: float, api_key: str | None = None) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        print(f"HTTP error {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        raise SystemExit(1) from exc
    except httpx.HTTPError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _config_path(args: argparse.Namespace) -> Path:
    return Path(args.config_file or os.environ.get("ORIONXCORE_CONFIG_FILE", "config.toml")).expanduser().resolve()


def _settings(args: argparse.Namespace) -> Settings:
    config_path = _config_path(args)
    return Settings(config_file=config_path, _env_file=config_path.parent / ".env")


def run_init(args: argparse.Namespace) -> int:
    target = _config_path(args)
    if target.exists() and not args.force:
        print(f"Configuration already exists: {target}", file=sys.stderr)
        return 1
    template = Path(__file__).resolve().parents[2] / "config.toml.example"
    target.parent.mkdir(parents=True, exist_ok=True)
    data = tomllib.loads(template.read_text(encoding="utf-8"))
    interactive = sys.stdin.isatty() and not args.non_interactive
    model = args.model or "gpt-4.1-mini"
    base_url = args.base_url or "https://api.openai.com/v1"
    api_key = ""
    if interactive:
        model = input(f"Default model [{model}]: ").strip() or model
        base_url = input(f"API base URL [{base_url}]: ").strip() or base_url
        api_key = os.environ.get("ORIONXCORE_LLM__API_KEY") or getpass.getpass("Provider API key (leave blank to set later): ")
    _set_dotted(data, "llm.model", model)
    _set_dotted(data, "llm.api_base_url", base_url)
    Settings.model_validate(data)
    _write_toml_atomic(target, data)
    if api_key:
        env_path = target.parent / ".env"
        _store_environment_value(env_path, "ORIONXCORE_LLM__API_KEY", api_key)
    print(f"Created {target}")
    return 0


def run_config(args: argparse.Namespace) -> int:
    source = _config_path(args)
    if args.config_command == "backup":
        if not source.exists():
            print(f"Configuration does not exist: {source}", file=sys.stderr)
            return 1
        backup = source.with_suffix(source.suffix + ".bak")
        shutil.copy2(source, backup)
        print(f"Backup created: {backup}")
        return 0
    if args.config_command == "restore":
        backup = source.with_suffix(source.suffix + ".bak")
        if not backup.exists():
            print(f"Configuration backup does not exist: {backup}", file=sys.stderr)
            return 1
        try:
            Settings(config_file=backup, _env_file=source.parent / ".env")
            _write_text_atomic(source, backup.read_text(encoding="utf-8"))
            print(f"Restored: {source}")
            return 0
        except Exception as exc:
            print(f"Configuration backup is invalid: {exc}", file=sys.stderr)
            return 1
    if args.config_command == "set":
        try:
            if any(part in args.path.lower() for part in ("api_key", "password", "secret", "token")):
                print("Store credentials in the adjacent .env file instead of TOML.", file=sys.stderr)
                return 1
            if not all(re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in args.path.split(".")):
                print("Configuration paths may contain only TOML key names separated by dots.", file=sys.stderr)
                return 1
            data = _load_toml(source)
            _set_dotted(data, args.path, _parse_value(args.value))
            Settings.model_validate(data)
            _backup_before_change(source)
            _write_toml_atomic(source, data)
            print(f"Updated {args.path}")
            return 0
        except Exception as exc:
            print(f"Unable to update configuration: {exc}", file=sys.stderr)
            return 1
    try:
        settings = _settings(args)
    except Exception as exc:
        print(f"Configuration invalid: {exc}", file=sys.stderr)
        return 1
    if args.config_command == "check":
        print(f"Configuration valid: {settings.config_file}")
        return 0
    if args.config_command == "show":
        print(json.dumps(_redact(settings.model_dump(mode="json")), ensure_ascii=False, indent=2))
        return 0
    return 2


def run_provider(args: argparse.Namespace) -> int:
    settings = _settings(args)
    registry = ProviderRegistry(settings)
    if args.provider_command == "list":
        print(json.dumps(registry.profiles(), ensure_ascii=False, indent=2))
        return 0
    if args.provider_command == "use":
        if args.name not in registry.names():
            print(f"Unknown provider: {args.name}", file=sys.stderr)
            return 1
        _backup_before_change(_config_path(args))
        data = _load_toml(_config_path(args))
        _set_dotted(data, "llm.provider", args.name)
        _write_toml_atomic(_config_path(args), data)
        print(f"Active provider: {args.name}")
        return 0
    if args.provider_command == "add":
        if not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
            print("Provider names may contain only letters, numbers, underscores, and hyphens.", file=sys.stderr)
            return 1
        secret_name = _provider_env_name(args.name)
        api_key = os.environ.get(secret_name)
        save_secret = False
        if not api_key and sys.stdin.isatty():
            api_key = getpass.getpass("Provider API key: ")
            save_secret = True
        if not api_key:
            print(f"Set {secret_name} in the environment or run this command interactively to enter the key securely.", file=sys.stderr)
            return 1
        _backup_before_change(_config_path(args))
        data = _load_toml(_config_path(args))
        _set_dotted(data, f"llm.providers.{args.name}.type", "openai_compatible")
        _set_dotted(data, f"llm.providers.{args.name}.api_base_url", args.base_url)
        _set_dotted(data, f"llm.providers.{args.name}.default_model", args.model)
        _set_dotted(data, f"llm.providers.{args.name}.models", [args.model])
        Settings.model_validate(data)
        _write_toml_atomic(_config_path(args), data)
        if save_secret:
            _store_provider_secret(_config_path(args).parent / ".env", args.name, api_key)
            print(f"Added provider: {args.name}; API key saved to the adjacent .env file.")
        else:
            print(f"Added provider: {args.name}; API key is read from {secret_name}.")
        return 0
    result = asyncio.run(registry.get(args.name).check())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def run_model(args: argparse.Namespace) -> int:
    print(json.dumps(ModelRouter(_settings(args)).models(), ensure_ascii=False, indent=2))
    return 0


def run_tool(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if args.tool_command == "list":
        print(json.dumps([item.model_dump() for item in build_registry(settings).describe()], ensure_ascii=False, indent=2))
        return 0
    tool_sections = {"run_terminal_command": "terminal", "filesystem": "filesystem", "database_query": "database", "generate_chart": "chart"}
    section = tool_sections.get(args.name)
    if section is None:
        print(f"Unknown tool: {args.name}", file=sys.stderr)
        return 1
    data = _load_toml(_config_path(args))
    _backup_before_change(_config_path(args))
    _set_dotted(data, f"{section}.enabled", args.tool_command == "enable")
    _write_toml_atomic(_config_path(args), data)
    print(f"Tool {args.name}: {'enabled' if args.tool_command == 'enable' else 'disabled'}")
    return 0


def run_diagnostic(args: argparse.Namespace) -> int:
    if args.command == "tui":
        return run_tui(args)
    try:
        settings = _settings(args)
        base_url = _base_url(args, settings)
        result = {"status": "ok", "config_file": str(settings.config_file), "provider": settings.llm.provider, "model": settings.llm.model}
        if args.command == "status":
            headers = {"Authorization": f"Bearer {os.environ['RILL_API_KEY']}"} if os.environ.get("RILL_API_KEY") else {}
            with httpx.Client(timeout=args.timeout, trust_env=False) as client:
                health = client.get(base_url.rstrip("/") + "/health", headers=headers)
                health.raise_for_status()
                runtime = client.get(base_url.rstrip("/") + "/v1/status", headers=headers)
                runtime.raise_for_status()
            result["service"] = health.json()
            result["runtime"] = runtime.json()
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Rill configuration and dependencies look valid.")
        return 0
    except Exception as exc:
        print(f"Rill diagnostic failed: {exc}", file=sys.stderr)
        return 1


def _base_url(args: argparse.Namespace, settings: Settings | None = None) -> str:
    if args.base_url:
        return args.base_url
    configured = settings or _settings(args)
    return configured.client.base_url or f"http://127.0.0.1:{configured.server.port}"


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _set_dotted(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _parse_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        sensitive = {"api_key", "api_keys", "secret", "password", "token"}
        return {
            key: (
                "********"
                if key.lower() in sensitive or key.lower().endswith(("_api_key", "_api_keys", "_secret", "_token"))
                else sanitize_base_url(item) if key.lower() == "api_base_url" and isinstance(item, str)
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value: {type(value).__name__}")


def _serialize_toml(data: dict[str, Any], prefix: str = "") -> str:
    lines: list[str] = []
    scalars = {key: value for key, value in data.items() if not isinstance(value, dict)}
    for key, value in scalars.items():
        lines.append(f"{key} = {_toml_scalar(value)}")
    for key, value in data.items():
        if isinstance(value, dict):
            section = f"{prefix}.{key}" if prefix else key
            if lines:
                lines.append("")
            lines.append(f"[{section}]")
            lines.append(_serialize_toml(value, section).rstrip())
    return "\n".join(lines) + ("\n" if lines else "")


def _write_toml_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _serialize_toml(data)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def _backup_before_change(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))


def _store_provider_secret(path: Path, provider_name: str, api_key: str) -> None:
    _store_environment_value(path, _provider_env_name(provider_name), api_key)


def _provider_env_name(provider_name: str) -> str:
    segment = "".join(character if character.isalnum() or character == "-" else "_" for character in provider_name.upper())
    return f"ORIONXCORE_LLM__PROVIDERS__{segment}__API_KEY"


def _store_environment_value(path: Path, env_name: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    assignment = f"{env_name}={json.dumps(value, ensure_ascii=True)}"
    updated = False
    for index, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == env_name:
            lines[index] = assignment
            updated = True
            break
    if not updated:
        lines.append(assignment)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write("\n".join(lines).rstrip() + "\n")
        temporary = Path(handle.name)
    temporary.chmod(0o600)
    temporary.replace(path)


def run_tui(args: argparse.Namespace) -> int:
    try:
        import curses
    except ImportError:
        print("This Python build does not include curses.", file=sys.stderr)
        return 1
    try:
        base_url = _base_url(args)
    except Exception as exc:
        print(f"Configuration invalid: {exc}", file=sys.stderr)
        return 1

    def draw(screen):
        curses.curs_set(0)
        screen.nodelay(True)
        while True:
            screen.erase()
            height, width = screen.getmaxyx()
            lines = ["Rill status observer", f"Service: {base_url}", "Press q to quit, r to refresh."]
            try:
                api_key = os.environ.get("RILL_API_KEY")
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                with httpx.Client(timeout=3, trust_env=False) as client:
                    health_response = client.get(base_url.rstrip("/") + "/health", headers=headers)
                    health_response.raise_for_status()
                    health = health_response.json()
                    tools_response = client.get(base_url.rstrip("/") + "/v1/tools", headers=headers)
                    tools_response.raise_for_status()
                    tools = tools_response.json()
                    providers_response = client.get(base_url.rstrip("/") + "/v1/providers", headers=headers)
                    providers_response.raise_for_status()
                    providers = providers_response.json()
                    status_response = client.get(base_url.rstrip("/") + "/v1/status", headers=headers)
                    status_response.raise_for_status()
                    status = status_response.json()
                active = status.get("agent_requests", {})
                lines += [f"Health: {health.get('status')}", f"Provider profiles: {len(providers.get('data', []))}", f"Agent active/queued: {active.get('active', 0)}/{active.get('queued', 0)}", "Tools:"]
                lines.extend(f"  {tool['name']} [{tool.get('risk_level', 'unknown')}]" for tool in tools)
                errors = status.get("recent_errors", [])
                if errors:
                    lines.append("Recent errors:")
                    lines.extend(f"  {error.get('request_id')}: {error.get('error')}" for error in errors[-5:])
                events = status.get("recent_agent_events", [])
                if events:
                    lines.append("Recent agent events:")
                    lines.extend(f"  {event.get('type')} {event.get('payload', {}).get('name', '')}" for event in events[-8:])
            except Exception as exc:
                lines.append(f"Connection error: {exc}")
            for index, line in enumerate(lines[: max(0, height - 1)]):
                screen.addnstr(index, 0, line, max(0, width - 1))
            screen.refresh()
            key = screen.getch()
            if key in (ord("q"), ord("Q")):
                return
            if key in (ord("r"), ord("R")):
                continue
            curses.napms(1000)

    try:
        curses.wrapper(draw)
        return 0
    except Exception as exc:
        print(f"Unable to start TUI: {exc}", file=sys.stderr)
        return 1


def render_agent_response(response: dict[str, Any], assistant_label: str = "Assistant", show_events: bool = True) -> None:
    print(assistant_label)
    print(response.get("message", {}).get("content", ""))
    print("")

    if not show_events:
        return

    events = response.get("events") or []
    if not events:
        return

    print("Events")
    for event in events:
        event_type = event.get("type", "")
        payload = event.get("payload", {})
        print(format_event(event_type, payload))


def format_event(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "assistant":
        # Skip assistant event as it's already printed via message.content
        return ""
    if event_type == "iteration":
        return f"- iteration #{payload.get('iteration')}"
    if event_type == "tool_call":
        return f"- tool_call {payload.get('name')}: {json.dumps(payload.get('arguments', {}), ensure_ascii=False)}"
    if event_type == "tool_result":
        result = payload.get("result", {})
        status = "ok" if result.get("ok") else "error"
        return f"- tool_result {payload.get('name')} [{status}]"
    if event_type == "database_trace":
        return (
            f"- database_trace question={payload.get('question')!r} "
            f"database={payload.get('database')!r} final_sql={payload.get('final_sql')!r}"
        )
    if event_type == "database_schema_context":
        return f"- database_schema_context\n{indent_text(payload.get('schema_context', ''))}"
    if event_type == "database_sql_attempt":
        return (
            f"- database_sql_attempt #{payload.get('attempt')}: {payload.get('sql')!r}"
            + (f" retry_reason={payload.get('retry_reason')!r}" if payload.get("retry_reason") else "")
            + (f" error={payload.get('error')!r}" if payload.get("error") else "")
        )
    if event_type == "database_result_summary":
        return (
            f"- database_result_summary ok={payload.get('ok')} "
            f"row_count={payload.get('row_count')} columns={payload.get('columns')}"
        )
    if event_type == "assistant":
        return f"- assistant: {payload.get('content', '')}"
    return f"- {event_type}: {json.dumps(payload, ensure_ascii=False)}"


def indent_text(text: str) -> str:
    if not text:
        return "  <empty>"
    return "\n".join(f"  {line}" for line in text.splitlines())
