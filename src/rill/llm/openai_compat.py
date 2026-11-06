import json
import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from rill.config import Settings
from rill.llm.errors import ProviderRequestError
from rill.llm.provider import ProviderAdapter


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    raw_message: dict[str, Any]


class OpenAICompatibleClient(ProviderAdapter):
    def __init__(
        self,
        settings: Settings,
        provider_name: str | None = None,
        model: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self.name = provider_name or settings.llm.provider
        self._model = model
        self._transport = transport

    @property
    def profile(self):
        return self._settings.provider(self.name, self._model)

    def _headers(self, accept: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = self.profile.api_key or self._settings.llm.api_key
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if accept:
            headers["Accept"] = accept
        return headers

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] | None = None,
        parallel_tool_calls: bool | None = None,
        model: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        stop: str | list[str] | None = None,
    ) -> LLMResponse:
        payload = {
            "model": model or self._model or self.profile.default_model or self._settings.llm.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice if tool_choice is not None else ("auto" if tools else "none"),
        }
        if parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = parallel_tool_calls
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stop is not None:
            payload["stop"] = stop
        headers = self._headers()
        timeout = httpx.Timeout(self.profile.timeout_seconds)
        last_error: Exception | None = None
        for attempt in range(self.profile.retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False, transport=self._transport) as client:
                    response = await client.post(
                        f"{self.profile.api_base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()
                break
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code != 429 and exc.response.status_code < 500:
                    raise ProviderRequestError(
                        self.name,
                        f"Provider rejected the request with HTTP {exc.response.status_code}.",
                        upstream_status=exc.response.status_code,
                    ) from exc
                if attempt >= self.profile.retries:
                    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                    raise ProviderRequestError(
                        self.name,
                        f"Provider request failed: {type(exc).__name__}.",
                        upstream_status=status,
                        retriable=True,
                    ) from exc
                import asyncio
                await asyncio.sleep(self.profile.retry_backoff_seconds * (2 ** attempt))
        else:
            raise last_error or RuntimeError("Provider request failed")

        data = response.json()
        message = data["choices"][0]["message"]
        try:
            tool_calls = [self._parse_tool_call(item) for item in message.get("tool_calls", [])]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderRequestError(self.name, "Provider returned malformed tool calls.") from exc
        return LLMResponse(
            content=self._flatten_content(message.get("content")),
            tool_calls=tool_calls,
            raw_message=message,
        )

    async def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **options: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": options.get("model") or self._model or self.profile.default_model,
            "messages": messages,
            "tools": tools,
            "tool_choice": options.get("tool_choice", "auto" if tools else "none"),
            "stream": True,
        }
        for key in ("temperature", "top_p", "max_tokens", "stop", "parallel_tool_calls"):
            if options.get(key) is not None:
                payload[key] = options[key]
        headers = self._headers("text/event-stream")
        timeout = httpx.Timeout(self.profile.timeout_seconds)
        for attempt in range(self.profile.retries + 1):
            received_chunk = False
            try:
                async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False, transport=self._transport) as client:
                    async with client.stream(
                        "POST",
                        f"{self.profile.api_base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json=payload,
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if not data or data == "[DONE]":
                                continue
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            received_chunk = True
                            yield chunk
                return
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status != 429 and status < 500:
                    raise ProviderRequestError(self.name, f"Provider rejected the request with HTTP {status}.", upstream_status=status) from exc
                if received_chunk or attempt >= self.profile.retries:
                    raise ProviderRequestError(self.name, f"Provider streaming request failed with HTTP {status}.", upstream_status=status, retriable=True) from exc
                await asyncio.sleep(self.profile.retry_backoff_seconds * (2 ** attempt))
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, OSError) as exc:
                if received_chunk or attempt >= self.profile.retries:
                    raise ProviderRequestError(self.name, f"Provider streaming request failed: {type(exc).__name__}.", retriable=True) from exc
                await asyncio.sleep(self.profile.retry_backoff_seconds * (2 ** attempt))

    def _parse_tool_call(self, item: dict[str, Any]) -> ToolCall:
        function = item.get("function", {})
        arguments = function.get("arguments") or "{}"
        if isinstance(arguments, str):
            parsed_arguments = json.loads(arguments)
        else:
            parsed_arguments = arguments
        return ToolCall(
            id=item["id"],
            name=function["name"],
            arguments=parsed_arguments,
        )

    def _flatten_content(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            return "\n".join(part for part in parts if part)
        return str(content)

    async def list_models(self, refresh: bool = False) -> list[str]:
        """Return configured models, falling back to the provider models endpoint."""
        if self.profile.models and not refresh:
            return list(self.profile.models)
        headers = self._headers()
        headers.pop("Content-Type", None)
        timeout = httpx.Timeout(self.profile.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False, transport=self._transport) as client:
            response = await client.get(f"{self.profile.api_base_url.rstrip('/')}/models", headers=headers)
            response.raise_for_status()
        return [str(item["id"]) for item in response.json().get("data", []) if item.get("id")]

    async def check(self) -> dict[str, Any]:
        try:
            models = await self.list_models(refresh=True)
            return {"ok": True, "provider": self.name, "models": models, "capabilities": self.profile.capabilities.model_dump(by_alias=True)}
        except Exception as exc:
            return {"ok": False, "provider": self.name, "error": f"Connection check failed: {type(exc).__name__}."}

    async def probe_capabilities(self) -> dict[str, Any]:
        """Actively test low-cost protocol capabilities for a selected model."""
        baseline = self.profile.capabilities.model_dump(by_alias=True)
        tool_schema = [{
            "type": "function",
            "function": {
                "name": "rill_capability_probe",
                "description": "Return the value requested by the user.",
                "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
            },
        }]
        try:
            tool_result = await self.complete(
                messages=[
                    {"role": "system", "content": "Call the provided function with value 'ok'."},
                    {"role": "user", "content": "Call rill_capability_probe now."},
                ],
                tools=tool_schema,
                tool_choice={"type": "function", "function": {"name": "rill_capability_probe"}},
                model=self._model or self.profile.default_model,
                temperature=0,
                max_tokens=32,
            )
            tool_call = bool(tool_result.tool_calls and tool_result.tool_calls[0].name == "rill_capability_probe")
        except Exception:
            tool_call = False
        json_mode = False
        try:
            headers = self._headers()
            headers.pop("Content-Type", None)
            timeout = httpx.Timeout(self.profile.timeout_seconds)
            async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False, transport=self._transport) as client:
                response = await client.post(
                    f"{self.profile.api_base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json={
                        "model": self._model or self.profile.default_model,
                        "messages": [{"role": "user", "content": "Return a JSON object with key ok and value true."}],
                        "response_format": {"type": "json_object"},
                        "max_tokens": 20,
                        "temperature": 0,
                    },
                )
                response.raise_for_status()
                json_data = response.json()
                content = json_data.get("choices", [{}])[0].get("message", {}).get("content")
                try:
                    json_mode = isinstance(json.loads(content or ""), dict)
                except (json.JSONDecodeError, TypeError):
                    json_mode = False
                reasoning = bool(json_data.get("choices", [{}])[0].get("message", {}).get("reasoning_content"))
        except Exception:
            reasoning = False
        streaming = False
        try:
            async for _ in self.stream_complete(
                messages=[{"role": "user", "content": "Reply with one word."}],
                tools=[],
                model=self._model or self.profile.default_model,
                max_tokens=4,
            ):
                streaming = True
        except Exception:
            streaming = False
        capabilities = {
            **baseline,
            "tool_call": tool_call,
            "json": json_mode,
            "streaming": streaming,
            "reasoning": reasoning,
        }
        return {"ok": True, "provider": self.name, "model": self._model or self.profile.default_model, "capabilities": capabilities}
