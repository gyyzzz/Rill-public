import asyncio
from pathlib import Path

from orionxcore.config import Settings
from orionxcore.tools.chart import ChartTool


def _make_settings(tmp_path: Path, **overrides) -> Settings:
    base = {
        "api_key": "test",
        "enable_chart": True,
        "chart_workdir": tmp_path / "charts",
        "terminal_workdir": tmp_path,
    }
    base.update(overrides)
    return Settings(**base)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_bar_chart(tmp_path):
    tool = ChartTool(_make_settings(tmp_path))
    result = _run(
        tool.execute(
            {
                "chart_type": "bar",
                "data": {
                    "labels": ["Q1", "Q2", "Q3", "Q4"],
                    "values": [100, 200, 150, 300],
                },
                "title": "Quarterly Sales",
                "xlabel": "Quarter",
                "ylabel": "Revenue",
            }
        )
    )
    assert result["ok"] is True
    assert result["chart_type"] == "bar"
    assert Path(result["path"]).exists()
    assert result["size_bytes"] > 0


def test_line_chart_multi_series(tmp_path):
    tool = ChartTool(_make_settings(tmp_path))
    result = _run(
        tool.execute(
            {
                "chart_type": "line",
                "data": {
                    "labels": ["Jan", "Feb", "Mar", "Apr"],
                    "values": [[10, 20, 15, 25], [5, 15, 10, 20]],
                    "series_names": ["Product A", "Product B"],
                },
                "title": "Monthly Trends",
            }
        )
    )
    assert result["ok"] is True
    assert result["chart_type"] == "line"
    assert Path(result["path"]).exists()


def test_pie_chart(tmp_path):
    tool = ChartTool(_make_settings(tmp_path))
    result = _run(
        tool.execute(
            {
                "chart_type": "pie",
                "data": {
                    "labels": ["A", "B", "C"],
                    "values": [30, 45, 25],
                },
                "title": "Market Share",
            }
        )
    )
    assert result["ok"] is True
    assert result["chart_type"] == "pie"
    assert Path(result["path"]).exists()


def test_scatter_chart(tmp_path):
    tool = ChartTool(_make_settings(tmp_path))
    result = _run(
        tool.execute(
            {
                "chart_type": "scatter",
                "data": {
                    "labels": ["P1", "P2", "P3", "P4", "P5"],
                    "values": [10, 25, 15, 30, 20],
                },
            }
        )
    )
    assert result["ok"] is True
    assert result["chart_type"] == "scatter"
    assert Path(result["path"]).exists()


def test_area_chart(tmp_path):
    tool = ChartTool(_make_settings(tmp_path))
    result = _run(
        tool.execute(
            {
                "chart_type": "area",
                "data": {
                    "labels": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                    "values": [[5, 10, 8, 12, 9], [3, 7, 5, 9, 6]],
                    "series_names": ["Team A", "Team B"],
                },
                "title": "Weekly Activity",
            }
        )
    )
    assert result["ok"] is True
    assert result["chart_type"] == "area"
    assert Path(result["path"]).exists()


def test_custom_filename_and_size(tmp_path):
    tool = ChartTool(_make_settings(tmp_path))
    result = _run(
        tool.execute(
            {
                "chart_type": "bar",
                "data": {
                    "labels": ["X", "Y"],
                    "values": [10, 20],
                },
                "filename": "custom_chart.png",
                "width": 1200,
                "height": 800,
            }
        )
    )
    assert result["ok"] is True
    assert result["filename"] == "custom_chart.png"
    assert Path(result["path"]).name == "custom_chart.png"
    assert result["width"] == 1200
    assert result["height"] == 800


def test_missing_labels(tmp_path):
    tool = ChartTool(_make_settings(tmp_path))
    result = _run(
        tool.execute(
            {
                "chart_type": "bar",
                "data": {"values": [1, 2, 3]},
            }
        )
    )
    assert result["ok"] is False
    assert "labels" in result["error"]


def test_missing_values(tmp_path):
    tool = ChartTool(_make_settings(tmp_path))
    result = _run(
        tool.execute(
            {
                "chart_type": "bar",
                "data": {"labels": ["A", "B"]},
            }
        )
    )
    assert result["ok"] is False
    assert "values" in result["error"]


def test_length_mismatch(tmp_path):
    tool = ChartTool(_make_settings(tmp_path))
    result = _run(
        tool.execute(
            {
                "chart_type": "bar",
                "data": {
                    "labels": ["A", "B", "C"],
                    "values": [1, 2],
                },
            }
        )
    )
    assert result["ok"] is False
    assert "match" in result["error"].lower()


def test_invalid_chart_type(tmp_path):
    tool = ChartTool(_make_settings(tmp_path))
    result = _run(
        tool.execute(
            {
                "chart_type": "radar",
                "data": {"labels": ["A"], "values": [1]},
            }
        )
    )
    assert result["ok"] is False
    assert "chart_type" in result["error"]


def test_path_escape_blocked(tmp_path):
    tool = ChartTool(_make_settings(tmp_path))
    result = _run(
        tool.execute(
            {
                "chart_type": "bar",
                "data": {"labels": ["A"], "values": [1]},
                "filename": "../../../etc/evil.png",
            }
        )
    )
    assert result["ok"] is False
    assert "escapes" in result["error"]


def test_filename_without_extension(tmp_path):
    tool = ChartTool(_make_settings(tmp_path))
    result = _run(
        tool.execute(
            {
                "chart_type": "bar",
                "data": {"labels": ["A"], "values": [1]},
                "filename": "no_ext",
            }
        )
    )
    assert result["ok"] is True
    assert result["filename"] == "no_ext.png"


def test_series_names_mismatch(tmp_path):
    tool = ChartTool(_make_settings(tmp_path))
    result = _run(
        tool.execute(
            {
                "chart_type": "line",
                "data": {
                    "labels": ["A", "B"],
                    "values": [[1, 2], [3, 4]],
                    "series_names": ["Only One"],
                },
            }
        )
    )
    assert result["ok"] is False
    assert "series_names" in result["error"]


def test_max_size_capped(tmp_path):
    tool = ChartTool(
        _make_settings(
            tmp_path,
            chart_max_width=500,
            chart_max_height=400,
        )
    )
    result = _run(
        tool.execute(
            {
                "chart_type": "bar",
                "data": {"labels": ["A"], "values": [1]},
                "width": 9999,
                "height": 9999,
            }
        )
    )
    assert result["ok"] is True
    assert result["width"] == 500
    assert result["height"] == 400


def test_chinese_characters(tmp_path):
    tool = ChartTool(_make_settings(tmp_path))
    result = _run(
        tool.execute(
            {
                "chart_type": "bar",
                "data": {
                    "labels": ["一季度", "二季度", "三季度", "四季度"],
                    "values": [100, 200, 150, 300],
                },
                "title": "季度销售额统计",
                "xlabel": "季度",
                "ylabel": "金额（万元）",
            }
        )
    )
    assert result["ok"] is True
    assert result["chart_type"] == "bar"
    assert Path(result["path"]).exists()
    assert result["size_bytes"] > 0
