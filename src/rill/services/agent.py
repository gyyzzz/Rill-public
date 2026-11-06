import json
import logging
import time
import uuid
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, date
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from rill.config import Settings
from rill.llm.openai_compat import OpenAICompatibleClient
from rill.services.flow_control import AgentCapacityError, AgentFlowController
from rill.schemas import AgentEvent, AgentRequest, AgentResponse
from rill.schemas import ChatCompletionChunk, ChatCompletionChunkChoice, ChatCompletionDelta
from rill.schemas import ChatCompletionMessage, ChatCompletionRequest, ChatCompletionResponse
from rill.schemas import ChatCompletionChoice, ChatCompletionUsage, ChatMessage
from rill.tools.registry import ToolRegistry
from rill.llm.router import ModelRouter
from rill.llm.errors import ProviderRequestError
from rill.llm.capabilities import CapabilityRegistry
from rill.services.fallback import FallbackParseError, fallback_instruction, parse_fallback_response

logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles datetime and date objects."""
    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        return super().default(obj)


def safe_json_dumps(obj: Any, **kwargs: Any) -> str:
    """JSON dumps that handles datetime objects."""
    return json.dumps(obj, cls=DateTimeEncoder, **kwargs)


@dataclass
class ChatCompletionTurnResult:
    content: str
    tool_calls: list[dict[str, Any]]
    finish_reason: str
    model: str
    reasoning_content: str | None = None


class AgentService:
    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry,
        flow_controller: AgentFlowController | None = None,
        capability_registry: CapabilityRegistry | None = None,
        event_sink: Callable[[AgentEvent], None] | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._client = OpenAICompatibleClient(settings)
        self._flow_controller = flow_controller
        self._capability_registry = capability_registry or CapabilityRegistry(settings)
        self._event_sink = event_sink

    async def run(self, request: AgentRequest) -> AgentResponse:
        started_at = time.monotonic()
        request_id = request.request_id or f"req-{uuid.uuid4().hex}"
        request.request_id = request_id
        logger.info("Agent request started request_id=%s messages=%s", request_id, len(request.messages))
        events: list[AgentEvent] = []
        emit = lambda event: self._stamp_event(event, request_id, events)
        async with self._execution_slot():
            final_message, iterations = await self._run_loop(request, emit, request_id=request_id)
        stop_reason = self._stop_reason(events)
        provider, model, _ = ModelRouter(self._settings).resolve(request.model, request.provider)
        provider = next((event.payload.get("provider") for event in reversed(events) if event.type == "model_completed"), provider)
        model = next((event.payload.get("model") for event in reversed(events) if event.type == "model_completed"), model)
        execution_mode = next((event.payload.get("execution_mode") for event in reversed(events) if event.type == "model_completed"), None)
        emit(AgentEvent(type="final", payload={"message": final_message.model_dump(), "iterations": iterations, "provider": provider, "model": model, "execution_mode": execution_mode, "stop_reason": stop_reason}))
        logger.info("Agent request completed request_id=%s iterations=%s elapsed_ms=%.1f", request_id, iterations, (time.monotonic() - started_at) * 1000)
        return AgentResponse(
            message=final_message,
            events=events,
            iterations=iterations,
            session_id=request.session_id,
            request_id=request_id,
            provider=provider,
            model=model,
            execution_mode=execution_mode or request.execution_mode or self._settings.agent.execution_mode,
            stop_reason=stop_reason,
        )

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        events: list[AgentEvent] = []
        request_id = request.request_id or f"req-{uuid.uuid4().hex}"
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

        def emit(event: AgentEvent) -> None:
            self._stamp_event(event, request_id, events)
            queue.put_nowait(event)

        async def produce() -> None:
            try:
                async with self._execution_slot():
                    final_message, iterations = await self._run_loop(request, emit, request_id=request_id)
                stop_reason = self._stop_reason(events)
                provider, model, _ = ModelRouter(self._settings).resolve(request.model, request.provider)
                provider = next((event.payload.get("provider") for event in reversed(events) if event.type == "model_completed"), provider)
                model = next((event.payload.get("model") for event in reversed(events) if event.type == "model_completed"), model)
                mode = next((event.payload.get("execution_mode") for event in reversed(events) if event.type == "model_completed"), request.execution_mode)
                emit(AgentEvent(
                    type="final",
                    payload={"message": final_message.model_dump(), "iterations": iterations, "session_id": request.session_id, "provider": provider, "model": model, "execution_mode": mode, "stop_reason": stop_reason},
                ))
            except AgentCapacityError as exc:
                emit(AgentEvent(type="error", payload={"code": "capacity_exhausted", "status_code": 429, "message": str(exc)}))
            except ProviderRequestError as exc:
                emit(AgentEvent(type="error", payload={"code": exc.code, "provider": exc.provider, "message": str(exc)}))
            except Exception as exc:
                logger.exception("Agent stream failed request_id=%s", request_id)
                emit(AgentEvent(type="error", payload={"code": "agent_error", "message": "Agent request failed."}))
            finally:
                queue.put_nowait(None)

        producer = asyncio.create_task(produce())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
            await producer
        finally:
            if not producer.done():
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)

    async def run_chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        async with self._execution_slot():
            turn = await self._run_chat_completion_turn(request)
        created = int(time.time())
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        usage = self._estimate_usage(request.messages, turn.content)
        return ChatCompletionResponse(
            id=completion_id,
            object="chat.completion",
            created=created,
            model=turn.model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessage(
                        role="assistant",
                        content=turn.content,
                        tool_calls=turn.tool_calls or None,
                        reasoning_content=turn.reasoning_content,
                    ),
                    finish_reason=turn.finish_reason,
                )
            ],
            usage=usage,
        )

    async def stream_chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[ChatCompletionChunk]:
        messages = self._build_messages(AgentRequest(messages=request.messages, metadata=request.metadata, provider=request.provider), include_system_prompt=False)
        registry = self._registry.filtered(request.tools)
        response_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        provider_name, model_name, client = ModelRouter(self._settings).resolve(request.model, request.provider)
        if not request.model and not request.provider:
            client = self._client
        finish_reason = "stop"
        finish_sent = False
        async with self._execution_slot():
            yield ChatCompletionChunk(
                id=response_id,
                object="chat.completion.chunk",
                created=created,
                model=model_name,
                choices=[ChatCompletionChunkChoice(index=0, delta=ChatCompletionDelta(role="assistant"), finish_reason=None)],
            )
            async for raw_chunk in client.stream_complete(
                messages=messages,
                tools=registry.as_openai_tools(),
                tool_choice=request.tool_choice,
                parallel_tool_calls=request.parallel_tool_calls,
                model=model_name,
                temperature=request.temperature,
                top_p=request.top_p,
                max_tokens=request.max_tokens,
                stop=request.stop,
            ):
                choice = (raw_chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                    finish_sent = True
                tool_calls = delta.get("tool_calls") or []
                yield ChatCompletionChunk(
                    id=response_id,
                    object="chat.completion.chunk",
                    created=created,
                    model=model_name,
                    choices=[ChatCompletionChunkChoice(index=0, delta=ChatCompletionDelta(
                        role=delta.get("role"), content=delta.get("content"), tool_calls=tool_calls or None
                    ), finish_reason=choice.get("finish_reason"))],
                )
        if not finish_sent:
            yield ChatCompletionChunk(
                id=response_id,
                object="chat.completion.chunk",
                created=created,
                model=model_name,
                choices=[ChatCompletionChunkChoice(index=0, delta=ChatCompletionDelta(), finish_reason=finish_reason)],
            )

    @asynccontextmanager
    async def _execution_slot(self) -> AsyncIterator[None]:
        if self._flow_controller is None:
            yield
            return
        async with self._flow_controller.slot():
            yield

    async def _run_chat_completion_turn(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionTurnResult:
        messages = self._build_messages(
            AgentRequest(messages=request.messages, metadata=request.metadata, provider=request.provider),
            include_system_prompt=False,
        )
        registry = self._registry.filtered(request.tools)
        tools = registry.as_openai_tools()
        provider_name, actual_model, client = ModelRouter(self._settings).resolve(request.model, request.provider)
        if not request.model and not request.provider:
            client = self._client
        llm_response = await client.complete(
            messages=messages,
            tools=tools,
            tool_choice=request.tool_choice,
            parallel_tool_calls=request.parallel_tool_calls,
            model=actual_model,
            temperature=request.temperature,
            top_p=request.top_p,
            max_tokens=request.max_tokens,
            stop=request.stop,
        )
        allowed_tool_names = {tool["function"]["name"] for tool in tools}
        valid_tool_calls = [
            call for call in llm_response.tool_calls if call.name in allowed_tool_names
        ]
        if valid_tool_calls:
            return ChatCompletionTurnResult(
                content=llm_response.content,
                tool_calls=[
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=True),
                        },
                    }
                    for call in valid_tool_calls
                ],
                finish_reason="tool_calls",
                model=actual_model,
                reasoning_content=llm_response.raw_message.get("reasoning_content"),
            )
        return ChatCompletionTurnResult(
            content=llm_response.content,
            tool_calls=[],
            finish_reason="stop",
            model=actual_model,
            reasoning_content=llm_response.raw_message.get("reasoning_content"),
        )

    async def _run_loop(
        self,
        request: AgentRequest,
        emit: Callable[[AgentEvent], None],
        requested_tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        parallel_tool_calls: bool | None = None,
        model: str | None = None,
        request_id: str | None = None,
    ) -> tuple[ChatMessage, int]:
        messages = self._build_messages(request)
        registry = self._registry.filtered(requested_tools if requested_tools is not None else request.tools)

        profile_settings = self._settings.agent.profiles.get(request.profile) if request.profile else None
        if request.profile and profile_settings is None:
            raise ValueError(f"Unknown Agent profile: {request.profile}")
        selected_model = model or request.model or (profile_settings.model if profile_settings else None)
        selected_provider = request.provider or (profile_settings.provider if profile_settings else None)
        if profile_settings and profile_settings.allowed_tools:
            profile_tool_specs = [
                item for item in registry.as_openai_tools()
                if item["function"]["name"] in profile_settings.allowed_tools
            ]
            registry = registry.filtered(profile_tool_specs)
        if profile_settings and profile_settings.read_only:
            registry = registry.read_only()
        profile_registry = registry
        provider_name, resolved_model, client = ModelRouter(self._settings).resolve(selected_model, selected_provider)
        if not selected_model and not selected_provider:
            client = self._client
        profile = self._settings.provider(provider_name, resolved_model)
        capabilities = self._capability_registry.get(provider_name, resolved_model)
        execution_mode = request.execution_mode or (profile_settings.execution_mode if profile_settings else None) or self._settings.agent.execution_mode
        if execution_mode == "auto":
            execution_mode = "native_tool_call" if capabilities.get("tool_call", False) else "json_fallback"
        if execution_mode == "native_tool_call" and not capabilities.get("tool_call", False):
            raise ValueError(f"Provider {provider_name} does not support native tool calls for model {resolved_model}.")
        if request.parallel_tool_calls and execution_mode == "native_tool_call" and not capabilities.get("parallel_tool_calls", False):
            raise ValueError(f"Provider {provider_name} does not support parallel tool calls for model {resolved_model}.")
        if execution_mode == "json_fallback" and not self._settings.tool_policy.fallback_allow_write_tools:
            registry = profile_registry.read_only()
        tools = registry.as_openai_tools()
        logger.debug("Available tools request_id=%s tools=%s", request_id, [item["function"]["name"] for item in tools])
        if execution_mode == "no_tools":
            tools = []
        api_tools = tools if execution_mode == "native_tool_call" else []
        if execution_mode == "json_fallback" and tools:
            messages.append({"role": "system", "content": fallback_instruction(tools)})
        tool_calls_used = 0
        legacy_approved_tools = request.metadata.get("approved_tools", [])
        approved_tools = set(request.approved_tools)
        if isinstance(legacy_approved_tools, list):
            approved_tools.update(str(name) for name in legacy_approved_tools)
        max_tool_calls = min(
            self._settings.agent.max_tool_calls,
            self._settings.tool_policy.max_calls_per_request,
        )
        emit(AgentEvent(type="request_started", payload={"provider": provider_name, "model": resolved_model, "execution_mode": execution_mode}))

        for iteration in range(1, self._settings.agent.max_iterations + 1):
            logger.info(f"迭代 #{iteration}")
            emit(AgentEvent(type="iteration", payload={"iteration": iteration, "request_id": request_id}))
            model_started_at = time.monotonic()
            parse_attempt = 0
            fallback_calls = []
            while True:
                emit(AgentEvent(type="model_started", payload={"provider": provider_name, "model": resolved_model, "iteration": iteration}))
                try:
                    llm_response = await client.complete(
                        messages=messages,
                        tools=api_tools,
                        tool_choice=("none" if not api_tools else (tool_choice or request.tool_choice)),
                        parallel_tool_calls=parallel_tool_calls if parallel_tool_calls is not None else request.parallel_tool_calls,
                        model=resolved_model,
                    )
                except Exception as exc:
                    fallback_name = profile.fallback_provider
                    if not fallback_name or fallback_name == provider_name:
                        raise
                    logger.warning("Provider %s failed; trying configured fallback %s: %s", provider_name, fallback_name, exc)
                    provider_name = fallback_name
                    profile = self._settings.provider(provider_name, resolved_model)
                    client = ModelRouter(self._settings).resolve(resolved_model, provider_name)[2]
                    capabilities = self._capability_registry.get(provider_name, resolved_model)
                    execution_mode = "native_tool_call" if capabilities.get("tool_call", False) else "json_fallback"
                    if execution_mode == "native_tool_call" and not self._settings.tool_policy.read_only_mode:
                        registry = profile_registry
                    elif execution_mode == "json_fallback" and not self._settings.tool_policy.fallback_allow_write_tools:
                        registry = profile_registry.read_only()
                    tools = registry.as_openai_tools()
                    api_tools = tools if execution_mode == "native_tool_call" else []
                    if execution_mode == "json_fallback" and tools:
                        messages.append({"role": "system", "content": fallback_instruction(tools)})
                    emit(AgentEvent(type="provider_fallback", payload={"provider": provider_name, "error": str(exc)}))
                    llm_response = await client.complete(
                        messages=messages,
                        tools=api_tools,
                        tool_choice=("none" if not api_tools else (tool_choice or request.tool_choice)),
                        parallel_tool_calls=parallel_tool_calls if parallel_tool_calls is not None else request.parallel_tool_calls,
                        model=resolved_model,
                    )
                emit(AgentEvent(type="model_completed", payload={"provider": provider_name, "model": resolved_model, "execution_mode": execution_mode, "iteration": iteration, "tool_calls": len(llm_response.tool_calls), "elapsed_ms": round((time.monotonic() - model_started_at) * 1000, 2)}))
                if execution_mode != "json_fallback" or llm_response.tool_calls or not tools:
                    break
                try:
                    fallback_calls, final_content = parse_fallback_response(llm_response.content)
                    if final_content is not None:
                        llm_response.content = final_content
                    break
                except FallbackParseError as exc:
                    emit(AgentEvent(type="fallback_parse_error", payload={"error": str(exc), "iteration": iteration, "attempt": parse_attempt + 1}))
                    if parse_attempt >= self._settings.agent.fallback_parse_retries:
                        final_message = ChatMessage(role="assistant", content=llm_response.content)
                        emit(AgentEvent(type="assistant", payload={"content": llm_response.content}))
                        return final_message, iteration
                    messages.extend([
                        {"role": "assistant", "content": llm_response.content},
                        {"role": "user", "content": "Return only the required JSON object. Use the documented tool_calls or final shape."},
                    ])
                    parse_attempt += 1

            calls = list(llm_response.tool_calls)
            if fallback_calls:
                from rill.llm.openai_compat import ToolCall
                calls = [ToolCall(id=call.id, name=call.name, arguments=call.arguments) for call in fallback_calls]

            if calls:
                logger.info(f"LLM 返回工具调用: {[c.name for c in calls]}")
                assistant_message = {"role": "assistant", "content": llm_response.content}
                if execution_mode == "native_tool_call":
                    assistant_message["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=True)},
                        }
                        for call in calls
                    ]
                reasoning_content = llm_response.raw_message.get("reasoning_content")
                if reasoning_content is not None:
                    assistant_message["reasoning_content"] = reasoning_content
                messages.append(assistant_message)

                for call in calls:
                    tool_calls_used += 1
                    if tool_calls_used > max_tool_calls:
                        content = "The agent stopped because the maximum tool call count was reached."
                        emit(AgentEvent(type="error", payload={"code": "max_tool_calls", "message": content}))
                        return ChatMessage(role="assistant", content=content), iteration
                    logger.info("Executing tool request_id=%s call_id=%s tool=%s", request_id, call.id, call.name)
                    emit(
                        AgentEvent(
                            type="tool_call",
                            payload={"id": call.id, "name": call.name, "arguments": call.arguments},
                        )
                    )
                    tool_started_at = time.monotonic()
                    result = await registry.execute(call.name, call.arguments, approved=call.name in approved_tools, request_id=request_id)
                    elapsed_ms = round((time.monotonic() - tool_started_at) * 1000, 2)
                    logger.info("Tool request completed request_id=%s call_id=%s tool=%s ok=%s elapsed_ms=%s", request_id, call.id, call.name, result.get("ok", "unknown"), elapsed_ms)
                    emit(
                        AgentEvent(
                            type="tool_result",
                            payload={"id": call.id, "name": call.name, "result": result, "elapsed_ms": elapsed_ms},
                        )
                    )
                    self._emit_tool_trace_events(call.name, result, emit)
                    if execution_mode == "native_tool_call":
                        messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": safe_json_dumps(result, ensure_ascii=True)})
                    else:
                        messages.append({"role": "user", "content": f"Tool result for {call.name}: {safe_json_dumps(result, ensure_ascii=True)}"})
                continue

            logger.info(f"LLM 返回最终回复")
            final_message = ChatMessage(role="assistant", content=llm_response.content)
            emit(AgentEvent(type="assistant", payload={"content": llm_response.content}))
            return final_message, iteration

        logger.warning(f"达到最大迭代次数 {self._settings.agent.max_iterations}")
        content = (
            "The agent stopped because the maximum iteration count was reached before a final "
            "answer was produced."
        )
        final_message = ChatMessage(role="assistant", content=content)
        emit(AgentEvent(type="assistant", payload={"content": content}))
        emit(AgentEvent(type="error", payload={"code": "max_iterations", "message": content}))
        return final_message, self._settings.agent.max_iterations

    def _stamp_event(self, event: AgentEvent, request_id: str, events: list[AgentEvent]) -> None:
        event.request_id = request_id
        event.timestamp = time.time()
        if "request_id" not in event.payload:
            event.payload["request_id"] = request_id
        events.append(event)
        if self._event_sink:
            self._event_sink(event)

    def _stop_reason(self, events: list[AgentEvent]) -> str:
        error = next((event.payload.get("code") for event in events if event.type == "error"), None)
        if error:
            return str(error)
        if any(event.type == "fallback_parse_error" for event in events):
            return "fallback_parse_error"
        return "completed"

    def _build_messages(self, request: AgentRequest) -> list[dict[str, Any]]:
        return self._build_messages_with_options(request, include_system_prompt=True)

    def _build_messages_with_options(
        self,
        request: AgentRequest,
        include_system_prompt: bool,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if include_system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": self._system_prompt(),
                }
            )
        messages.extend(message.model_dump(exclude_none=True) for message in request.messages)
        return messages

    def _build_messages(
        self,
        request: AgentRequest,
        include_system_prompt: bool = True,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if include_system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": self._system_prompt(),
                }
            )
        messages.extend(message.model_dump(exclude_none=True) for message in request.messages)
        return messages

    def _system_prompt(self) -> str:
        prompt = self._settings.agent.system_prompt + "\nUse tools when they help. Explain risky actions before requesting them."
        if any(tool.name == "alert_query" for tool in self._registry.describe()):
            prompt += (
                "\nWhen using alert_query, follow these rules: if the user provides no time, call it without "
                "time arguments so it queries the latest 30 minutes; if the user provides exactly one time point, "
                "pass it as time_point so the tool queries 30 minutes before and after that point; include alarm "
                "levels in the final answer. After the tool returns, strictly use this output format: 【查询范围】, "
                "【告警统计】, 【未清除告警TopN】, 【总告警TopN】, 【分析】. The statistics must include uncleared "
                "alerts and total alerts, and TopN details must be ranked by count and severity."
            )
        return prompt

    def _estimate_usage(
        self,
        request_messages: list[ChatMessage],
        completion_content: Any,
    ) -> ChatCompletionUsage:
        prompt_text = " ".join(str(message.content) for message in request_messages)
        completion_text = str(completion_content or "")
        prompt_tokens = self._estimate_tokens(prompt_text)
        completion_tokens = self._estimate_tokens(completion_text)
        return ChatCompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

    def _estimate_tokens(self, text: str) -> int:
        stripped = text.strip()
        if not stripped:
            return 0
        return len(stripped.split())

    def _emit_tool_trace_events(
        self,
        tool_name: str,
        result: dict[str, Any],
        emit: Callable[[AgentEvent], None],
    ) -> None:
        if tool_name != "database_query":
            return
        trace = result.get("trace")
        if not isinstance(trace, dict):
            return

        emit(
            AgentEvent(
                type="database_trace",
                payload={
                    "question": trace.get("question"),
                    "database": trace.get("database"),
                    "tables": trace.get("tables", []),
                    "attempt_count": trace.get("attempt_count"),
                    "final_sql": trace.get("final_sql"),
                },
            )
        )
        schema_context = trace.get("schema_context")
        if schema_context:
            emit(
                AgentEvent(
                    type="database_schema_context",
                    payload={"schema_context": schema_context},
                )
            )
        for attempt in trace.get("attempts", []):
            emit(
                AgentEvent(
                    type="database_sql_attempt",
                    payload={
                        "attempt": attempt.get("attempt"),
                        "sql": attempt.get("sql"),
                        "retry_reason": attempt.get("retry_reason"),
                        "error": attempt.get("error"),
                    },
                )
            )
        emit(
            AgentEvent(
                type="database_result_summary",
                payload={
                    "ok": result.get("ok"),
                    "row_count": result.get("row_count"),
                    "columns": result.get("columns", []),
                    "generated_sql": result.get("generated_sql"),
                },
            )
        )

    def _split_stream_content(self, content: str) -> list[str]:
        if not content:
            return []
        words = content.split(" ")
        if len(words) <= 1:
            return [content]
        parts: list[str] = []
        for index, word in enumerate(words):
            if index < len(words) - 1:
                parts.append(f"{word} ")
            else:
                parts.append(word)
        return parts

    def _stream_tool_call_deltas(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deltas: list[dict[str, Any]] = []
        for index, tool_call in enumerate(tool_calls):
            function = tool_call.get("function", {})
            arguments = function.get("arguments", "")
            deltas.append(
                {
                    "index": index,
                    "id": tool_call.get("id"),
                    "type": tool_call.get("type", "function"),
                    "function": {
                        "name": function.get("name"),
                        "arguments": "",
                    },
                }
            )
            for part in self._split_argument_stream(arguments):
                deltas.append(
                    {
                        "index": index,
                        "function": {
                            "arguments": part,
                        },
                    }
                )
        return deltas

    def _split_argument_stream(self, arguments: str) -> list[str]:
        if not arguments:
            return []
        chunk_size = 12
        return [arguments[i : i + chunk_size] for i in range(0, len(arguments), chunk_size)]
