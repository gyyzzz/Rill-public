import json
import logging
import mimetypes
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from rill.api.playground import render_playground
from rill.config import Settings, get_settings
from rill.schemas import AgentRequest, AgentResponse, ChatCompletionRequest
from rill.schemas import ChatCompletionResponse, UploadResponse
from rill.schemas import CapabilityProbeRequest, ModelDescriptor, ModelListResponse, ToolDescriptor
from rill.llm.router import ModelRouter
from rill.llm.errors import ProviderRequestError
from rill.llm.router import ProviderRegistry
from rill.services.agent import AgentService
from rill.services.flow_control import AgentCapacityError, AgentFlowController
from rill.tools.registry import build_registry

router = APIRouter()
logger = logging.getLogger(__name__)


def require_api_key(permission: str = "agent"):
    async def check(request: Request, settings: Settings = Depends(get_settings)) -> None:
        if not settings.auth.enabled:
            return
        remote = request.client.host if request.client else ""
        token = request.headers.get("x-api-key")
        authorization = request.headers.get("authorization", "")
        if not token and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if settings.auth.allow_local_without_key and remote in {"127.0.0.1", "::1", "localhost"} and not token:
            return
        scoped_keys = {
            "agent": settings.auth.agent_api_keys,
            "tool": settings.auth.tool_api_keys,
            "diagnostic": settings.auth.diagnostic_api_keys,
        }.get(permission, [])
        accepted = set(settings.auth.api_keys) | set(scoped_keys)
        if not token or token not in accepted:
            raise HTTPException(status_code=401, detail={"code": "authentication_required", "message": f"A valid {permission} API key is required."}, headers={"WWW-Authenticate": "Bearer"})
    return check


def get_flow_controller(request: Request) -> AgentFlowController:
    return request.app.state.agent_flow_controller


def get_agent_service(
    request: Request,
    settings: Settings = Depends(get_settings),
    flow_controller: AgentFlowController = Depends(get_flow_controller),
) -> AgentService:
    return AgentService(
        settings=settings,
        registry=build_registry(settings),
        flow_controller=flow_controller,
        capability_registry=request.app.state.capability_registry,
        event_sink=request.app.state.record_agent_event,
    )


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, str | bool]:
    return {
        "name": settings.server.app_name,
        "status": "ok",
        "terminal_enabled": settings.terminal.enabled,
        "database_enabled": settings.database.enabled,
    }


@router.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready(settings: Settings = Depends(get_settings)) -> dict[str, str | bool]:
    issues: list[str] = []
    if not settings.llm.model:
        issues.append("llm.model is required")
    if settings.terminal.enabled and not settings.terminal.workdir.is_dir():
        issues.append("terminal.workdir is unavailable")
    if settings.filesystem.enabled:
        workspace = settings.filesystem.workdir or settings.terminal.workdir
        if not workspace.is_dir():
            issues.append("filesystem.workdir is unavailable")
    if issues:
        raise HTTPException(status_code=503, detail={"code": "not_ready", "message": "Required settings are missing or unavailable.", "issues": issues})
    return {"status": "ok", "ready": True, "provider": settings.llm.provider, "model": settings.llm.model}


@router.get("/v1/tools", response_model=list[ToolDescriptor])
async def list_tools(settings: Settings = Depends(get_settings), _: None = Depends(require_api_key("diagnostic"))) -> list[ToolDescriptor]:
    registry = build_registry(settings)
    return registry.describe()


@router.get("/v1/models", response_model=ModelListResponse)
async def list_models(request: Request, settings: Settings = Depends(get_settings), _: None = Depends(require_api_key("diagnostic"))) -> ModelListResponse:
    models = ModelRouter(settings).models()
    for model in models:
        model["capabilities"] = request.app.state.capability_registry.get(model["owned_by"], model["id"])
    return ModelListResponse(data=[ModelDescriptor(**model) for model in models])


@router.get("/v1/providers")
async def list_providers(settings: Settings = Depends(get_settings), _: None = Depends(require_api_key("diagnostic"))) -> dict:
    from rill.llm.router import ProviderRegistry
    return {"object": "list", "data": ProviderRegistry(settings).profiles()}


@router.get("/v1/status")
async def service_status(
    request: Request,
    settings: Settings = Depends(get_settings),
    _: None = Depends(require_api_key("diagnostic")),
) -> dict:
    snapshot = await request.app.state.agent_flow_controller.snapshot()
    return {
        "service": settings.server.app_name,
        "status": "ok",
        "requests_seen": request.app.state.request_count,
        "agent_requests": {"active": snapshot.active, "queued": snapshot.queued, "limit": snapshot.limit},
        "recent_errors": list(request.app.state.recent_errors),
        "recent_agent_events": list(request.app.state.recent_agent_events),
    }


@router.post("/v1/providers/{provider_name}/check")
async def check_provider(
    provider_name: str,
    settings: Settings = Depends(get_settings),
    _: None = Depends(require_api_key("diagnostic")),
) -> dict:
    try:
        result = await ProviderRegistry(settings).get(provider_name).check()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "provider_not_found", "message": str(exc)}) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail={"code": "provider_unavailable", **result})
    return result


@router.post("/v1/capabilities/refresh")
async def refresh_capabilities(
    probe: CapabilityProbeRequest,
    request: Request,
    _: None = Depends(require_api_key("diagnostic")),
) -> dict:
    try:
        result = await request.app.state.capability_registry.probe(probe.provider, probe.model)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "provider_not_found", "message": str(exc)}) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail={"code": "provider_unavailable", **result})
    return {"provider": probe.provider, "model": probe.model, "capabilities": request.app.state.capability_registry.get(probe.provider, probe.model)}


@router.post("/v1/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    _: None = Depends(require_api_key("tool")),
) -> UploadResponse:
    if not settings.upload.enabled:
        raise HTTPException(status_code=403, detail="File upload is disabled.")

    # Check file size
    content = await file.read()
    max_bytes = settings.upload.max_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(content)} bytes exceeds {max_bytes} bytes.",
        )

    # Check extension
    original_name = file.filename or "unnamed"
    suffix = Path(original_name).suffix.lower()
    allowed = {ext.strip().lower() for ext in settings.upload.allowed_extensions}
    if suffix and allowed and suffix not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed: {suffix}. Allowed: {', '.join(sorted(allowed))}",
        )

    # Sanitize filename: strip directory components to prevent path traversal
    safe_name = Path(original_name).name
    if not safe_name or safe_name.startswith("."):
        safe_name = f"upload_{int(__import__('time').time())}{suffix or ''}"

    # Resolve destination directory
    if settings.upload.workdir:
        base = settings.upload.workdir.resolve()
    else:
        workspace_root = settings.filesystem.workdir or settings.terminal.workdir
        base = (workspace_root / "uploads").resolve()

    destination = (base / safe_name).resolve()
    if destination != base and base not in destination.parents:
        raise HTTPException(status_code=400, detail=f"Path escapes upload directory: {destination}")

    base.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)

    logger.info(f"文件已上传: {destination}, 大小: {len(content)} bytes")

    return UploadResponse(
        ok=True,
        filename=safe_name,
        path=str(destination),
        size_bytes=len(content),
        content_type=file.content_type,
    )


@router.get("/v1/workspace/{filepath:path}")
async def serve_workspace_file(
    filepath: str,
    settings: Settings = Depends(get_settings),
    _: None = Depends(require_api_key("tool")),
) -> FileResponse:
    """Serve files from the workspace directory (charts, uploads, etc.)."""
    # Determine workspace root
    if settings.upload.workdir:
        base = settings.upload.workdir.resolve()
    elif settings.filesystem.workdir:
        base = settings.filesystem.workdir.resolve()
    else:
        base = (settings.terminal.workdir / "workspace").resolve()

    # Resolve requested path and ensure it's within workspace
    requested = (base / filepath).resolve()
    if requested != base and base not in requested.parents:
        raise HTTPException(status_code=403, detail="Access denied: path escapes workspace.")

    if not requested.exists() or not requested.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    content_type, _ = mimetypes.guess_type(str(requested))
    return FileResponse(str(requested), media_type=content_type or "application/octet-stream")


@router.post("/v1/agent/respond", response_model=AgentResponse)
async def respond(
    request: AgentRequest,
    http_request: Request,
    agent_service: AgentService = Depends(get_agent_service),
    _: None = Depends(require_api_key("agent")),
) -> AgentResponse:
    request.request_id = http_request.state.request_id
    logger.info(f"Agent 请求: session_id={request.session_id}, messages_count={len(request.messages)}")
    logger.debug(f"请求消息: {[m.role for m in request.messages]}")
    try:
        result = await agent_service.run(request)
        logger.info(f"Agent 完成: iterations={result.iterations}, session_id={result.session_id}")
        return result
    except AgentCapacityError as e:
        raise HTTPException(status_code=429, detail=str(e), headers={"Retry-After": "1"}) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"code": "invalid_request", "message": str(e)}) from e
    except ProviderRequestError as e:
        raise HTTPException(status_code=502, detail={"code": e.code, "message": str(e), "provider": e.provider, "upstream_status": e.upstream_status}) from e
    except Exception as e:
        logger.error(f"Agent 执行失败: {e}", exc_info=True)
        raise


@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    agent_service: AgentService = Depends(get_agent_service),
    _: None = Depends(require_api_key("agent")),
) -> ChatCompletionResponse | StreamingResponse:
    logger.info(f"Chat completions 请求: model={request.model}, stream={request.stream}, messages_count={len(request.messages)}")
    if request.stream:
        async def event_stream() -> AsyncIterator[str]:
            try:
                async for chunk in agent_service.stream_chat_completion(request):
                    yield f"data: {chunk.model_dump_json()}\n\n"
            except AgentCapacityError as e:
                yield f"data: {json.dumps({'error': {'code': 'capacity_exhausted', 'message': str(e)}}, ensure_ascii=True)}\n\n"
            except ProviderRequestError as e:
                yield f"data: {json.dumps({'error': {'code': e.code, 'message': str(e), 'provider': e.provider}}, ensure_ascii=True)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    try:
        result = await agent_service.run_chat_completion(request)
        logger.info(f"Chat completions 完成: finish_reason={result.choices[0].finish_reason}")
        return result
    except AgentCapacityError as e:
        raise HTTPException(status_code=429, detail=str(e), headers={"Retry-After": "1"}) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"code": "invalid_request", "message": str(e)}) from e
    except ProviderRequestError as e:
        raise HTTPException(status_code=502, detail={"code": e.code, "message": str(e), "provider": e.provider, "upstream_status": e.upstream_status}) from e
    except Exception as e:
        logger.error(f"Chat completions 执行失败: {e}", exc_info=True)
        raise


@router.post("/v1/agent/stream")
async def stream(
    request: AgentRequest,
    http_request: Request,
    agent_service: AgentService = Depends(get_agent_service),
    _: None = Depends(require_api_key("agent")),
) -> StreamingResponse:
    request.request_id = http_request.state.request_id
    logger.info(f"Agent 流式请求: session_id={request.session_id}")
    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in agent_service.stream(request):
                logger.debug(f"发送事件: type={event.type}")
                yield f"event: {event.type}\n"
                yield f"data: {json.dumps(event.payload, ensure_ascii=True)}\n\n"
        except AgentCapacityError as e:
            # SSE headers are already committed, so expose a machine-readable error event.
            yield "event: error\n"
            yield f"data: {json.dumps({'status_code': 429, 'detail': str(e)}, ensure_ascii=True)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/")
async def root() -> JSONResponse:
    return JSONResponse(
        {
            "service": "Rill",
            "docs_hint": "Use /health, /v1/status, /v1/tools, /v1/models, /v1/agent/respond, /v1/agent/stream, or /playground.",
        }
    )


@router.get("/playground", response_class=HTMLResponse)
async def playground() -> HTMLResponse:
    return render_playground()
