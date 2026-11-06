# Rill

```text
 █▀█ █ █   █
 █▀▄ █ █   █
 ▀ ▀ ▀ ▀▀▀ ▀▀▀
```

**Less is more. Simplicity is beauty.**

Rill 是一个轻量、自托管的 Agent Gateway：你提供一个 OpenAI 兼容模型和
API Key，Rill 在服务端完成一轮或多轮模型调用、工具执行和结果整理，再通过
HTTP、SSE 或 CLI 返回结果。

```text
客户端 -> Rill -> 模型
                  |
                  +-> 工具
```

## Why Rill？

| 你的需求                   | 推荐                                | 原因                     |
| ---------------------- | --------------------------------- | ---------------------- |
| 终端里的成熟 AI coding agent | **Claude Code 或 Codex**           | 客户端体验和编程工作流更完整         |
| IDE 编程助手               | **GitHub Copilot 或 IDE 原生 Agent** | 编辑器集成更深                |
| 自托管 Agent API          | **Rill**                          | 一个小服务，接入任意 OpenAI 兼容模型 |
| 服务端工具循环                | **Rill**                          | HTTP/SSE 接口简单，工具在服务端执行 |
| 按需扩展工具                 | **Rill**                          | 小型可靠底座，配合 Skill 开发扩展   |
| 小团队内部运维自动化             | **Rill**                          | 配置少、部署简单、权限可控          |

Rill 适合放在你自己的客户端、模型和工具后面。

## 架构

```text
CLI / Web UI / IDE 插件 / 脚本
                |
           REST / SSE
                v
       +-------------------+
       |       Rill        |
       | API + Agent 循环  |
       | Provider 适配层   |
       | Tool Registry     |
       +---------+---------+
                 |
       +---------+----------+
       |                    |
  OpenAI 兼容模型接口     工具
                       终端 / 文件
                       ClickHouse
                       图表（可选）
```

`/v1/chat/completions` 只执行一轮兼容模型调用；`/v1/agent/*` 执行服务端
Agent 循环，直到模型给出结果或达到限制。工具由配置启用，并经过权限、沙盒、
超时、结果大小和审批策略检查。完整边界见[架构文档](docs/ARCHITECTURE.md)。

## 快速开始

### 安装 uv

没有 uv 时先安装：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 本地运行

在仓库根目录执行：

```bash
uv sync
cp config.toml.example config.toml
cp .env.example .env
```

编辑 `config.toml`，设置模型和模型服务地址：

```toml
[llm]
model = "deepseek-v4-flash"
api_base_url = "https://api.deepseek.com"
```

编辑 `.env`，填入 API Key：

```bash
ORIONXCORE_LLM__API_KEY=replace-me
```

检查并启动服务：

```bash
uv run rill config check
uv run rill serve
```

`rill serve` 读取 `[server].host` 和 `[server].port`，默认监听
`0.0.0.0:8080`。另开终端验证：

```bash
curl http://127.0.0.1:8080/health/ready
uv run rill ask "列出当前工作区中的文件。"
uv run rill chat
```

也可以用 `uv run uvicorn rill.main:app --host 0.0.0.0 --port 8080` 启动；
这时监听地址由 Uvicorn 参数决定。

### Docker

```bash
cp config.toml.example config.toml
cp .env.example .env
```

编辑 `config.toml` 中的 `[llm]`，并在 `.env` 中填入：

```bash
ORIONXCORE_LLM__API_KEY=replace-me
```

启动并检查：

```bash
docker compose -f deploy/docker-compose.yml up -d --build
curl http://127.0.0.1:8080/health/ready
```

停止服务：

```bash
docker compose -f deploy/docker-compose.yml down
```

## 工具

默认启用终端、文件系统和数据库工具；图表工具需要显式开启：生产环境请评估保留最小化工具开启。

```bash
uv run rill tool list
uv run rill tool enable tool_name
```

终端和文件系统的写操作需要请求级批准：

```bash
uv run rill ask \
  --approve-tool run_terminal_command \
  --approve-tool filesystem \
  "列出当前工作区中的文件"
```

HTTP 客户端在 Agent 请求中传入相同的 `approved_tools` 数组。OpenAI 兼容的
`/v1/chat/completions` 由客户端负责工具调用；需要服务端自动循环时使用
`/v1/agent/respond` 或 `/v1/agent/stream`。添加自定义工具的完整流程见
[工具开发文档](docs/TOOLS.md)和[开发 Skill](skills/tool-development/SKILL.md)。

## API

| 入口 | 用途 |
| --- | --- |
| `GET /health/live` | 进程存活检查 |
| `GET /health/ready` | 配置和工作区就绪检查 |
| `GET /v1/models` | 查看配置的模型 |
| `GET /v1/tools` | 查看已启用工具及审批元数据 |
| `POST /v1/agent/respond` | 服务端执行 Agent 循环并返回完整结果 |
| `POST /v1/agent/stream` | 以 SSE 流式返回 Agent 事件 |
| `POST /v1/chat/completions` | OpenAI 兼容的一轮调用 |

最小 Agent 请求：

```bash
curl -sS -X POST http://127.0.0.1:8080/v1/agent/respond \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"列出工作区中的文件。"}]}'
```

## 文档

- [配置](docs/CONFIGURATION.md)
- [安装](docs/INSTALL.md)
- [部署](docs/DEPLOYMENT.md)
- [架构](docs/ARCHITECTURE.md)
- [工具开发](docs/TOOLS.md)
- [API 接口](docs/API.md)
- [依赖](docs/dependencies.md)
- [产品计划](docs/PRODUCT_PLAN.md)

English: [README.md](README.md).
