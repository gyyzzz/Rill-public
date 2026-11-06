import logging
import uuid
import time
from collections import deque

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from rill.api.routes import router
from rill.config import get_settings, setup_logging
from rill.services.flow_control import AgentFlowController
from rill.llm.capabilities import CapabilityRegistry
from rill.llm.router import sanitize_base_url

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    for directory in (
        settings.terminal.workdir,
        settings.filesystem.workdir,
        settings.chart.workdir,
        settings.upload.workdir,
        settings.logging.file.parent if settings.logging.file else None,
    ):
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    # 初始化日志系统
    setup_logging(settings)

    logger.info(f"启动 {settings.server.app_name} 服务")
    logger.info("Configuration: %s", settings.config_file)
    logger.info("Model: %s, API: %s", settings.llm.model, sanitize_base_url(settings.llm.api_base_url))
    logger.info(
        f"Terminal: {settings.terminal.enabled}, Database: {settings.database.enabled}, "
        f"FileSystem: {settings.filesystem.enabled}"
    )

    app = FastAPI(title=settings.server.app_name, version="0.2.0")

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": f"http_{exc.status_code}", "message": str(exc.detail)}
        if "code" not in detail:
            detail = {"code": f"http_{exc.status_code}", **detail}
        return JSONResponse(status_code=exc.status_code, content={"error": detail}, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {key: error[key] for key in ("loc", "type", "msg") if key in error}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder({"error": {"code": "validation_error", "message": "Request validation failed.", "details": details}}),
            headers={"x-request-id": getattr(request.state, "request_id", "")},
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception("Unhandled request error request_id=%s", request_id, exc_info=exc)
        app.state.recent_errors.append({
            "request_id": request_id,
            "error": type(exc).__name__,
            "timestamp": time.time(),
        })
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "An internal error occurred."}},
            headers={"x-request-id": request_id or ""},
        )
    if settings.cors.enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors.allow_origins,
            allow_credentials=settings.cors.allow_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def request_context(request, call_next):
        request_id = request.headers.get("x-request-id") or f"req-{uuid.uuid4().hex}"
        if not request_id.isascii() or len(request_id) > 128 or any(not (character.isalnum() or character in "-_.:") for character in request_id):
            request_id = f"req-{uuid.uuid4().hex}"
        request.state.request_id = request_id
        started = time.monotonic()
        response = await call_next(request)
        app.state.request_count += 1
        response.headers["x-request-id"] = request_id
        response.headers["x-response-time-ms"] = f"{(time.monotonic() - started) * 1000:.1f}"
        return response

    app.state.agent_flow_controller = AgentFlowController(
        settings.agent.max_concurrent_requests,
        settings.agent.queue_timeout_seconds,
    )
    app.state.recent_errors = deque(maxlen=20)
    app.state.recent_agent_events = deque(maxlen=100)
    app.state.request_count = 0
    app.state.capability_registry = CapabilityRegistry(settings)

    def record_agent_event(event) -> None:
        payload = event.payload
        safe_payload = {
            key: payload[key]
            for key in ("request_id", "provider", "model", "execution_mode", "iteration", "name", "id", "code", "elapsed_ms", "stop_reason")
            if key in payload
        }
        app.state.recent_agent_events.append({"type": event.type, "timestamp": event.timestamp, "payload": safe_payload})

    app.state.record_agent_event = record_agent_event
    app.include_router(router)

    logger.info("FastAPI 应用创建完成")
    return app
