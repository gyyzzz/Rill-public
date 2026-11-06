from __future__ import annotations


class ProviderRequestError(RuntimeError):
    def __init__(self, provider: str, message: str, *, upstream_status: int | None = None, retriable: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        self.upstream_status = upstream_status
        self.retriable = retriable
        self.code = "provider_unavailable" if retriable else "provider_request_failed"
