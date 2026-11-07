绘制一张 16:9 横版、中文标注的软件架构图，标题为

“Rill：自托管 Agent Gateway 架构”。采用清晰、克制的技术文档风格。

按“左侧客户端 → 中央 Rill 服务 → 右侧模型与业务资源”布局。

用边框明确标出 Rill 服务的进程边界，并用不同颜色区分

“模型调用路径”和“工具执行路径”。文字必须清晰可读。

左侧【客户端】：

1. 内置字符对话客户端“rill chat”，连接 Agent API。

2. “用户自建的 Web UI / IDE 插件 / 脚本”，可连接 Agent API

   或 OpenAI 兼容 API。标注“外部客户端，项目不提供完整图形化客户端”。

3. 可在角落标注“/playground：API 调试页面”，不要将其画成产品客户端。

中央【Rill 服务】内分四层：

1. API 接入层：

   - Agent API：POST /v1/agent/respond（JSON 结果及事件）；

     POST /v1/agent/stream（SSE 事件）。

   - OpenAI 兼容 API：POST /v1/chat/completions（单轮模型调用）。

   - 辅助接口：模型和工具发现、状态、健康检查。

   - 可选的分权限 API Key 认证。

2. Agent 运行层：

   - AgentService：管理多轮模型调用、工具调用、事件和执行上限。

   - AgentFlowController：限制并发与排队时间。

   - 模型能力档案决定原生 Tool Call、JSON fallback 或 no\_tools 模式。

3. 模型层：

   - ModelRouter 根据配置选择 Provider 和模型。

   - ProviderAdapter 负责调用模型；当前实现使用 OpenAI 兼容协议。

   - 支持多个 Provider 配置；可标注“按配置选择备用 Provider”。

4. 工具层：

   - ToolRegistry 检查工具启用状态、允许/拒绝策略、只读模式、

     请求级审批、参数、超时和结果大小。

   - 工具代码位于 Rill 服务边界内，随服务部署和执行。

   - 分成“项目内置工具：终端、文件系统、数据库查询、可选图表”

     和“项目自定义工具：实现 Tool 接口、在 build\_registry 注册、

     通过配置启用”。

右侧【服务外部依赖】分成上下两组：

- 上方“一个或多个 OpenAI 兼容模型服务”，连接 ModelRouter。

- 下方“工具访问的资源：工作区、ClickHouse、企业内网 API/系统”，

  连接相应工具。ClickHouse 是数据库工具访问的资源，

  不要把 ClickHouse 本身画成 Rill 内部工具。

绘制两条明确的请求流程：

A. Agent API：客户端 → Agent API → AgentService → ModelRouter → 模型；

   模型返回工具调用意图 → AgentService → ToolRegistry → 项目工具

   → 业务资源；工具结果经 ToolRegistry 回到 AgentService，

   再进入下一轮模型调用；最终以 JSON 或 SSE 事件返回客户端。

B. 兼容 API：外部客户端 → /v1/chat/completions → ModelRouter → 模型

   → 单轮结果返回客户端。若结果包含 tool_calls，将其交还客户端；

   这条路径不经过服务端 ToolRegistry，也不执行服务端工具循环。

在 Rill 服务下方放一个【配置与运维】支撑区：

config.toml、.env、进程环境变量和密钥文件向各层提供设置；

配置 CLI 管理本地配置，状态 TUI 观察服务状态。

标注“会话历史由客户端维护；Rill 不自动持久化对话历史”。