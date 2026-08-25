import json
import logging
import mimetypes
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from orionxcore.api.playground import render_playground
from orionxcore.config import Settings, get_settings
from orionxcore.schemas import AgentRequest, AgentResponse, ChatCompletionRequest
from orionxcore.schemas import ChatCompletionResponse, UploadResponse
from orionxcore.schemas import ToolDescriptor
from orionxcore.services.agent import AgentService
from orionxcore.tools.registry import build_registry

router = APIRouter()
logger = logging.getLogger(__name__)


def get_agent_service(settings: Settings = Depends(get_settings)) -> AgentService:
    return AgentService(settings=settings, registry=build_registry(settings))


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, str | bool]:
    return {
        "name": settings.app_name,
        "status": "ok",
        "terminal_enabled": settings.enable_terminal,
        "database_enabled": settings.enable_database,
    }


@router.get("/v1/tools", response_model=list[ToolDescriptor])
async def list_tools(settings: Settings = Depends(get_settings)) -> list[ToolDescriptor]:
    registry = build_registry(settings)
    return registry.describe()


@router.post("/v1/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
) -> UploadResponse:
    if not settings.enable_upload:
        raise HTTPException(status_code=403, detail="File upload is disabled.")

    # Check file size
    content = await file.read()
    max_bytes = settings.upload_max_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(content)} bytes exceeds {max_bytes} bytes.",
        )

    # Check extension
    original_name = file.filename or "unnamed"
    suffix = Path(original_name).suffix.lower()
    allowed = {ext.strip().lower() for ext in settings.upload_allowed_extensions.split(",")}
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
    if settings.upload_workdir:
        base = settings.upload_workdir.resolve()
    else:
        workspace_root = settings.filesystem_workdir or settings.terminal_workdir
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
) -> FileResponse:
    """Serve files from the workspace directory (charts, uploads, etc.)."""
    # Determine workspace root
    if settings.upload_workdir:
        base = settings.upload_workdir.resolve()
    elif settings.filesystem_workdir:
        base = settings.filesystem_workdir.resolve()
    else:
        base = (settings.terminal_workdir / "workspace").resolve()

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
    agent_service: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    logger.info(f"Agent 请求: session_id={request.session_id}, messages_count={len(request.messages)}")
    logger.debug(f"请求消息: {[m.role for m in request.messages]}")
    try:
        result = await agent_service.run(request)
        logger.info(f"Agent 完成: iterations={result.iterations}, session_id={result.session_id}")
        return result
    except Exception as e:
        logger.error(f"Agent 执行失败: {e}", exc_info=True)
        raise


@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    agent_service: AgentService = Depends(get_agent_service),
) -> ChatCompletionResponse | StreamingResponse:
    logger.info(f"Chat completions 请求: model={request.model}, stream={request.stream}, messages_count={len(request.messages)}")
    if request.stream:
        async def event_stream() -> AsyncIterator[str]:
            async for chunk in agent_service.stream_chat_completion(request):
                yield f"data: {chunk.model_dump_json()}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    try:
        result = await agent_service.run_chat_completion(request)
        logger.info(f"Chat completions 完成: finish_reason={result.choices[0].finish_reason}")
        return result
    except Exception as e:
        logger.error(f"Chat completions 执行失败: {e}", exc_info=True)
        raise


@router.post("/v1/agent/stream")
async def stream(
    request: AgentRequest,
    agent_service: AgentService = Depends(get_agent_service),
) -> StreamingResponse:
    logger.info(f"Agent 流式请求: session_id={request.session_id}")
    async def event_stream() -> AsyncIterator[str]:
        async for event in agent_service.stream(request):
            logger.debug(f"发送事件: type={event.type}")
            yield f"event: {event.type}\n"
            yield f"data: {json.dumps(event.payload, ensure_ascii=True)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/")
async def root() -> JSONResponse:
    return JSONResponse(
        {
            "service": "OrionXCore",
            "docs_hint": "Use /health, /v1/tools, /v1/agent/respond, /v1/agent/stream, or /playground.",
        }
    )


@router.get("/playground", response_class=HTMLResponse)
async def playground() -> HTMLResponse:
    return render_playground()
