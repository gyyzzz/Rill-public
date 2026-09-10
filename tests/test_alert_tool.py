import asyncio

from orionxcore.config import Settings
from orionxcore.tools.alert import AlertQueryTool
from orionxcore.tools.registry import build_registry


def _settings(**overrides):
    values = {"enable_alert": True, "alert_database_url": "postgresql+psycopg2://ismp:secret%23value@db.example.test:15400/ismp", "alert_top_n": 10}
    values.update(overrides)
    return Settings(**values)


def test_alert_tool_runs_fixed_aggregations(monkeypatch):
    calls = []

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def cursor(self, **kwargs): return self
        def execute(self, statement, params):
            calls.append((str(statement), params))
        def fetchall(self):
            return [{"title": "CPU high", "count": 3}] if len(calls) == 1 else [{"alarm_code": "3520", "title": "Link down", "uncleared_count": 2, "cleared_count": 4, "total_count": 6}]

    connection_params = {}
    def fake_connect(**kwargs):
        connection_params.update(kwargs)
        return Connection()
    monkeypatch.setattr("orionxcore.tools.alert.psycopg2.connect", fake_connect)
    result = asyncio.run(AlertQueryTool(_settings()).execute({"window_minutes": 30, "host_names": ["host-a"]}))
    assert result["ok"] is True
    assert result["summary"] == {"uncleared_total": 2, "cleared_total": 4, "group_count": 1}
    assert result["current_uncleared_by_title"] == [{"title": "CPU high", "count": 3}]
    assert result["by_alarm_code_title_and_status"][0]["uncleared_count"] == 2
    assert result["by_alarm_code_title_and_status"][0]["cleared_count"] == 4
    assert len(calls) == 2
    assert all("GROUP BY" in sql for sql, _ in calls)
    assert calls[0][1]["host_0"] == "host-a"
    assert "filter_type" not in calls[0][0]
    assert connection_params["password"] == "secret#value"
    assert connection_params["dbname"] == "ismp"


def test_alert_tool_validates_time_and_configuration():
    assert asyncio.run(AlertQueryTool(_settings(alert_database_url="")).execute({}))["ok"] is False
    result = asyncio.run(AlertQueryTool(_settings()).execute({"start_time": "2026-09-07 08:00:00"}))
    assert result["ok"] is False
    assert "provided together" in result["error"]


def test_alert_tool_maps_level_filters(monkeypatch):
    captured = {}

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def cursor(self, **kwargs): return self
        def execute(self, statement, params):
            captured["sql"] = str(statement)
            captured["params"] = params
        def fetchall(self): return []

    monkeypatch.setattr("orionxcore.tools.alert.psycopg2.connect", lambda **kwargs: Connection())
    result = asyncio.run(AlertQueryTool(_settings()).execute({"alarm_levels": ["CRITICAL", "严重"]}))
    assert result["ok"] is True
    assert "x1.curr_level IN" in captured["sql"]
    assert captured["params"]["level_0"] == 0
    assert captured["params"]["level_1"] == 0


def test_alert_tool_registry_switch():
    assert "alert_query" in {item.name for item in build_registry(_settings()).describe()}
    assert "alert_query" not in {item.name for item in build_registry(_settings(enable_alert=False)).describe()}
