from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import clickhouse_connect

from orionxcore.config import Settings
from orionxcore.tools.base import Tool


class MetricsQueryTool(Tool):
    """Read-only metrics inspection against ClickHouse metrics tables."""

    name = "metrics_query"
    _REPORT_PROMPT = (
        "指标查询结果必须严格按以下固定格式输出，不要输出原始数据：\n"
        "【查询范围】业务模块、时间范围。\n"
        "【当前时段指标】各指标的请求数、成功数、成功率。\n"
        "【前一日对比】各指标的请求数变化、成功数变化、成功率变化。\n"
        "【分析】用1-2句话说明指标趋势、异常项和重点关注项。"
    )

    description = (
        "Query gold metrics from ClickHouse. If no time is provided, query the latest 30 minutes. "
        "If exactly one time point is provided, query 30 minutes before and after that point. "
        "Results include current period metrics and comparison with the same period one day ago. "
        "After receiving the result, follow the report_prompt exactly."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "business_module": {
                "type": "string",
                "enum": ["NCP", "SNE", "SCPAS"],
                "description": "业务模块名称：NCP、SNE、SCPAS",
            },
            "time_point": {
                "type": "string",
                "description": "ISO 8601 or YYYY-MM-DD HH:MM:SS. Queries +/- 30 minutes around this point.",
            },
            "nename": {
                "type": "string",
                "description": "网元名称，可选筛选条件",
            },
        },
        "required": ["business_module"],
        "additionalProperties": False,
    }

    _DEFAULT_WINDOW_MINUTES = 30

    # 业务模块对应的表和指标配置
    _MODULE_CONFIG = {
        "NCP": {
            "tables": [
                {
                    "name": "metrics.r_NCP_User_1min_ne",
                    "metrics": [
                        {"field": "NCP_AnswerTraf", "desc": "新通话应答话务量(erl)", "has_rate": False},
                        {"field": "NCP_DCAnswerTraf", "desc": "新通话DC应答话务量(erl)", "has_rate": False},
                    ],
                },
                {
                    "name": "metrics.r_NCP_Media_1min_ne",
                    "metrics": [
                        {
                            "request_field": "NCP_MediaAnchor",
                            "success_field": "NCP_MediaAnchorSuc",
                            "desc": "音视频锚定",
                            "has_rate": True,
                        },
                        {
                            "request_field": "NCP_DownloadApplicationList",
                            "success_field": "NCP_DownloadApplicationListSuc",
                            "desc": "小程序列表下载",
                            "has_rate": True,
                        },
                    ],
                },
                {
                    "name": "metrics.r_pd_scp_volteasv_vonr_15min_ne",
                    "metrics": [
                        {
                            "request_field": "ImsCallReq",
                            "success_field": "ImsCallExe",
                            "desc": "IMS域智能呼叫触发(VONR)",
                            "has_rate": True,
                        },
                    ],
                },
            ],
        },
        "SNE": {
            "tables": [
                {
                    "name": "metrics.r_YWPT_HTTP_1min_ne",
                    "metrics": [
                        {
                            "request_field": "ControlReq",
                            "success_field": "ControlRsp",
                            "desc": "呼叫时间控制",
                            "has_rate": True,
                        },
                    ],
                },
            ],
        },
        "SCPAS": {
            "tables": [
                {
                    "name": "metrics.r_pd_scp_volteas_1min_ne",
                    "metrics": [
                        {
                            "request_field": "ImsCallReq",
                            "success_field": "ImsCallExe",
                            "desc": "IMS域智能呼叫触发",
                            "has_rate": True,
                        },
                    ],
                },
            ],
        },
    }

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._settings.metrics_database_url.strip():
            return {"ok": False, "error": "Metrics database is not configured."}

        try:
            business_module = arguments.get("business_module")
            if not business_module or business_module not in self._MODULE_CONFIG:
                return {"ok": False, "error": f"Invalid business_module: {business_module}"}

            time_range = self._build_time_range(arguments)
            client = self._get_client()

            current_metrics = self._query_metrics(client, business_module, time_range["current"], arguments.get("nename"))
            previous_metrics = self._query_metrics(
                client, business_module, time_range["previous"], arguments.get("nename")
            )

            comparison = self._compare_metrics(current_metrics, previous_metrics)

            client.close()

        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"Metrics database query failed: {exc}"}

        return {
            "ok": True,
            "source": "clickhouse",
            "business_module": business_module,
            "query": {
                "current_period": time_range["current"],
                "previous_period": time_range["previous"],
                "nename": arguments.get("nename"),
            },
            "current_metrics": current_metrics,
            "previous_metrics": previous_metrics,
            "comparison": comparison,
            "report_prompt": self._REPORT_PROMPT,
        }

    def _build_time_range(self, arguments: dict[str, Any]) -> dict[str, dict[str, str]]:
        """构建查询时间范围，返回当前时段和前一时段。"""
        timezone = self._timezone()
        point_raw = arguments.get("time_point")

        if point_raw:
            point = self._parse_time(point_raw, timezone, "time_point")
        else:
            point = datetime.now(timezone)

        # 当前时段：时间点前后30分钟
        current_start = point - timedelta(minutes=self._DEFAULT_WINDOW_MINUTES)
        current_end = point + timedelta(minutes=self._DEFAULT_WINDOW_MINUTES)

        # 前一时段：前一天同一时间段
        previous_start = current_start - timedelta(days=1)
        previous_end = current_end - timedelta(days=1)

        return {
            "current": {
                "start_time": self._format_time(current_start),
                "end_time": self._format_time(current_end),
            },
            "previous": {
                "start_time": self._format_time(previous_start),
                "end_time": self._format_time(previous_end),
            },
        }

    def _get_client(self):
        """获取ClickHouse客户端连接。"""
        url = self._settings.metrics_database_url.strip()

        # 支持多种URL格式:
        # clickhouse://user:password@host:port/database
        # clickhousedb://user:password@host:port/database
        # http://host:8123?user=xxx&password=xxx
        try:
            # 标准化URL格式
            normalized_url = url.replace("clickhousedb://", "clickhouse://", 1)
            parsed = urlparse(normalized_url)

            if not parsed.hostname:
                raise ValueError("Missing hostname in ClickHouse URL.")

            # 提取连接参数，对用户名和密码进行URL解码
            host = parsed.hostname
            port = parsed.port or 8123  # 默认使用HTTP端口
            username = unquote(parsed.username) if parsed.username else "default"
            password = unquote(parsed.password) if parsed.password else ""
            database = parsed.path.lstrip("/") if parsed.path else "default"

            return clickhouse_connect.get_client(
                host=host,
                port=port,
                username=username,
                password=password,
                database=database,
            )
        except Exception as exc:
            raise ValueError(f"Invalid ClickHouse database URL format: {exc}") from exc

    def _query_metrics(
        self,
        client,
        business_module: str,
        time_range: dict[str, str],
        nename: str | None,
    ) -> list[dict[str, Any]]:
        """查询指定业务模块和时间范围的指标。"""
        results = []
        config = self._MODULE_CONFIG[business_module]

        for table_config in config["tables"]:
            table_name = table_config["name"]

            for metric in table_config["metrics"]:
                result = {
                    "table": table_name,
                    "metric_desc": metric["desc"],
                }

                # 构建SQL查询
                if metric.get("has_rate"):
                    # 有成功率计算的指标
                    request_field = metric["request_field"]
                    success_field = metric["success_field"]

                    sql = f"""
                        SELECT
                            sum({request_field}) as request_count,
                            sum({success_field}) as success_count
                        FROM {table_name}
                        WHERE end_ts >= %(start_time)s
                          AND end_ts <= %(end_time)s
                    """
                    params = {
                        "start_time": time_range["start_time"],
                        "end_time": time_range["end_time"],
                    }

                    if nename:
                        sql += " AND nename = %(nename)s"
                        params["nename"] = nename

                    query_result = client.query(sql, params)
                    if query_result.result_rows:
                        request_count = float(query_result.result_rows[0][0] or 0)
                        success_count = float(query_result.result_rows[0][1] or 0)
                        result["request_count"] = request_count
                        result["success_count"] = success_count
                        result["success_rate"] = (
                            round(success_count / request_count * 100, 2) if request_count > 0 else 0
                        )
                    else:
                        result["request_count"] = 0
                        result["success_count"] = 0
                        result["success_rate"] = 0
                else:
                    # 没有成功率的指标（绝对值）
                    field = metric["field"]
                    sql = f"""
                        SELECT sum({field}) as total_value
                        FROM {table_name}
                        WHERE end_ts >= %(start_time)s
                          AND end_ts <= %(end_time)s
                    """
                    params = {
                        "start_time": time_range["start_time"],
                        "end_time": time_range["end_time"],
                    }

                    if nename:
                        sql += " AND nename = %(nename)s"
                        params["nename"] = nename

                    query_result = client.query(sql, params)
                    if query_result.result_rows:
                        result["total_value"] = float(query_result.result_rows[0][0] or 0)
                    else:
                        result["total_value"] = 0

                results.append(result)

        return results

    def _compare_metrics(
        self,
        current: list[dict[str, Any]],
        previous: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """对比当前指标与前一日指标。"""
        comparisons = []

        for curr, prev in zip(current, previous):
            comparison = {
                "table": curr["table"],
                "metric_desc": curr["metric_desc"],
            }

            if "request_count" in curr:
                # 有成功率的指标
                request_diff = curr["request_count"] - prev.get("request_count", 0)
                success_diff = curr["success_count"] - prev.get("success_count", 0)
                rate_diff = curr["success_rate"] - prev.get("success_rate", 0)

                comparison["request_count_current"] = curr["request_count"]
                comparison["request_count_previous"] = prev.get("request_count", 0)
                comparison["request_count_change"] = round(request_diff, 2)
                comparison["request_count_change_pct"] = (
                    round(request_diff / prev["request_count"] * 100, 2)
                    if prev.get("request_count", 0) > 0
                    else 0
                )

                comparison["success_count_current"] = curr["success_count"]
                comparison["success_count_previous"] = prev.get("success_count", 0)
                comparison["success_count_change"] = round(success_diff, 2)

                comparison["success_rate_current"] = curr["success_rate"]
                comparison["success_rate_previous"] = prev.get("success_rate", 0)
                comparison["success_rate_change"] = round(rate_diff, 2)

                # 趋势判断
                if rate_diff > 5:
                    comparison["trend"] = "上升"
                elif rate_diff < -5:
                    comparison["trend"] = "下降"
                else:
                    comparison["trend"] = "稳定"

            else:
                # 绝对值指标
                value_diff = curr["total_value"] - prev.get("total_value", 0)
                comparison["total_value_current"] = curr["total_value"]
                comparison["total_value_previous"] = prev.get("total_value", 0)
                comparison["total_value_change"] = round(value_diff, 2)
                comparison["total_value_change_pct"] = (
                    round(value_diff / prev["total_value"] * 100, 2)
                    if prev.get("total_value", 0) > 0
                    else 0
                )

                if value_diff > 0:
                    comparison["trend"] = "上升"
                elif value_diff < 0:
                    comparison["trend"] = "下降"
                else:
                    comparison["trend"] = "稳定"

            comparisons.append(comparison)

        return comparisons

    def _timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self._settings.metrics_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Invalid metrics timezone: {self._settings.metrics_timezone}") from exc

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