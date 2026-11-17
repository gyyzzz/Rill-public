工业级 Agent Runtime 的五层架构

入口与身份层（Gateway Layer）： 负责拦截请求，识别是哪个用户、哪个租户，做速率限制和参数校验。

编排与状态层（Orchestrator &amp; State）： 这是 Runtime 的大脑。它管理着 Session 历史、任务的状态机（Checkpoint）。它来决定什么时候任务该继续，什么时候该停下来等待人工审批，如果系统崩了，它负责从上一个检查点恢复。

能力与路由层（Capability &amp; Router）： 统一管理 Agent 能调用的所有资产（Function Tools、远程 MCP 服务、甚至其他 Agent）。同时，由 Model Router 决定这个任务是该用昂贵的推理模型，还是便宜的轻量模型。

执行与隔离层（Executor &amp; Sandbox）： 这是最体现 Runtime 资源管理的一层。 模型的自由输出是不可信的，所以 Runtime 会把具体的代码执行、工具调用，扔进一个绝对隔离的安全沙箱（Sandbox/Container）或异步队列里去跑，把副作用锁在笼子里。

治理与观测层（Governance &amp; Observability）： 负责卡死安全边界。在敏感操作前插入人工审批（Human-in-the-loop），同时全量记录 Trace 和日志，确保每一步都能“回放”和评测。