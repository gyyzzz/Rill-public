# OrionXCore 产品演进计划

> 状态：讨论稿
>
> 本文记录当前已经确认的产品方向、架构边界和实施任务。实现任务应按本文的阶段顺序拆分，新的功能需要先确认是否符合轻量 Agent Gateway 定位。

## 1. 产品定位

OrionXCore 定位为轻量、自托管的 Agent Gateway / Agent Runtime，为客户端统一提供模型调用、工具执行和服务端 Agent 循环能力。

核心价值：

- 统一接入多个模型供应商和模型
- 在服务端完成 Agent 多轮循环和工具执行
- 兼容 OpenAI 客户端生态
- 提供原生 Tool Call 和非 Tool Call 模型的 fallback
- 对工具执行提供权限、沙盒、确认和审计控制
- 保持单体部署、低依赖和易于扩展

当前范围不包含 MCP。后续如果外部工具生态成为明确需求，再单独评估 MCP Client 和 MCP Server。

## 2. 已确认的产品决策

### API

- 保留 `/v1/chat/completions`，作为 OpenAI 兼容层。
- `/v1/agent/respond` 和 `/v1/agent/stream` 是主产品 API。
- OpenAI 兼容层保持标准的一轮对话语义。
- Agent API 负责服务端 Agent loop、工具调用和执行事件。

### 配置和运维

采用以下分工：

```text
配置文件负责声明
CLI 负责操作
TUI 负责观察
```

- TOML、环境变量和 Docker Secret 用于声明式部署。
- CLI 用于初始化、校验、配置 provider、测试模型和管理工具。
- TUI 用于查看 provider、模型、工具、请求状态、错误和日志。
- 不建设 Web 配置中心。
- 现有 Playground 保留为调试页面，不承担管理职责。

### 工具开发

工具由用户自行开发。项目通过 Skill 约定工具开发方式和检查流程，用户可以在 Claude Code 中调用该 Skill 完成工具开发。

Skill 是开发规范和协作流程，不是运行时的工具加载机制。运行时工具仍然遵守 `Tool`、`ToolRegistry` 和配置系统的约定。

## 3. 目标架构

```text
API Layer
├── Compatibility API: /v1/chat/completions
├── Agent API: /v1/agent/respond, /v1/agent/stream
└── Admin-free local operations: CLI / optional TUI

Agent Runtime
├── Agent loop
├── Model capability selection
├── Native Tool Call
├── JSON / Prompt fallback
├── Tool policy
└── Execution events

Model Layer
├── ProviderRegistry
├── ModelRouter
├── ProviderAdapter
└── CapabilityRegistry

Tool Layer
├── Built-in tools
├── Local extension tools
├── ToolRegistry
└── Tool policy and sandbox
```

## 4. 完整任务列表

### P0：架构和协议基础

- [ ] 定义 API、Agent Runtime、Model Provider、Tool Runtime 的模块边界。
- [ ] 将 `OpenAICompatibleClient` 抽象为 `ProviderAdapter`。
- [ ] 定义统一的内部消息、工具、工具调用、工具结果和事件模型。
- [ ] 明确 `/v1/chat/completions` 与 `/v1/agent/*` 的行为差异。
- [ ] 统一错误格式、请求 ID 和响应状态。
- [ ] 明确配置优先级：默认值、TOML、环境变量、Docker Secret。
- [ ] 增加配置版本和旧配置迁移机制。
- [ ] 修正文档中的默认端口、启动命令和失效路径。

### P1：多 Provider 和模型能力

- [ ] 将单一 `[llm]` 配置迁移为多个 provider profile。
- [ ] 保留旧 `[llm]` 配置的兼容读取和迁移提示。
- [ ] 支持 provider 类型、Base URL、API Key、模型列表和默认模型。
- [ ] 支持模型别名，例如 `fast`、`strong`、`local`。
- [ ] 支持请求级模型选择。
- [ ] 支持 provider 连接测试和模型列表检查。
- [ ] 增加超时、重试、退避和错误分类。
- [ ] 增加模型能力档案：Tool Call、JSON、流式、并行调用、reasoning、上下文长度。
- [ ] 支持自动探测和手动覆盖模型能力。
- [ ] 缓存能力检测结果，并提供刷新方式。
- [ ] 设计 provider 不可用时的明确错误和可选备用模型。

建议的模型工具模式：

```text
native_tool_call
json_fallback
no_tools
```

### P1：Agent Runtime 和 fallback

- [ ] 保留并整理现有服务端 Agent loop。
- [ ] 统一不同供应商的 Tool Call 响应解析。
- [ ] 支持工具参数 JSON 校验和未知工具处理。
- [ ] 支持单轮多个工具调用。
- [ ] 增加最大迭代次数、工具调用次数和结果大小限制。
- [ ] 为模型调用、工具调用和工具结果建立关联 ID。
- [ ] 设计 JSON 或 XML fallback 输出协议。
- [ ] 将工具定义注入 fallback Prompt。
- [ ] 实现 fallback 文本解析器。
- [ ] 支持代码块 JSON、前后解释文字和常见格式错误。
- [ ] 解析失败时有限次数重试。
- [ ] 解析失败时返回明确的 `fallback_parse_error` 事件。
- [ ] fallback 支持多轮工具执行。
- [ ] fallback 默认只开放只读工具。
- [ ] 写文件、删除文件、终端等高风险工具需要显式启用。
- [ ] `/v1/chat/completions` 保持标准客户端驱动语义。

### P1：主产品 API

- [ ] 完善 `/v1/agent/respond` 的执行结果和事件结构。
- [ ] 完善 `/v1/agent/stream` 的 SSE 事件定义。
- [ ] 增加 `request_started`、`model_started`、`model_completed`、`tool_call`、`tool_result`、`iteration`、`final`、`error` 事件。
- [ ] 响应中暴露 provider、model、执行模式和迭代次数。
- [ ] 支持请求指定可用工具。
- [ ] 支持 Agent profile 或运行模式选择。
- [ ] 达到上限时返回明确的停止原因。
- [ ] 增加 `/v1/models`，供兼容客户端发现可用模型。
- [ ] 逐步修正 `/v1/chat/completions` 的真实流式行为。

### P1：认证和工具策略

- [ ] 增加运行时 API Key。
- [ ] 增加管理类 CLI 操作的本地权限控制。
- [ ] 区分 Agent 调用权限、工具执行权限和诊断权限。
- [ ] 支持本地开发环境显式关闭认证。
- [ ] 统一认证失败响应。
- [ ] 增加 CORS 配置。
- [ ] 为每个工具增加 `read_only`、`risk_level`、`requires_approval`、`timeout` 和 `max_result_bytes` 元数据。
- [ ] 实现全局工具白名单和黑名单。
- [ ] 支持按 Agent profile 限制工具。
- [ ] 支持按请求限制工具。
- [ ] 实现只读工具模式。
- [ ] 实现写操作和高风险操作确认机制。
- [ ] 保留终端和文件系统的路径沙盒。
- [ ] 增加工具执行超时、取消和结果截断。
- [ ] 记录工具执行审计信息。

### P2：CLI 配置和运维

建议增加以下命令：

```text
orionx init
orionx config check
orionx config show
orionx config set <path> <value>
orionx provider list
orionx provider add
orionx provider test <name>
orionx provider use <name>
orionx model list
orionx tool list
orionx tool enable <name>
orionx tool disable <name>
orionx doctor
orionx status
orionx tui
```

CLI 要求：

- [ ] 配置初始化向导。
- [ ] 配置检查和清晰的错误提示。
- [ ] API Key 隐藏输入，不回显、不进入普通日志。
- [ ] 配置展示自动脱敏。
- [ ] 配置文件原子写入。
- [ ] 配置备份和恢复。
- [ ] 支持非交互模式，适合 CI 和 Docker。
- [ ] provider 连接测试。
- [ ] 工具启用、禁用和策略查看。

### P2：TUI 观察工具

TUI 作为可选依赖和本地观察工具，不复制服务端业务逻辑。

- [ ] Provider 状态和模型列表。
- [ ] 模型连接测试。
- [ ] 工具列表和权限状态。
- [ ] Agent 请求状态和最近错误。
- [ ] Agent 事件和工具执行轨迹。
- [ ] 日志查看和过滤。
- [ ] 基础系统状态。
- [ ] TUI 通过 HTTP 调用状态接口或 CLI 诊断命令。
- [ ] TUI 依赖作为可选安装项，基础安装保持轻量。

### P2：部署和运行质量

- [ ] 统一默认端口和 CLI 默认服务地址。
- [ ] `docker compose up -d` 一键启动。
- [ ] 启动时检查 API Key、模型、工作目录和工具配置。
- [ ] 增加 `/health/live` 和 `/health/ready`。
- [ ] 完善 Docker healthcheck。
- [ ] 自动创建必要目录。
- [ ] 增加启动失败诊断。
- [ ] 增加请求 ID、provider、model、耗时和状态日志。
- [ ] 记录模型调用、工具调用和 fallback 解析失败统计。
- [ ] 敏感数据脱敏。
- [ ] 后续再考虑 Prometheus 指标。

### P3：工具开发 Skill

新增项目级 Skill，例如：

```text
skills/tool-development/SKILL.md
```

Skill 的目标是让 Claude Code 按统一流程开发工具。建议流程：

1. 阅读工具需求，确认工具边界、输入输出和风险等级。
2. 检查现有 `Tool` 基类、`ToolRegistry`、配置模型和相似工具。
3. 创建工具实现、JSON Schema、配置项和必要的辅助模块。
4. 添加 `read_only`、风险、超时、结果大小等策略元数据。
5. 实现参数校验、错误返回、超时和权限检查。
6. 将工具注册到 registry，并加入配置开关。
7. 编写有意义的单元测试和最小集成测试。
8. 更新工具列表、配置示例和使用文档。
9. 运行格式检查、测试和工具发现检查。
10. 输出变更摘要、配置变更和部署注意事项。

工具开发规范应包含：

- [ ] 工具目录和命名规则。
- [ ] `Tool` 接口要求。
- [ ] JSON Schema 要求。
- [ ] 错误返回格式。
- [ ] 权限和风险元数据。
- [ ] 配置项和环境变量命名。
- [ ] 测试要求。
- [ ] 文档要求。
- [ ] 注册和启用流程。
- [ ] 禁止工具绕过沙盒和策略执行。

Skill 只负责开发流程约束。工具是否进入运行时，仍由代码注册和配置开关决定。

### P3：测试和文档

- [ ] Provider adapter 单元测试。
- [ ] 多 provider 配置和迁移测试。
- [ ] 原生 Tool Call 测试。
- [ ] fallback 正常、异常和边界测试。
- [ ] 工具策略和认证测试。
- [ ] Agent API 契约测试。
- [ ] OpenAI 兼容接口测试。
- [ ] Docker 启动测试。
- [ ] 配置检查和 CLI 命令测试。
- [ ] 更新架构文档。
- [ ] 更新配置参考。
- [ ] 更新 API 文档。
- [ ] 增加部署和升级说明。
- [ ] 增加新增工具开发指南。

## 5. 版本建议

### v0.3：核心 Agent Gateway

- 多 provider 配置
- 模型能力档案
- 原生 Tool Call
- JSON fallback
- `/v1/agent/*` 主 API
- 保留 `/v1/chat/completions`
- 基础认证
- 工具策略
- CLI 配置检查
- Docker 部署整理

### v0.4：可用的本地管理体验

- CLI provider 管理
- provider 连接测试
- 工具管理命令
- `doctor` 和 `status`
- 可选 TUI
- 审计日志和运行统计
- 可选 SQLite 会话存储

### v0.5：工具开发生态

- 工具开发 Skill
- 工具脚手架和校验
- 工具版本和依赖说明
- Skill 使用文档
- 更完整的模型路由和备用模型

MCP 不属于以上版本的必需内容。只有在确认需要接入外部 MCP 工具生态后，再单独制定 MCP 适配方案。

## 6. 推荐默认原则

- 默认配置保持简单，复杂能力使用显式开关。
- 默认不暴露敏感信息。
- 默认不允许高风险工具无确认执行。
- 默认 Agent API 负责服务端循环。
- 默认 OpenAI 兼容接口保持标准行为。
- 默认工具由代码和配置共同启用。
- 默认工具开发由用户和 Claude Code Skill 协作完成。
- 默认使用单体部署，只有出现明确需求后再引入外部数据库或分布式组件。
