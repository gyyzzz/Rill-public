import logging
import time
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib import font_manager

from orionxcore.config import Settings
from orionxcore.tools.base import Tool

logger = logging.getLogger(__name__)

CHART_TYPES = ("bar", "line", "pie", "scatter", "area")

# 常见中文字体候选列表（按优先级排列，覆盖 macOS / Windows / Linux）
_CHINESE_FONT_CANDIDATES = [
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "SimHei",
    "STHeiti",
    "WenQuanYi Micro Hei",
    "WenQuanYi Zen Hei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "AR PL UMing CN",
    "Arial Unicode MS",
]

_font_configured = False


def _configure_chinese_font(custom_family: str = "") -> None:
    """配置 matplotlib 以支持中文字符显示。"""
    global _font_configured
    if _font_configured:
        return

    available_fonts = {f.name for f in font_manager.fontManager.ttflist}

    chosen: str | None = None
    if custom_family and custom_family in available_fonts:
        chosen = custom_family
    else:
        for candidate in _CHINESE_FONT_CANDIDATES:
            if candidate in available_fonts:
                chosen = candidate
                break

    if chosen:
        plt.rcParams["font.sans-serif"] = [chosen] + plt.rcParams["font.sans-serif"]
        logger.info(f"图表中文字体已配置: {chosen}")
    else:
        logger.warning(
            "未找到可用的中文字体，图表中的中文可能显示为乱码。"
            "请安装中文字体或通过 ORIONXCORE_CHART_FONT_FAMILY 指定。"
        )

    # 解决负号显示为方块的问题
    plt.rcParams["axes.unicode_minus"] = False
    _font_configured = True


class ChartTool(Tool):
    name = "generate_chart"
    description = (
        "Generate a chart image (bar, line, pie, scatter, area) from provided data "
        "and save it as a PNG file. Use this tool when the user asks for a visualization, "
        "chart, graph, or plot."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "chart_type": {
                "type": "string",
                "enum": list(CHART_TYPES),
                "description": "Type of chart to generate.",
            },
            "data": {
                "type": "object",
                "description": (
                    "Chart data. Must contain 'labels' (array of strings) and 'values' "
                    "(array of numbers for single series, or array of arrays for multi-series). "
                    "For multi-series, you may also provide 'series_names' (array of strings)."
                ),
                "properties": {
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "values": {
                        "type": "array",
                    },
                    "series_names": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["labels", "values"],
            },
            "title": {
                "type": "string",
                "description": "Chart title (optional).",
            },
            "xlabel": {
                "type": "string",
                "description": "X-axis label (optional).",
            },
            "ylabel": {
                "type": "string",
                "description": "Y-axis label (optional).",
            },
            "filename": {
                "type": "string",
                "description": (
                    "Output filename (optional). If omitted, a timestamped name is generated. "
                    "Should end with .png."
                ),
            },
            "width": {
                "type": "integer",
                "description": "Image width in pixels (optional, default 800).",
                "minimum": 100,
            },
            "height": {
                "type": "integer",
                "description": "Image height in pixels (optional, default 600).",
                "minimum": 100,
            },
        },
        "required": ["chart_type", "data"],
        "additionalProperties": False,
    }

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        _configure_chinese_font(settings.chart_font_family)

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        chart_type = arguments.get("chart_type")
        data = arguments.get("data")

        if chart_type not in CHART_TYPES:
            return {"ok": False, "error": f"Invalid chart_type: {chart_type}"}

        if not isinstance(data, dict):
            return {"ok": False, "error": "data must be an object"}

        labels = data.get("labels")
        values = data.get("values")

        if not labels or not isinstance(labels, list):
            return {"ok": False, "error": "data.labels must be a non-empty array"}
        if not values or not isinstance(values, list):
            return {"ok": False, "error": "data.values must be a non-empty array"}

        # Determine if multi-series
        is_multi = isinstance(values[0], list)
        if is_multi:
            for series in values:
                if len(series) != len(labels):
                    return {
                        "ok": False,
                        "error": f"Each series in values must have {len(labels)} items to match labels",
                    }
        else:
            if len(values) != len(labels):
                return {
                    "ok": False,
                    "error": f"values length ({len(values)}) must match labels length ({len(labels)})",
                }
            values = [values]

        series_names = data.get("series_names")
        if series_names and len(series_names) != len(values):
            return {
                "ok": False,
                "error": f"series_names length ({len(series_names)}) must match number of series ({len(values)})",
            }

        title = arguments.get("title")
        xlabel = arguments.get("xlabel")
        ylabel = arguments.get("ylabel")
        requested_width = int(arguments.get("width") or 800)
        requested_height = int(arguments.get("height") or 600)

        width = min(requested_width, self._settings.chart_max_width)
        height = min(requested_height, self._settings.chart_max_height)
        dpi = self._settings.chart_default_dpi

        fig_width = width / dpi
        fig_height = height / dpi

        try:
            output_path = self._resolve_output_path(arguments.get("filename"))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        try:
            fig, ax = plt.subplots(figsize=(fig_width, fig_height), dpi=dpi)

            x_positions = range(len(labels))

            if chart_type == "bar":
                self._draw_bar(ax, x_positions, labels, values, series_names)
            elif chart_type == "line":
                self._draw_line(ax, x_positions, labels, values, series_names)
            elif chart_type == "pie":
                self._draw_pie(ax, labels, values, series_names)
            elif chart_type == "scatter":
                self._draw_scatter(ax, x_positions, labels, values, series_names)
            elif chart_type == "area":
                self._draw_area(ax, x_positions, labels, values, series_names)

            if title:
                ax.set_title(title, fontsize=14, fontweight="bold")
            if xlabel and chart_type != "pie":
                ax.set_xlabel(xlabel)
            if ylabel and chart_type != "pie":
                ax.set_ylabel(ylabel)

            if chart_type != "pie":
                ax.set_xticks(list(x_positions))
                ax.set_xticklabels(labels, rotation=45 if len(labels) > 6 else 0, ha="right")
                ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
                ax.grid(axis="y", alpha=0.3)

            plt.tight_layout()

            output_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(str(output_path), format="png", bbox_inches="tight", facecolor="white")
            plt.close(fig)

            size_bytes = output_path.stat().st_size
            logger.info(f"图表已生成: {output_path}, 大小: {size_bytes} bytes")

            return {
                "ok": True,
                "path": str(output_path),
                "filename": output_path.name,
                "chart_type": chart_type,
                "size_bytes": size_bytes,
                "width": width,
                "height": height,
            }
        except Exception as exc:
            plt.close("all")
            logger.error(f"图表生成失败: {exc}")
            return {"ok": False, "error": f"Chart generation failed: {exc}"}

    def _draw_bar(
        self,
        ax: Any,
        x_positions: range,
        labels: list[str],
        values: list[list],
        series_names: list[str] | None,
    ) -> None:
        import numpy as np

        n_series = len(values)
        bar_width = 0.8 / n_series
        x_arr = np.array(list(x_positions))

        for i, series in enumerate(values):
            offset = (i - n_series / 2 + 0.5) * bar_width
            label = series_names[i] if series_names else None
            ax.bar(x_arr + offset, series, bar_width, label=label, alpha=0.85)

        if series_names:
            ax.legend()

    def _draw_line(
        self,
        ax: Any,
        x_positions: range,
        labels: list[str],
        values: list[list],
        series_names: list[str] | None,
    ) -> None:
        for i, series in enumerate(values):
            label = series_names[i] if series_names else None
            ax.plot(list(x_positions), series, marker="o", linewidth=2, label=label)

        if series_names:
            ax.legend()

    def _draw_pie(
        self,
        ax: Any,
        labels: list[str],
        values: list[list],
        series_names: list[str] | None,
    ) -> None:
        # Pie chart only uses the first series
        series = values[0]
        ax.pie(
            series,
            labels=labels,
            autopct="%1.1f%%",
            startangle=90,
        )
        ax.axis("equal")

    def _draw_scatter(
        self,
        ax: Any,
        x_positions: range,
        labels: list[str],
        values: list[list],
        series_names: list[str] | None,
    ) -> None:
        for i, series in enumerate(values):
            label = series_names[i] if series_names else None
            ax.scatter(list(x_positions), series, s=60, alpha=0.7, label=label)

        if series_names:
            ax.legend()

    def _draw_area(
        self,
        ax: Any,
        x_positions: range,
        labels: list[str],
        values: list[list],
        series_names: list[str] | None,
    ) -> None:
        x_list = list(x_positions)
        for i, series in enumerate(values):
            label = series_names[i] if series_names else None
            ax.fill_between(x_list, series, alpha=0.4, label=label)
            ax.plot(x_list, series, linewidth=1.5)

        if series_names:
            ax.legend()

    def _resolve_output_path(self, filename: str | None) -> Path:
        if self._settings.chart_workdir:
            base = self._settings.chart_workdir.resolve()
        elif self._settings.filesystem_workdir:
            base = (self._settings.filesystem_workdir / "charts").resolve()
        else:
            base = (self._settings.terminal_workdir / "workspace" / "charts").resolve()

        if not filename:
            timestamp = int(time.time())
            filename = f"chart_{timestamp}.png"

        if not filename.endswith(".png"):
            filename += ".png"

        candidate = (base / filename).resolve()
        if candidate != base and base not in candidate.parents:
            raise ValueError(f"Path escapes chart workdir: {candidate}")

        return candidate
