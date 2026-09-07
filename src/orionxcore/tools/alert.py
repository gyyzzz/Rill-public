from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from orionxcore.config import Settings
from orionxcore.tools.base import Tool


class AlertQueryTool(Tool):
    """Read-only adapter for EOP's alert event endpoint."""

    name = "alert_query"
    description = (
        "Query alert events from the configured EOP alert service. Use this tool when the "
        "user asks to inspect a system, check system health or status, view recent alerts, "
        "or determine whether a system has an active fault. This tool is read-only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["recent", "query"],
                "description": "recent queries a recent window; query applies explicit filters.",
            },
            "window_minutes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10080,
                "description": "Recent query window in minutes. Defaults to configured value.",
            },
            "start_time": {"type": "string", "description": "Start time in ISO 8601 or YYYY-MM-DD HH:MM:SS."},
            "end_time": {"type": "string", "description": "End time in ISO 8601 or YYYY-MM-DD HH:MM:SS."},
            "cluster_names": {"type": "array", "items": {"type": "string"}},
            "host_names": {"type": "array", "items": {"type": "string"}},
            "cluster_types": {"type": "array", "items": {"type": "string"}},
            "alarm_levels": {
                "type": "array",
                "items": {"type": "string", "enum": ["CRITICAL", "MAJOR", "MINOR", "WARNING", "INFO"]},
            },
            "alarm_codes": {"type": "array", "items": {"type": "string"}},
            "active_only": {"type": "boolean", "description": "Return only uncleared alerts."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "additionalProperties": False,
    }

    _LEVELS = {"CRITICAL", "MAJOR", "MINOR", "WARNING", "INFO"}
    _LEVEL_MAP = {
        "CRITICAL": "CRITICAL", "严重": "CRITICAL", "0": "CRITICAL", "critical": "CRITICAL",
        "MAJOR": "MAJOR", "重要": "MAJOR", "1": "MAJOR", "high": "MAJOR",
        "MINOR": "MINOR", "次要": "MINOR", "2": "MINOR", "medium": "MINOR",
        "WARNING": "WARNING", "警告": "WARNING", "3": "WARNING", "low": "WARNING",
        "INFO": "INFO", "信息": "INFO", "4": "INFO", "info": "INFO",
    }
    _SEVERITY = {"INFO": 0, "WARNING": 1, "MINOR": 2, "MAJOR": 3, "CRITICAL": 4}

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._settings.alert_api_url.strip():
            return {"ok": False, "error": "Alert service is not configured."}

        try:
            query = self._build_query(arguments)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        headers = {"Accept": "application/json"}
        if self._settings.alert_api_token:
            headers["Authorization"] = f"Bearer {self._settings.alert_api_token}"

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._settings.alert_api_timeout_seconds),
                verify=self._settings.alert_verify_ssl,
                trust_env=False,
            ) as client:
                response = await client.get(
                    f"{self._settings.alert_api_url.rstrip('/')}/api/alerts",
                    params=query["params"],
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException:
            return {"ok": False, "error": "Alert service request timed out."}
        except httpx.HTTPStatusError as exc:
            return {
                "ok": False,
                "error": "Alert service returned an error.",
                "status_code": exc.response.status_code,
            }
        except httpx.HTTPError as exc:
            return {"ok": False, "error": f"Alert service request failed: {exc}"}
        except ValueError:
            return {"ok": False, "error": "Alert service returned invalid JSON."}

        return self._normalize_response(payload, query)

    def _build_query(self, arguments: dict[str, Any]) -> dict[str, Any]:
        operation = arguments.get("operation", "recent")
        if operation not in {"recent", "query"}:
            raise ValueError("operation must be recent or query.")
        start_raw = arguments.get("start_time")
        end_raw = arguments.get("end_time")
        if bool(start_raw) != bool(end_raw):
            raise ValueError("start_time and end_time must be provided together.")

        timezone = self._timezone()
        if start_raw:
            start = self._parse_time(start_raw, timezone, "start_time")
            end = self._parse_time(end_raw, timezone, "end_time")
            if start >= end:
                raise ValueError("start_time must be earlier than end_time.")
        else:
            window = arguments.get("window_minutes", self._settings.alert_default_window_minutes)
            if not isinstance(window, int) or isinstance(window, bool) or not 1 <= window <= 10080:
                raise ValueError("window_minutes must be an integer between 1 and 10080.")
            end = datetime.now(timezone)
            start = end - timedelta(minutes=window)

        limit = arguments.get("limit", self._settings.alert_max_rows)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer.")
        limit = min(limit, self._settings.alert_max_rows)

        filters = {
            "cluster_names": self._string_list(arguments.get("cluster_names"), "cluster_names"),
            "host_names": self._string_list(arguments.get("host_names"), "host_names"),
            "cluster_types": self._string_list(arguments.get("cluster_types"), "cluster_types"),
            "alarm_levels": self._levels(arguments.get("alarm_levels")),
            "alarm_codes": self._string_list(arguments.get("alarm_codes"), "alarm_codes"),
            "active_only": bool(arguments.get("active_only", False)),
        }
        params: list[tuple[str, str | int]] = [
            ("page", 1),
            ("limit", limit),
            ("maxRecords", self._settings.alert_max_rows),
            ("startTime", self._format_time(start)),
            ("endTime", self._format_time(end)),
            ("sortField", "lastTime"),
            ("sortOrder", "desc"),
        ]
        for key, values in (("clusterName[]", filters["cluster_names"]), ("hostName[]", filters["host_names"]),
                            ("clusterType[]", filters["cluster_types"]), ("alarmLevel[]", filters["alarm_levels"]),
                            ("alarmCode[]", filters["alarm_codes"])):
            params.extend((key, value) for value in values)
        return {
            "params": params,
            "query": {
                "operation": operation,
                "start_time": self._format_time(start),
                "end_time": self._format_time(end),
                "limit": limit,
                "filters": filters,
            },
        }

    def _normalize_response(self, payload: Any, query: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {"ok": False, "error": "Alert service returned an invalid response."}
        if payload.get("code") != 200:
            return {"ok": False, "error": payload.get("message") or "Alert service query failed."}
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return {"ok": False, "error": "Alert service returned an invalid response."}

        items = [self._normalize_item(item) for item in data["items"] if isinstance(item, dict)]
        if query["query"]["filters"]["active_only"]:
            items = [item for item in items if item["clear_status"] == 0]
        summary = self._summarize(items)
        total = data.get("total", len(items))
        if not isinstance(total, int):
            total = len(items)
        return {
            "ok": True,
            "source": "eop",
            "query": query["query"],
            "inspection_status": self._inspection_status(summary),
            "summary": summary,
            "items": items,
            "pagination": {
                "page": data.get("page", 1),
                "limit": data.get("limit", query["query"]["limit"]),
                "total": total,
                "has_more": total > query["query"]["limit"],
            },
        }

    def _normalize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        clear_status = item.get("clearStatus", item.get("clear_status", 0))
        try:
            clear_status = int(clear_status)
        except (TypeError, ValueError):
            clear_status = 0
        level = self._LEVEL_MAP.get(str(item.get("alarmLevel", item.get("alarm_level", "INFO"))).strip(), "INFO")
        return {
            "alarm_id": item.get("alarmId", item.get("alarm_id")),
            "alarm_code": item.get("alarmCode", item.get("alarm_code")),
            "title": item.get("title"),
            "instance": item.get("instance"),
            "alarm_level": level,
            "clear_status": clear_status,
            "clear_status_text": "已清除" if clear_status == 1 else "未清除",
            "count": item.get("count", 0),
            "first_time": item.get("firstTime", item.get("first_time")),
            "last_time": item.get("lastTime", item.get("last_time")),
            "clear_time": item.get("clearTime", item.get("clear_time")),
            "agent_dn": item.get("agentDn", item.get("agent_dn")),
            "cause": item.get("cause"),
            "solve": item.get("solve"),
            "last_info": item.get("lastInfo", item.get("last_info")),
        }

    def _summarize(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        by_level = Counter(item["alarm_level"] for item in items)
        active = [item for item in items if item["clear_status"] == 0]
        affected_clusters = sorted({item["instance"].split("/")[0] for item in items if isinstance(item.get("instance"), str) and "/" in item["instance"]})
        affected_hosts = sorted({item["agent_dn"] for item in items if item.get("agent_dn")})
        latest_time = max((item["last_time"] for item in items if item.get("last_time")), default=None)
        return {
            "total": len(items),
            "active": len(active),
            "cleared": len(items) - len(active),
            "by_level": {level: by_level.get(level, 0) for level in ("CRITICAL", "MAJOR", "MINOR", "WARNING", "INFO")},
            "affected_clusters": affected_clusters,
            "affected_hosts": affected_hosts,
            "highest_level": max((item["alarm_level"] for item in items), key=lambda level: self._SEVERITY[level], default=None),
            "latest_time": latest_time,
        }

    def _inspection_status(self, summary: dict[str, Any]) -> str:
        levels = summary["by_level"]
        if levels["CRITICAL"] or (levels["MAJOR"] and summary["active"]):
            return "critical"
        if summary["total"]:
            return "warning"
        return "healthy"

    def _timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self._settings.alert_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Invalid alert timezone: {self._settings.alert_timezone}") from exc

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _parse_time(value: Any, timezone: ZoneInfo, name: str) -> datetime:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty time string.")
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            try:
                parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            except ValueError as exc:
                raise ValueError(f"{name} must be ISO 8601 or YYYY-MM-DD HH:MM:SS.") from exc
        return parsed.astimezone(timezone) if parsed.tzinfo else parsed.replace(tzinfo=timezone)

    @staticmethod
    def _string_list(value: Any, name: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError(f"{name} must be a list of non-empty strings.")
        return [item.strip() for item in value]

    def _levels(self, value: Any) -> list[str]:
        levels = self._string_list(value, "alarm_levels")
        normalized = [level.upper() for level in levels]
        if any(level not in self._LEVELS for level in normalized):
            raise ValueError("alarm_levels must contain CRITICAL, MAJOR, MINOR, WARNING, or INFO.")
        return normalized
