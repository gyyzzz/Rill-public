import logging

from fastapi import FastAPI

from orionxcore.api.routes import router
from orionxcore.config import get_settings, setup_logging
from orionxcore.services.flow_control import AgentFlowController

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    # 初始化日志系统
    setup_logging(settings)

    logger.info(f"启动 {settings.app_name} 服务")
    logger.info(f"模型: {settings.model}, API: {settings.api_base_url}")
    logger.info(f"Terminal: {settings.enable_terminal}, Database: {settings.enable_database}, FileSystem: {settings.enable_filesystem}")

    app = FastAPI(title=settings.app_name, version="0.2.0")
    app.state.agent_flow_controller = AgentFlowController(
        settings.agent_max_concurrent_requests,
        settings.agent_queue_timeout_seconds,
    )
    app.include_router(router)

    logger.info("FastAPI 应用创建完成")
    return app
