from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = True
    risk_level: str = "low"
    requires_approval: bool = False
    timeout_seconds: float | None = None
    max_result_bytes: int | None = None

    def descriptor(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "read_only": self.read_only,
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "timeout_seconds": self.timeout_seconds,
            "max_result_bytes": self.max_result_bytes,
        }

    def is_read_only(self, arguments: dict[str, Any] | None = None) -> bool:
        return self.read_only

    def approval_required(self, arguments: dict[str, Any] | None = None) -> bool:
        return self.requires_approval

    def schema_for(self, read_only: bool = False) -> dict[str, Any]:
        return self.input_schema

    @abstractmethod
    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

