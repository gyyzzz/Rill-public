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
    _REPORT_PROMPT = (
        "告警查询结果必须严格按以下固定格式输出，不要输出原始告警事件：\n"
        "【查询范围】start_time、end_time、筛选条件。\n"
        "【告警统计】未清除告警、已清除告警、总告警、聚合项数量及未清除占比。\n"
        "【未清除告警TopN】按告警数量降序、告警级别严重程度升序，逐项输出：告警标题、告警级别、数量。\n"
        "【总告警TopN】按总数量降序、告警级别严重程度升序，逐项输出：告警编码、告警标题、告警级别、未清除数量、已清除数量、总数量。\n"
        "【分析】用2-4句话说明未清除占比、最高严重级别告警、最高频告警和建议优先处理项。"
    )
    description = (
        "Inspect EOP alerts using fixed read-only aggregations. If no time is provided, "
        "query the latest 30 minutes. If exactly one time point is provided in time_point, "
        "start_time, or end_time, query 30 minutes before and after that point. Results "
        "include alert level and fixed TopN summaries for uncleared and total alerts, "
        "ranked by count and severity. After receiving the result, follow the report_prompt "
        "exactly. Raw alert events are never returned."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "window_minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
            "time_point": {"type": "string", "description": "ISO 8601 or YYYY-MM-DD HH:MM:SS. Queries +/- 30 minutes around this point."},
            "start_time": {"type": "string", "description": "ISO 8601 or YYYY-MM-DD HH:MM:SS."},
            "end_time": {"type": "string", "description": "ISO 8601 or YYYY-MM-DD HH:MM:SS."},
            "host_names": {"type": "array", "items": {"type": "string"}},
            "alarm_levels": {"type": "array", "items": {"type": "string"}},
            "alarm_codes": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }

    _DEFAULT_WINDOW_MINUTES = 30
    _LEVEL_MAP = {"CRITICAL": 0, "严重": 0, "MAJOR": 1, "重要": 1, "MINOR": 2, "次要": 2, "WARNING": 3, "警告": 3, "INFO": 4, "信息": 4}
    _LEVEL_LABELS = {0: "CRITICAL", 1: "MAJOR", 2: "MINOR", 3: "WARNING", 4: "INFO"}

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
                    cursor.execute(query["summary_sql"], query["params"])
                    summary_row = cursor.fetchone() or {}
                    cursor.execute(query["active_sql"], query["params"])
                    active_rows = cursor.fetchall()
                    cursor.execute(query["status_sql"], query["params"])
                    status_rows = cursor.fetchall()
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"Alert database query failed: {exc}"}

        active_by_title = [self._row_to_active_group(row) for row in active_rows]
        groups = [{
            "alarm_code": row.get("alarm_code") or "UNKNOWN",
            "title": row.get("title") or "UNKNOWN",
            "alarm_level": self._format_level(row.get("alarm_level")),
            "uncleared_count": int(row.get("uncleared_count") or 0),
            "cleared_count": int(row.get("cleared_count") or 0),
            "total_count": int(row.get("total_count") or 0),
        } for row in status_rows]
        active_total = int(summary_row.get("uncleared_total") or 0)
        cleared_total = int(summary_row.get("cleared_total") or 0)
        total_count = int(summary_row.get("total_count") or 0)
        group_count = int(summary_row.get("group_count") or 0)
        return {
            "ok": True,
            "source": "eop_database",
            "query": query["query"],
            "inspection_status": "critical" if active_total else "healthy",
            "summary": {
                "uncleared_total": active_total,
                "cleared_total": cleared_total,
                "total_count": total_count,
                "group_count": group_count,
                "top_n": self._settings.alert_top_n,
                "uncleared_percentage": round(active_total / total_count * 100, 2) if total_count else 0,
            },
            "current_uncleared_by_title": active_by_title,
            "by_alarm_code_title_and_status": groups,
            "top_n_details": {
                "uncleared_alerts": active_by_title,
                "total_alerts": groups,
            },
            "report_prompt": self._REPORT_PROMPT,
        }

    def _build_query(self, arguments: dict[str, Any]) -> dict[str, Any]:
        start_raw, end_raw, point_raw = arguments.get("start_time"), arguments.get("end_time"), arguments.get("time_point")
        timezone = self._timezone()
        if point_raw and (start_raw or end_raw):
            raise ValueError("time_point cannot be combined with start_time or end_time.")
        if start_raw and end_raw:
            start, end = self._parse_time(start_raw, timezone, "start_time"), self._parse_time(end_raw, timezone, "end_time")
            if start >= end:
                raise ValueError("start_time must be earlier than end_time.")
            time_mode = "range"
        elif point_raw or start_raw or end_raw:
            point_name = "time_point" if point_raw else "start_time" if start_raw else "end_time"
            point = self._parse_time(point_raw or start_raw or end_raw, timezone, point_name)
            start = point - timedelta(minutes=self._DEFAULT_WINDOW_MINUTES)
            end = point + timedelta(minutes=self._DEFAULT_WINDOW_MINUTES)
            time_mode = "point_plus_minus_30_minutes"
        else:
            window = arguments.get("window_minutes", self._DEFAULT_WINDOW_MINUTES)
            if not isinstance(window, int) or isinstance(window, bool) or not 1 <= window <= 10080:
                raise ValueError("window_minutes must be an integer between 1 and 10080.")
            end = datetime.now(timezone)
            start = end - timedelta(minutes=window)
            time_mode = "latest_window"

        params: dict[str, Any] = {"start_time": self._format_time(start), "end_time": self._format_time(end)}
        where = ["x1.first_time <= %(end_time)s", "x1.last_time >= %(start_time)s"]
        self._append_list_filter(where, params, "x1.instance", arguments.get("host_names"), "host_names", "host")
        self._append_list_filter(where, params, "x1.alarm_code", arguments.get("alarm_codes"), "alarm_codes", "code")
        self._append_level_filter(where, params, arguments.get("alarm_levels"))
        where_sql = " AND ".join(where)
        join_sql = "LEFT JOIN m_alarm_base x0 ON x0.alarm_code = x1.alarm_code"
        level_expr = "COALESCE(x1.curr_level, 999)"
        summary_sql = f"SELECT COUNT(*) AS total_count, SUM(CASE WHEN x1.clear_status = 0 THEN 1 ELSE 0 END) AS uncleared_total, SUM(CASE WHEN x1.clear_status = 1 THEN 1 ELSE 0 END) AS cleared_total, COUNT(DISTINCT (x1.alarm_code, COALESCE(x0.title, 'UNKNOWN'), {level_expr})) AS group_count FROM d_alarm_event_new x1 {join_sql} WHERE {where_sql}"
        active_sql = f"SELECT COALESCE(x0.title, 'UNKNOWN') AS title, {level_expr} AS alarm_level, COUNT(*) AS count FROM d_alarm_event_new x1 {join_sql} WHERE {where_sql} AND x1.clear_status = 0 GROUP BY COALESCE(x0.title, 'UNKNOWN'), {level_expr} ORDER BY count DESC, {level_expr}, title LIMIT %(top_n)s"
        status_sql = f"SELECT x1.alarm_code AS alarm_code, COALESCE(x0.title, 'UNKNOWN') AS title, {level_expr} AS alarm_level, SUM(CASE WHEN x1.clear_status = 0 THEN 1 ELSE 0 END) AS uncleared_count, SUM(CASE WHEN x1.clear_status = 1 THEN 1 ELSE 0 END) AS cleared_count, COUNT(*) AS total_count FROM d_alarm_event_new x1 {join_sql} WHERE {where_sql} GROUP BY x1.alarm_code, COALESCE(x0.title, 'UNKNOWN'), {level_expr} ORDER BY total_count DESC, {level_expr}, x1.alarm_code, title LIMIT %(status_limit)s"
        params["top_n"] = self._settings.alert_top_n
        params["status_limit"] = self._settings.alert_top_n
        return {"summary_sql": summary_sql, "active_sql": active_sql, "status_sql": status_sql, "params": params, "query": {"time_mode": time_mode, "start_time": params["start_time"], "end_time": params["end_time"], "filters": {"host_names": arguments.get("host_names") or [], "alarm_codes": arguments.get("alarm_codes") or [], "alarm_levels": arguments.get("alarm_levels") or []}}}

    def _row_to_active_group(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": row.get("title") or "UNKNOWN",
            "alarm_level": self._format_level(row.get("alarm_level")),
            "count": int(row.get("count") or 0),
        }

    def _format_level(self, value: Any) -> str:
        try:
            level = int(value)
        except (TypeError, ValueError):
            return "UNKNOWN"
        return self._LEVEL_LABELS.get(level, f"UNKNOWN({level})")

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
