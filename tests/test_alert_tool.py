import asyncio

import httpx

from orionxcore.config import Settings
from orionxcore.tools.alert import AlertQueryTool
from orionxcore.tools.registry import build_registry


def _run(coro):
    return asyncio.run(coro)


def _settings(**overrides) -> Settings:
    values = {
        "enable_alert": True,
        "alert_api_url": "https://eop.example.test",
        "alert_default_window_minutes": 60,
        "alert_max_rows": 50,
    }
    values.update(overrides)
    return Settings(**values)


def _transport(payload, status_code=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload, request=request)
    return httpx.MockTransport(handler)


def test_alert_tool_normalizes_eop_response_and_summarizes(monkeypatch) -> None:
    payload = {
        "code": 200,
        "data": {
            "page": 1,
            "limit": 20,
            "total": 2,
            "items": [
                {"alarmId": 1, "alarmCode": "CPU_HIGH", "title": "CPU", "instance": "APP-A/host-a/service", "alarmLevel": "严重", "clearStatus": 0, "count": 2, "lastTime": "2026-09-07 09:00:00", "agentDn": "host-a"},
                {"alarmId": 2, "alarmCode": "DISK", "title": "Disk", "instance": "APP-B/host-b/service", "alarmLevel": "WARNING", "clearStatus": 1, "count": 1, "lastTime": "2026-09-07 08:00:00", "agentDn": "host-b"},
            ],
        },
    }
    original_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = _transport(payload)
        return original_client(*args, **kwargs)

    monkeypatch.setattr("orionxcore.tools.alert.httpx.AsyncClient", fake_client)
    result = _run(AlertQueryTool(_settings()).execute({"start_time": "2026-09-07 08:00:00", "end_time": "2026-09-07 10:00:00"}))

    assert result["ok"] is True
    assert result["inspection_status"] == "critical"
    assert result["summary"]["active"] == 1
    assert result["summary"]["by_level"]["CRITICAL"] == 1
    assert result["summary"]["affected_clusters"] == ["APP-A", "APP-B"]
    assert result["items"][0]["alarm_level"] == "CRITICAL"


def test_alert_tool_builds_eop_array_filters_and_clamps_limit(monkeypatch) -> None:
    received = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received["url"] = str(request.url)
        return httpx.Response(200, json={"code": 200, "data": {"items": [], "total": 0}}, request=request)

    original_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    monkeypatch.setattr("orionxcore.tools.alert.httpx.AsyncClient", fake_client)
    result = _run(AlertQueryTool(_settings()).execute({"cluster_names": ["APP-A", "APP-B"], "alarm_levels": ["critical"], "limit": 500}))

    assert result["ok"] is True
    assert "clusterName%5B%5D=APP-A" in received["url"]
    assert "clusterName%5B%5D=APP-B" in received["url"]
    assert "alarmLevel%5B%5D=CRITICAL" in received["url"]
    assert "limit=50" in received["url"]


def test_alert_tool_filters_active_items_and_validates_times(monkeypatch) -> None:
    payload = {"code": 200, "data": {"items": [{"alarmId": 1, "alarmLevel": "INFO", "clearStatus": 1}, {"alarmId": 2, "alarmLevel": "MAJOR", "clearStatus": 0}], "total": 2}}
    original_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = _transport(payload)
        return original_client(*args, **kwargs)

    monkeypatch.setattr("orionxcore.tools.alert.httpx.AsyncClient", fake_client)
    tool = AlertQueryTool(_settings())
    filtered = _run(tool.execute({"active_only": True}))
    invalid = _run(tool.execute({"start_time": "2026-09-07 08:00:00"}))

    assert filtered["ok"] is True
    assert filtered["summary"]["total"] == 1
    assert filtered["items"][0]["alarm_id"] == 2
    assert invalid["ok"] is False
    assert "provided together" in invalid["error"]


def test_alert_tool_handles_unconfigured_and_http_errors(monkeypatch) -> None:
    assert _run(AlertQueryTool(_settings(alert_api_url="")).execute({}))["ok"] is False
    original_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = _transport({"message": "denied"}, status_code=401)
        return original_client(*args, **kwargs)

    monkeypatch.setattr("orionxcore.tools.alert.httpx.AsyncClient", fake_client)
    result = _run(AlertQueryTool(_settings()).execute({}))
    assert result == {"ok": False, "error": "Alert service returned an error.", "status_code": 401}


def test_alert_tool_is_registered_only_when_enabled() -> None:
    assert "alert_query" in {item.name for item in build_registry(_settings()).describe()}
    assert "alert_query" not in {item.name for item in build_registry(_settings(enable_alert=False)).describe()}
