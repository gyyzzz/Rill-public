from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool"]
    content: Any
    name: str | None = None
    tool_call_id: str | None = None


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    messages: list[ChatMessage]
    session_id: str | None = None
    request_id: str | None = None
    model: str | None = None
    provider: str | None = None
    profile: str | None = None
    execution_mode: Literal["auto", "native_tool_call", "json_fallback", "no_tools"] | None = None
    tools: list[dict[str, Any]] | None = None
    approved_tools: list[str] = Field(default_factory=list)
    tool_choice: str | dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentEvent(BaseModel):
    type: str
    payload: dict[str, Any]
    request_id: str | None = None
    timestamp: float | None = None


class AgentResponse(BaseModel):
    message: ChatMessage
    events: list[AgentEvent] = Field(default_factory=list)
    iterations: int
    session_id: str | None = None
    request_id: str | None = None
    provider: str | None = None
    model: str | None = None
    execution_mode: str | None = None
    stop_reason: str | None = None


class ToolDescriptor(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = True
    risk_level: Literal["low", "medium", "high"] = "low"
    requires_approval: bool = False
    timeout_seconds: float | None = None
    max_result_bytes: int | None = None


class ModelDescriptor(BaseModel):
    id: str
    object: Literal["model"] = "model"
    owned_by: str
    capabilities: dict[str, Any] = Field(default_factory=dict)


class ModelListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelDescriptor]


class CapabilityProbeRequest(BaseModel):
    provider: str
    model: str


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    messages: list[ChatMessage]
    model: str | None = None
    provider: str | None = None
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: str | list[str] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    user: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatCompletionMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["assistant"]
    content: str | None
    tool_calls: list[dict[str, Any]] | None = None


class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatCompletionMessage
    finish_reason: str


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"]
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage


class ChatCompletionDelta(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["assistant"] | None = None
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatCompletionChunkChoice(BaseModel):
    index: int
    delta: ChatCompletionDelta
    finish_reason: str | None = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: Literal["chat.completion.chunk"]
    created: int
    model: str
    choices: list[ChatCompletionChunkChoice]


class UploadResponse(BaseModel):
    ok: bool
    filename: str
    path: str
    size_bytes: int
    content_type: str | None = None
