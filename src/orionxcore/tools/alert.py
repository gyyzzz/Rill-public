from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg2
from psycopg2.extras import RealDictCursor

from orionxcore.config import Settings
from orionxcore.tools.base import Tool


class AlertQueryTool(Tool):
    """Read-only aggregate alert inspection against EOP's alert tables."""

    name = "alert_query"
    description = (
        "Inspect EOP alerts using two fixed read-only aggregations: current uncleared "
        "alert counts by title, and cleared/uncleared counts grouped by alarm code and "
        "title. Use for system inspections and status checks. Raw alert events are never returned."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "window_minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
            "start_time": {"type": "string", "description": "ISO 8601 or YYYY-MM-DD HH:MM:SS."},
            "end_time": {"type": "string", "description": "ISO 8601 or YYYY-MM-DD HH:MM:SS."},
            "host_names": {"type": "array", "items": {"type": "string"}},
            "alarm_levels": {"type": "array", "items": {"type": "string"}},
            "alarm_codes": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }

    _LEVEL_MAP = {"CRITICAL": 0, "严重": 0, "MAJOR": 1, "重要": 1, "MINOR": 2, "次要": 2, "WARNING": 3, "警告": 3, "INFO": 4, "信息": 4}

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._settings.alert_database_url.strip():
            return {"ok": False, "error": "Alert database is not configured."}
        try:
            query = self._build_query(arguments)
            connection_params = self._connection_params()
            with psycopg2.connect(**connection_params) as connection:
                with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(query["active_sql"], query["params"])
                    active_rows = cursor.fetchall()
                    cursor.execute(query["status_sql"], query["params"])
                    status_rows = cursor.fetchall()
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"Alert database query failed: {exc}"}

        active_by_title = [{"title": row.get("title") or "UNKNOWN", "count": int(row.get("count") or 0)} for row in active_rows]
        groups = [{
            "alarm_code": row.get("alarm_code") or "UNKNOWN",
            "title": row.get("title") or "UNKNOWN",
            "uncleared_count": int(row.get("uncleared_count") or 0),
            "cleared_count": int(row.get("cleared_count") or 0),
            "total_count": int(row.get("total_count") or 0),
        } for row in status_rows]
        active_total = sum(row["uncleared_count"] for row in groups)
        cleared_total = sum(row["cleared_count"] for row in groups)
        return {
            "ok": True,
            "source": "eop_database",
            "query": query["query"],
            "inspection_status": "critical" if active_total else "healthy",
            "summary": {"uncleared_total": active_total, "cleared_total": cleared_total, "group_count": len(groups)},
            "current_uncleared_by_title": active_by_title,
            "by_alarm_code_title_and_status": groups,
        }

    def _build_query(self, arguments: dict[str, Any]) -> dict[str, Any]:
        start_raw, end_raw = arguments.get("start_time"), arguments.get("end_time")
        if bool(start_raw) != bool(end_raw):
            raise ValueError("start_time and end_time must be provided together.")
        timezone = self._timezone()
        if start_raw:
            start, end = self._parse_time(start_raw, timezone, "start_time"), self._parse_time(end_raw, timezone, "end_time")
            if start >= end:
                raise ValueError("start_time must be earlier than end_time.")
        else:
            window = arguments.get("window_minutes", self._settings.alert_default_window_minutes)
            if not isinstance(window, int) or isinstance(window, bool) or not 1 <= window <= 10080:
                raise ValueError("window_minutes must be an integer between 1 and 10080.")
            end = datetime.now(timezone)
            start = end - timedelta(minutes=window)

        params: dict[str, Any] = {"start_time": self._format_time(start), "end_time": self._format_time(end)}
        where = ["x1.first_time <= %(end_time)s", "x1.last_time >= %(start_time)s"]
        self._append_list_filter(where, params, "x1.instance", arguments.get("host_names"), "host_names", "host")
        self._append_list_filter(where, params, "x1.alarm_code", arguments.get("alarm_codes"), "alarm_codes", "code")
        self._append_level_filter(where, params, arguments.get("alarm_levels"))
        where_sql = " AND ".join(where)
        join_sql = "LEFT JOIN m_alarm_base x0 ON x0.alarm_code = x1.alarm_code"
        active_sql = f"SELECT COALESCE(x0.title, 'UNKNOWN') AS title, COUNT(*) AS count FROM d_alarm_event_new x1 {join_sql} WHERE {where_sql} AND x1.clear_status = 0 GROUP BY COALESCE(x0.title, 'UNKNOWN') ORDER BY count DESC, title LIMIT %(top_n)s"
        status_sql = f"SELECT x1.alarm_code AS alarm_code, COALESCE(x0.title, 'UNKNOWN') AS title, SUM(CASE WHEN x1.clear_status = 0 THEN 1 ELSE 0 END) AS uncleared_count, SUM(CASE WHEN x1.clear_status = 1 THEN 1 ELSE 0 END) AS cleared_count, COUNT(*) AS total_count FROM d_alarm_event_new x1 {join_sql} WHERE {where_sql} GROUP BY x1.alarm_code, COALESCE(x0.title, 'UNKNOWN') ORDER BY total_count DESC, x1.alarm_code, title LIMIT %(status_limit)s"
        params["top_n"] = self._settings.alert_top_n
        params["status_limit"] = self._settings.alert_top_n
        return {"active_sql": active_sql, "status_sql": status_sql, "params": params, "query": {"start_time": params["start_time"], "end_time": params["end_time"], "filters": {"host_names": arguments.get("host_names") or [], "alarm_codes": arguments.get("alarm_codes") or [], "alarm_levels": arguments.get("alarm_levels") or []}}}

    @staticmethod
    def _append_list_filter(where: list[str], params: dict[str, Any], column: str, values: Any, name: str, prefix: str) -> None:
        if values is None:
            return
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError(f"{name} must be a list of non-empty strings.")
        if values:
            keys = []
            for index, value in enumerate(values):
                key = f"{prefix}_{index}"
                keys.append(f"%({key})s")
                params[key] = value.strip()
            where.append(f"{column} IN ({', '.join(keys)})")

    def _append_level_filter(self, where: list[str], params: dict[str, Any], values: Any) -> None:
        if values is None:
            return
        if not isinstance(values, list):
            raise ValueError("alarm_levels must be a list.")
        keys = []
        for index, value in enumerate(values):
            if not isinstance(value, str) or value.upper() not in self._LEVEL_MAP:
                raise ValueError("alarm_levels contains an unsupported level.")
            key = f"level_{index}"
            params[key] = self._LEVEL_MAP[value.upper()]
            keys.append(f"%({key})s")
        if keys:
            where.append(f"x1.curr_level IN ({', '.join(keys)})")

    def _connection_params(self) -> dict[str, Any]:
        """Accept a PostgreSQL or SQLAlchemy PostgreSQL URL without SQLAlchemy."""
        raw_url = self._settings.alert_database_url.strip()
        normalized_url = raw_url.replace("postgresql+psycopg2://", "postgresql://", 1)
        parsed = urlparse(normalized_url)
        if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname or not parsed.path.strip("/"):
            raise ValueError("alert_database_url must be a PostgreSQL/OpenGauss connection URL.")
        return {
            "host": parsed.hostname,
            "port": parsed.port or 5432,
            "dbname": unquote(parsed.path.lstrip("/")),
            "user": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
            "connect_timeout": self._settings.alert_database_timeout_seconds,
        }

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
