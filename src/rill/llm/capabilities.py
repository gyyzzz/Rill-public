"""Capability discovery and a small in-memory cache."""

from __future__ import annotations

from typing import Any

from rill.config import Settings
from rill.llm.router import ProviderRegistry


class CapabilityRegistry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}

    def get(self, provider: str, model: str) -> dict[str, Any]:
        key = (provider, model)
        if key in self._cache:
            return dict(self._cache[key])
        profile = self._settings.provider(provider, model)
        capabilities = profile.capabilities.model_dump(by_alias=True)
        self._cache[key] = capabilities
        return dict(capabilities)

    def set(self, provider: str, model: str, capabilities: dict[str, Any]) -> None:
        self._cache[(provider, model)] = dict(capabilities)

    def clear(self) -> None:
        self._cache.clear()

    async def probe(self, provider: str, model: str) -> dict[str, Any]:
        client = ProviderRegistry(self._settings).get(provider, model)
        connection = await client.check()
        if not connection.get("ok"):
            return connection
        result = await client.probe_capabilities()
        if result.get("ok"):
            self.set(provider, model, result.get("capabilities") or self.get(provider, model))
        return result
