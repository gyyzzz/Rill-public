# 部署

Rill 是单进程 FastAPI 服务，可直接运行或部署在 Docker 中。`rill serve` 会读取
`server.host` 和 `server.port`；也可以通过 Uvicorn 或 Docker 显式覆盖绑定地址。对外
开放服务前，请配置模型凭据、工作区路径和要启用的工具。

使用 Docker Compose 时，请在项目根目录旁准备 `config.toml` 和 `.env` 文件，然后运行：

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

镜像会检查 `/health/ready`；`/health/live` 用于报告进程存活状态。默认配置会启用
终端、文件系统和数据库工具；只有设置 `chart.enabled = true` 才会启用图表生成。未
配置数据库 URL 不会导致就绪检查失败；调用数据库工具时会返回配置错误。若部署将
对不可信客户端开放，请先设置 `[auth].enabled = true` 并配置
`ORIONXCORE_AUTH__API_KEYS`。入口处应使用 HTTPS。设置
`ORIONXCORE_LLM__API_KEY_FILE=/run/secrets/llm_api_key`，即可从挂载的 Docker Secret
读取 Provider 凭据。

对于会造成破坏的文件系统操作和终端命令，调用方必须在该请求的 `approved_tools` 中
加入工具名称。也可以在配置中禁用终端执行。

CLI 通过 `--approve-tool` 提供相同的单次请求审批，例如：
`rill ask --approve-tool run_terminal_command "show the current directory"`。
