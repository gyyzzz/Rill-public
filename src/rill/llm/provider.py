"""Provider abstractions used by the Agent runtime.

The first implementation speaks the OpenAI-compatible HTTP protocol, but the
runtime depends on this small interface so additional providers do not leak
protocol details into API handlers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from rill.config import ProviderSettings, Settings


class ProviderAdapter(ABC):
    """Common adapter contract for model providers."""

    name: str

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError

    @abstractmethod
    async def list_models(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    async def stream_complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **options: Any):
        raise NotImplementedError

    @abstractmethod
    async def check(self) -> dict[str, Any]:
        raise NotImplementedError


def provider_profile(settings: Settings, name: str | None = None, model: str | None = None) -> ProviderSettings:
    return settings.provider(name=name, model=model)
