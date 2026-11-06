"""Provider and model selection for API requests and diagnostics."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from rill.config import Settings
from rill.llm.openai_compat import OpenAICompatibleClient


def sanitize_base_url(value: str) -> str:
    parts = urlsplit(value)
    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        port = ""
    return urlunsplit((parts.scheme, hostname + port, parts.path, "", ""))


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def names(self) -> list[str]:
        names = list(self._settings.llm.providers)
        if "default" not in names:
            names.insert(0, "default")
        return names

    def get(self, name: str | None = None, model: str | None = None) -> OpenAICompatibleClient:
        provider_name = name or self._settings.llm.provider
        if provider_name != "default" and provider_name not in self._settings.llm.providers:
            raise ValueError(f"Unknown provider: {provider_name}")
        return OpenAICompatibleClient(self._settings, provider_name=provider_name, model=model)

    def profiles(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "type": self._settings.provider(name).type,
                "api_base_url": sanitize_base_url(self._settings.provider(name).api_base_url),
                "models": self._settings.provider(name).models,
                "default_model": self._settings.provider(name).default_model,
                "capabilities": self._settings.provider(name).capabilities.model_dump(by_alias=True),
            }
            for name in self.names()
        ]

    def client(self, name: str | None = None, model: str | None = None):
        return self.get(name, model)


class ModelRouter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._providers = ProviderRegistry(settings)

    def resolve(self, model: str | None = None, provider: str | None = None) -> tuple[str, str, OpenAICompatibleClient]:
        provider_name = provider or self._settings.llm.provider
        if provider_name != "default" and provider_name not in self._settings.llm.providers:
            raise ValueError(f"Unknown provider: {provider_name}")
        profile = self._settings.provider(provider_name, model)
        resolved = profile.default_model or self._settings.llm.model
        return provider_name, resolved, self._providers.get(provider_name, resolved)

    def models(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for provider in self._providers.profiles():
            for model in provider["models"] or [provider["default_model"]]:
                result.append({
                    "id": model,
                    "object": "model",
                    "owned_by": provider["name"],
                    "capabilities": provider["capabilities"],
                })
        return result
