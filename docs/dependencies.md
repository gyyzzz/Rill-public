# 依赖

Rill 使用 FastAPI 和 Uvicorn 提供 HTTP 服务，使用 HTTPX 调用 OpenAI 兼容的
Provider，使用 Pydantic Settings 处理 TOML 和环境变量配置，并使用 SQLAlchemy 及
ClickHouse/PostgreSQL 驱动支持数据库工具。图表生成功能使用 Matplotlib。

使用 `uv sync` 安装运行时依赖。开发时使用 `uv sync --extra dev` 安装 pytest。项目
不捆绑可选终端界面；目前 `tui` 命令会引导运维人员使用本地诊断命令。
