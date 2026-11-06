"""JSON fallback protocol for models without native tool calling."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class FallbackToolCall:
    name: str
    arguments: dict[str, Any]
    id: str


class FallbackParseError(ValueError):
    pass


def fallback_instruction(tools: list[dict[str, Any]]) -> str:
    definitions = json.dumps(tools, ensure_ascii=True, separators=(",", ":"))
    return (
        "When a tool is needed, respond with exactly one JSON object of the form "
        '{"tool_calls":[{"id":"call_1","name":"tool_name","arguments":{}}]}.'
        " When no tool is needed, respond with {\"final\":\"your answer\"}. "
        f"Available tools: {definitions}"
    )


def parse_fallback_response(content: str) -> tuple[list[FallbackToolCall], str | None]:
    """Parse plain JSON, fenced JSON, or JSON surrounded by explanation text."""
    candidates = [content.strip()]
    candidates.extend(re.findall(r"```(?:json)?\s*(.*?)```", content, flags=re.I | re.S))
    decoder = json.JSONDecoder()
    for index, character in enumerate(content):
        if character == "{":
            try:
                value, _ = decoder.raw_decode(content[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                candidates.append(json.dumps(value))
                break
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        if "final" in value:
            return [], str(value.get("final") or "")
        raw_calls = value.get("tool_calls")
        if isinstance(raw_calls, dict):
            raw_calls = [raw_calls]
        if isinstance(raw_calls, list):
            calls: list[FallbackToolCall] = []
            for index, item in enumerate(raw_calls):
                if not isinstance(item, dict) or not item.get("name"):
                    raise FallbackParseError("tool_calls entries require a name")
                arguments = item.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError as exc:
                        raise FallbackParseError(f"invalid tool arguments: {exc}") from exc
                if not isinstance(arguments, dict):
                    raise FallbackParseError("tool arguments must be a JSON object")
                calls.append(FallbackToolCall(
                    name=str(item["name"]),
                    arguments=arguments,
                    id=str(item.get("id") or f"fallback_call_{index + 1}"),
                ))
            return calls, None
    raise FallbackParseError("response did not contain a valid fallback JSON object")
