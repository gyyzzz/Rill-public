import asyncio
import tomllib
import asyncio

import pytest
import httpx
from fastapi.testclient import TestClient

from rill.app import create_app
from rill.cli import _approval_requests, main as cli_main
from rill.config import Settings, get_settings
from rill.llm.openai_compat import LLMResponse, ToolCall
from rill.llm.openai_compat import OpenAICompatibleClient
from rill.llm.router import ModelRouter
from rill.llm.capabilities import CapabilityRegistry
from rill.schemas import AgentRequest, ChatCompletionRequest, ChatMessage
from rill.services.agent import AgentService
from rill.services.fallback import parse_fallback_response
from rill.tools.base import Tool
from rill.tools.database import DatabaseTool
from rill.tools.registry import ToolRegistry
from rill.tools.terminal import TerminalTool


class EchoTool(Tool):
    name = "echo"
    description = "Return the given value."
    input_schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }

    async def execute(self, arguments):
        return {"ok": True, "value": arguments["value"]}


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)

    async def stream_complete(self, **kwargs):
        yield {"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}


class WriteTool(EchoTool):
    name = "write"
    read_only = False


def request(mode="native_tool_call"):
    return AgentRequest(
        messages=[ChatMessage(role="user", content="echo hello")],
        execution_mode=mode,
    )


def test_native_agent_tool_call_and_events():
    settings = Settings(tool_policy={"max_calls_per_request": 4})
    service = AgentService(settings, ToolRegistry([EchoTool()], settings))
    service._client = FakeClient([
        LLMResponse("", [ToolCall("call-1", "echo", {"value": "hello"})], {}),
        LLMResponse("finished", [], {}),
    ])

    result = asyncio.run(service.run(request()))

    assert result.message.content == "finished"
    assert result.iterations == 2
    assert result.request_id
    assert {event.type for event in result.events} >= {
        "request_started", "model_started", "model_completed", "tool_call", "tool_result", "final"
    }
    assert all(event.request_id == result.request_id for event in result.events)


def test_json_fallback_runs_multiple_turns():
    settings = Settings(agent={"execution_mode": "json_fallback"})
    service = AgentService(settings, ToolRegistry([EchoTool()], settings))
    service._client = FakeClient([
        LLMResponse('{"tool_calls":[{"name":"echo","arguments":{"value":"hello"}}]}', [], {}),
        LLMResponse('{"final":"finished"}', [], {}),
    ])

    result = asyncio.run(service.run(request("json_fallback")))

    assert result.message.content == "finished"
    assert result.iterations == 2


def test_fallback_parser_accepts_fenced_json_and_surrounding_text():
    calls, final = parse_fallback_response(
        'Here is the request:\n```json\n{"tool_calls":[{"name":"echo","arguments":{"value":"x"}}]}\n```'
    )
    assert final is None
    assert calls[0].name == "echo"
    assert calls[0].arguments == {"value": "x"}


def test_legacy_flat_config_migrates_and_future_versions_reject(tmp_path):
    legacy = tmp_path / "legacy.toml"
    legacy.write_text('model = "legacy-model"\napi_base_url = "https://legacy.example/v1"\n')
    settings = Settings(config_file=legacy)
    assert settings.llm.model == "legacy-model"
    assert settings.llm.api_base_url == "https://legacy.example/v1"

    with pytest.raises(ValueError, match="newer than this Rill release"):
        Settings.model_validate({"config_version": 99})


def test_cli_base_url_uses_config_default_and_explicit_override(tmp_path):
    from argparse import Namespace

    from rill.cli import _base_url

    config_path = tmp_path / "config.toml"
    config_path.write_text('[server]\nport = 18080\n')
    args = Namespace(config_file=str(config_path), base_url=None)

    assert _base_url(args) == "http://127.0.0.1:18080"
    args.base_url = "https://rill.example.com"
    assert _base_url(args) == "https://rill.example.com"


def test_default_tools_and_unconfigured_database_are_safe(tmp_path):
    settings = Settings(config_file=tmp_path / "missing.toml", _env_file=tmp_path / "missing.env")
    assert settings.terminal.enabled
    assert settings.filesystem.enabled
    assert settings.database.enabled
    assert not settings.chart.enabled

    result = asyncio.run(DatabaseTool(settings).execute({"operation": "list_tables"}))
    assert result == {
        "ok": False,
        "code": "database_not_configured",
        "error": "Database is not configured. Set database.url in config.toml or ORIONXCORE_DATABASE__URL, then restart the service.",
    }


def test_cli_finds_request_scoped_tool_approval_events():
    response = {
        "events": [
            {"type": "tool_result", "payload": {"name": "run_terminal_command", "result": {"requires_approval": True}}},
            {"type": "tool_result", "payload": {"name": "filesystem", "result": {"requires_approval": True}}},
            {"type": "tool_result", "payload": {"name": "run_terminal_command", "result": {"requires_approval": True}}},
        ]
    }
    assert _approval_requests(response) == ["run_terminal_command", "filesystem"]


def test_chat_retries_the_same_turn_after_interactive_approval(tmp_path, monkeypatch):
    import rill.cli as cli

    responses = iter([
        {
            "message": {"content": "Approval required."},
            "events": [{
                "type": "tool_result",
                "payload": {"name": "run_terminal_command", "result": {"requires_approval": True}},
            }],
        },
        {"message": {"content": "Done."}, "events": []},
    ])
    requests = []

    def fake_post_json(**kwargs):
        requests.append({**kwargs, "payload": {
            **kwargs["payload"],
            "messages": list(kwargs["payload"]["messages"]),
            "approved_tools": list(kwargs["payload"]["approved_tools"]),
        }})
        return next(responses)

    answers = iter(["list files", "y", "exit"])
    monkeypatch.setattr(cli, "post_json", fake_post_json)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    from argparse import Namespace

    args = Namespace(
        session_id=None,
        no_events=True,
        approve_tool=[],
        raw=False,
        config_file=str(tmp_path / "missing.toml"),
        base_url="http://127.0.0.1:8080",
        timeout=10.0,
    )
    assert cli.run_chat(args) == 0
    assert len(requests) == 2
    assert requests[0]["payload"]["approved_tools"] == []
    assert requests[1]["payload"]["approved_tools"] == ["run_terminal_command"]
    assert requests[0]["payload"]["messages"] == requests[1]["payload"]["messages"]


def test_multi_provider_model_alias_and_capability_cache():
    settings = Settings(llm={
        "model": "legacy-model",
        "provider": "local",
        "model_aliases": {"fast": "small-model"},
        "providers": {
            "local": {
                "api_base_url": "http://127.0.0.1:1234/v1",
                "default_model": "local-model",
                "models": ["small-model", "local-model"],
            }
        },
    })
    provider, model, _ = ModelRouter(settings).resolve("fast")
    assert (provider, model) == ("local", "small-model")
    with pytest.raises(ValueError, match="Unknown provider"):
        ModelRouter(settings).resolve(provider="missing")

    capabilities = CapabilityRegistry(settings)
    capabilities.set("local", "small-model", {"tool_call": False})
    assert capabilities.get("local", "small-model") == {"tool_call": False}


def test_openai_compatible_adapter_parses_tool_calls_and_uses_auth():
    def handler(request):
        assert request.headers["authorization"] == "Bearer test-secret"
        payload = __import__("json").loads(request.content)
        assert payload["model"] == "demo-model"
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "",
            "tool_calls": [{"id": "call-1", "function": {"name": "echo", "arguments": "{\"value\":\"ok\"}"}}],
        }}]})

    settings = Settings(llm={
        "model": "demo-model",
        "api_key": "test-secret",
        "api_base_url": "https://provider.example/v1",
        "retries": 0,
    })
    client = OpenAICompatibleClient(settings, transport=httpx.MockTransport(handler))
    result = asyncio.run(client.complete(messages=[], tools=[]))
    assert result.tool_calls == [ToolCall("call-1", "echo", {"value": "ok"})]


def test_tool_policy_limits_discovery_execution_and_schema():
    settings = Settings(tool_policy={"read_only_mode": True})
    registry = ToolRegistry([WriteTool()], settings)
    assert registry.as_openai_tools() == []

    result = asyncio.run(registry.execute("write", {"value": "x"}))
    assert result["ok"] is False
    assert "read-only" in result["error"]


def test_tool_schema_rejects_wrong_types_and_unknown_fields():
    settings = Settings()
    registry = ToolRegistry([EchoTool()], settings)
    wrong_type = asyncio.run(registry.execute("echo", {"value": 123}))
    unknown = asyncio.run(registry.execute("echo", {"value": "ok", "extra": True}))
    assert "must be string" in wrong_type["error"]
    assert "Unknown arguments" in unknown["error"]


def test_api_key_permissions_are_scoped():
    settings = Settings(auth={
        "enabled": True,
        "allow_local_without_key": False,
        "agent_api_keys": ["agent-secret"],
        "diagnostic_api_keys": ["diagnostic-secret"],
    })
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    assert client.get("/v1/models", headers={"Authorization": "Bearer agent-secret"}).status_code == 401
    assert client.get("/v1/models", headers={"Authorization": "Bearer diagnostic-secret"}).status_code == 200


def test_chat_completion_stream_forwards_provider_deltas():
    settings = Settings()
    service = AgentService(settings, ToolRegistry([], settings))
    service._client = FakeClient([])
    request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")], stream=True)

    chunks = asyncio.run(_collect_stream(service, request))
    content = "".join((chunk.choices[0].delta.content or "") for chunk in chunks)
    assert content == "hello"
    assert chunks[-1].choices[0].finish_reason == "stop"


def test_cli_config_set_validates_atomically_and_keeps_backup(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[server]\nport = 8080\n')
    assert cli_main(["--config-file", str(path), "config", "set", "server.port", "9090"]) == 0
    assert tomllib.loads(path.read_text())["server"]["port"] == 9090
    assert tomllib.loads(path.with_suffix(".toml.bak").read_text())["server"]["port"] == 8080
    assert cli_main(["--config-file", str(path), "config", "set", "server.port", "70000"]) == 1
    assert tomllib.loads(path.read_text())["server"]["port"] == 9090


def test_terminal_output_is_bounded_and_timeout_kills_command(tmp_path):
    settings = Settings(
        terminal={"workdir": tmp_path, "timeout_seconds": 1, "allow_risky_commands": True, "require_confirmation": False},
        agent={"max_result_bytes": 2048},
    )
    tool = TerminalTool(settings)
    result = asyncio.run(tool.execute({"command": "python3 -c \"print('x' * 50000)\""}))
    assert result["ok"]
    assert result["stdout_truncated"]
    assert len(result["stdout"].encode()) <= 1024

    timeout_result = asyncio.run(tool.execute({"command": "sleep 3"}))
    assert timeout_result["ok"] is False
    assert "timed out" in timeout_result["error"]


async def _collect_stream(service, request):
    return [chunk async for chunk in service.stream_chat_completion(request)]


def test_health_and_model_discovery_routes():
    client = TestClient(create_app())
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
    response = client.get("/v1/models")
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "gpt-4.1-mini"
