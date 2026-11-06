import asyncio
import json
import logging
from typing import Any

from rill.config import Settings
from rill.schemas import ToolDescriptor
from rill.tools.base import Tool
from rill.tools.chart import ChartTool
from rill.tools.database import DatabaseTool
from rill.tools.filesystem import FileSystemTool
from rill.tools.terminal import TerminalTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self, tools: list[Tool], settings: Settings | None = None, read_only: bool = False) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._settings = settings
        self._read_only = read_only
        if settings:
            for tool in tools:
                if tool.timeout_seconds is None:
                    tool.timeout_seconds = settings.agent.tool_timeout_seconds
                if tool.max_result_bytes is None:
                    tool.max_result_bytes = settings.agent.max_result_bytes

    def describe(self) -> list[ToolDescriptor]:
        return [
            ToolDescriptor(
                name=tool.name,
                description=tool.description,
                input_schema=tool.schema_for(self._read_only or bool(self._settings and self._settings.tool_policy.read_only_mode)),
                read_only=tool.read_only,
                risk_level=tool.risk_level,
                requires_approval=tool.requires_approval,
                timeout_seconds=tool.timeout_seconds,
                max_result_bytes=tool.max_result_bytes,
            )
            for tool in self._tools.values()
            if self._is_allowed(tool)
        ]

    def as_openai_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.schema_for(self._read_only or bool(self._settings and self._settings.tool_policy.read_only_mode)),
                },
            }
            for tool in self._tools.values()
            if self._is_allowed(tool)
        ]

    def filtered(self, requested_tools: list[dict[str, Any]] | None) -> "ToolRegistry":
        if requested_tools is None:
            return ToolRegistry(list(self._tools.values()), self._settings, self._read_only)

        requested_names = {
            item.get("function", {}).get("name")
            for item in requested_tools
            if item.get("type") == "function"
        }
        tools = [tool for name, tool in self._tools.items() if name in requested_names]
        return ToolRegistry(tools, self._settings, self._read_only)

    def read_only(self) -> "ToolRegistry":
        return ToolRegistry(list(self._tools.values()), self._settings, read_only=True)

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        approved: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        if not isinstance(arguments, dict):
            return {"ok": False, "error": "Tool arguments must be a JSON object."}
        policy = self._settings.tool_policy if self._settings else None
        if not self._is_allowed(tool, arguments):
            if policy and policy.allowed_tools and name not in policy.allowed_tools:
                return {"ok": False, "error": f"Tool is not allowlisted: {name}"}
            if policy and name in policy.denied_tools:
                return {"ok": False, "error": f"Tool is denied by policy: {name}"}
            logger.warning("Tool denied request_id=%s tool=%s reason=read_only", request_id, name)
            return {"ok": False, "error": f"Tool is disabled in read-only mode: {name}"}
        if policy:
            policy_approval = policy.require_approval_for_risky and tool.risk_level == "high" and not tool.is_read_only(arguments)
        else:
            policy_approval = False
        if (tool.approval_required(arguments) or policy_approval) and not approved:
            logger.warning("Tool denied request_id=%s tool=%s reason=approval_required", request_id, name)
            return {"ok": False, "error": f"Tool requires explicit approval: {name}", "requires_approval": True}
        required = tool.input_schema.get("required", [])
        missing = [key for key in required if key not in arguments]
        if missing:
            return {"ok": False, "error": f"Missing required arguments: {', '.join(missing)}"}
        validation_error = self._validate_arguments(tool.input_schema, arguments)
        if validation_error:
            return {"ok": False, "error": validation_error}
        try:
            timeout = tool.timeout_seconds
            result = await asyncio.wait_for(tool.execute(arguments), timeout=timeout) if timeout else await tool.execute(arguments)
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"Tool timed out: {name}"}
        except Exception as exc:
            logger.exception("Tool execution failed request_id=%s tool=%s", request_id, name)
            return {"ok": False, "error": f"Tool execution failed: {exc}"}
        limited = self._limit_result(tool, result)
        logger.info("Tool audited request_id=%s tool=%s ok=%s", request_id, name, limited.get("ok"))
        return limited

    def _validate_arguments(self, schema: dict[str, Any], value: dict[str, Any]) -> str | None:
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            return f"Missing required arguments: {', '.join(missing)}"
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = [name for name in value if name not in properties]
            if unknown:
                return f"Unknown arguments: {', '.join(unknown)}"
        for name, item in value.items():
            definition = properties.get(name)
            if not definition:
                continue
            allowed = definition.get("enum")
            if allowed is not None and item not in allowed:
                return f"Invalid value for {name}; expected one of {allowed}."
            expected = definition.get("type")
            checks = {
                "string": lambda x: isinstance(x, str),
                "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
                "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
                "boolean": lambda x: isinstance(x, bool),
                "object": lambda x: isinstance(x, dict),
                "array": lambda x: isinstance(x, list),
            }
            if expected in checks and not checks[expected](item):
                return f"Argument {name} must be {expected}."
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                if "minimum" in definition and item < definition["minimum"]:
                    return f"Argument {name} must be at least {definition['minimum']}."
                if "maximum" in definition and item > definition["maximum"]:
                    return f"Argument {name} must be at most {definition['maximum']}."
            if expected == "object" and isinstance(item, dict):
                nested_error = self._validate_arguments(definition, item)
                if nested_error:
                    return f"Invalid {name}: {nested_error}"
            if expected == "array" and isinstance(item, list) and isinstance(definition.get("items"), dict):
                item_schema = definition["items"]
                item_type = item_schema.get("type")
                type_check = {
                    "string": lambda x: isinstance(x, str),
                    "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
                    "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
                    "boolean": lambda x: isinstance(x, bool),
                    "object": lambda x: isinstance(x, dict),
                }.get(item_type)
                if type_check and any(not type_check(element) for element in item):
                    return f"Items in argument {name} must be {item_type}."
        return None

    def _is_allowed(self, tool: Tool, arguments: dict[str, Any] | None = None) -> bool:
        policy = self._settings.tool_policy if self._settings else None
        if policy is None:
            return True
        if policy.allowed_tools and tool.name not in policy.allowed_tools:
            return False
        if tool.name in policy.denied_tools:
            return False
        if (self._read_only or policy.read_only_mode) and not tool.is_read_only(arguments):
            return False
        return True

    def _limit_result(self, tool: Tool, result: dict[str, Any]) -> dict[str, Any]:
        limit = (tool.max_result_bytes or 0) or (self._settings.agent.max_result_bytes if self._settings else 0)
        if not limit:
            return result
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) <= limit:
            return result
        return {
            "ok": False,
            "error": f"Tool result exceeded {limit} bytes and was truncated.",
            "truncated": True,
            "result_preview": encoded[:limit],
        }


def build_registry(settings: Settings) -> ToolRegistry:
    tools: list[Tool] = []
    if settings.terminal.enabled:
        tools.append(TerminalTool(settings))
    if settings.database.enabled:
        tools.append(DatabaseTool(settings))
    if settings.filesystem.enabled:
        tools.append(FileSystemTool(settings))
    if settings.chart.enabled:
        tools.append(ChartTool(settings))
    return ToolRegistry(tools, settings)
