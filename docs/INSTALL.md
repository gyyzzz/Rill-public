# 安装

## 本地开发

```bash
uv sync --extra dev
cp config.toml.example config.toml
export ORIONXCORE_LLM__API_KEY=your_api_key
uv run rill config check
uv run rill serve
```

默认 Provider 使用 OpenAI 兼容 API。要使用其他兼容服务，请配置
`llm.api_base_url`、`llm.model` 和 API Key。多 Provider 配置示例见
[`config.toml.example`](../config.toml.example)。

## Docker

将 `config.toml.example` 复制为 `config.toml`，通过环境变量设置 API Key，然后运行：

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

服务监听 `server.host` 和 `server.port`，默认值为 `0.0.0.0:8080`。部署前请运行
`rill config check` 验证 TOML 配置。默认运行环境会启用终端、文件系统和数据库工具；
图表生成功能需要显式启用。

如需非交互式初始化，请使用 `rill init --non-interactive`；通过 `--model` 和
`--base-url` 设置模型及 API Base URL，然后在 `ORIONXCORE_LLM__API_KEY` 中提供凭据，
或挂载 `api_key_file`。
