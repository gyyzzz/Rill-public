"""Application configuration loaded from TOML and environment variables.

Configuration precedence is: defaults < ``config.toml`` < ``.env`` < process
environment. Environment variables use the nested form
``ORIONXCORE_<SECTION>__<FIELD>``.
"""

from __future__ import annotations

import logging
import os
import sys
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve(path: Path | None) -> Path | None:
    """Resolve relative paths against the project root."""
    if path is None:
        return None
    if path.is_absolute():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSettings(StrictConfigModel):
    app_name: str = "Rill"
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)


class ClientSettings(StrictConfigModel):
    base_url: str | None = None

    @model_validator(mode="after")
    def validate_base_url(self) -> "ClientSettings":
        if self.base_url is None:
            return self
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("client.base_url must be an HTTP or HTTPS URL.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("client.base_url cannot include credentials, query parameters, or fragments.")
        return self


class ProviderCapabilities(StrictConfigModel):
    """Capabilities used to select native tools or a fallback protocol."""

    tool_call: bool = True
    json_mode: bool = Field(default=True, alias="json")
    streaming: bool = True
    parallel_tool_calls: bool = False
    reasoning: bool = False
    context_length: int | None = Field(default=None, ge=1)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ProviderSettings(StrictConfigModel):
    """One OpenAI-compatible provider profile."""

    type: Literal["openai_compatible"] = "openai_compatible"
    api_base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    api_key_file: Path | None = None
    models: list[str] = Field(default_factory=list)
    default_model: str = "gpt-4.1-mini"
    timeout_seconds: int = Field(default=120, ge=1)
    retries: int = Field(default=2, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.5, ge=0)
    capabilities: ProviderCapabilities = Field(default_factory=ProviderCapabilities)
    fallback_provider: str | None = None


class AgentProfileSettings(StrictConfigModel):
    model: str | None = None
    provider: str | None = None
    execution_mode: str | None = Field(default=None, pattern="^(auto|native_tool_call|json_fallback|no_tools)$")
    allowed_tools: list[str] = Field(default_factory=list)
    read_only: bool = False


class LLMSettings(StrictConfigModel):
    model: str = "gpt-4.1-mini"
    api_key: str = ""
    api_key_file: Path | None = None
    api_base_url: str = "https://api.openai.com/v1"
    http_timeout: int = Field(default=120, ge=1)
    provider: str = "default"
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)
    model_aliases: dict[str, str] = Field(default_factory=dict)
    retries: int = Field(default=2, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.5, ge=0)


class AgentSettings(StrictConfigModel):
    system_prompt: str = "You are Rill, a lightweight AI coding agent."
    max_iterations: int = Field(default=6, ge=1)
    max_tool_calls: int = Field(default=24, ge=1)
    max_result_bytes: int = Field(default=1_048_576, ge=1)
    tool_timeout_seconds: float = Field(default=60, ge=0.1)
    fallback_parse_retries: int = Field(default=2, ge=0, le=5)
    execution_mode: str = Field(default="auto", pattern="^(auto|native_tool_call|json_fallback|no_tools)$")
    profiles: dict[str, AgentProfileSettings] = Field(default_factory=dict)
    max_concurrent_requests: int = Field(default=4, ge=1)
    queue_timeout_seconds: float = Field(default=5.0, ge=0)


class AuthSettings(StrictConfigModel):
    enabled: bool = False
    api_keys: list[str] = Field(default_factory=list)
    agent_api_keys: list[str] = Field(default_factory=list)
    tool_api_keys: list[str] = Field(default_factory=list)
    diagnostic_api_keys: list[str] = Field(default_factory=list)
    allow_local_without_key: bool = True


class CorsSettings(StrictConfigModel):
    enabled: bool = True
    allow_origins: list[str] = Field(default_factory=lambda: ["*"])
    allow_credentials: bool = False

    @model_validator(mode="after")
    def validate_origins(self) -> "CorsSettings":
        if self.allow_credentials and "*" in self.allow_origins:
            raise ValueError("CORS allow_credentials cannot be used with wildcard origins.")
        return self


class ToolPolicySettings(StrictConfigModel):
    read_only_mode: bool = False
    fallback_allow_write_tools: bool = False
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    require_approval_for_risky: bool = False
    max_calls_per_request: int = Field(default=24, ge=1)

    @model_validator(mode="after")
    def validate_policy_names(self) -> "ToolPolicySettings":
        overlap = set(self.allowed_tools) & set(self.denied_tools)
        if overlap:
            raise ValueError(f"Tools cannot be both allowed and denied: {', '.join(sorted(overlap))}")
        return self


class LoggingSettings(StrictConfigModel):
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: Path | None = None


class TerminalSettings(StrictConfigModel):
    enabled: bool = True
    workdir: Path = Field(default=PROJECT_ROOT)
    timeout_seconds: int = Field(default=30, ge=1)
    allow_risky_commands: bool = False
    require_confirmation: bool = True


class DatabaseSettings(StrictConfigModel):
    enabled: bool = True
    url: str = ""
    max_rows: int = Field(default=200, ge=1)
    query_timeout_seconds: int = Field(default=30, ge=1)
    allow_mutation: bool = False
    allowed_databases: list[str] = Field(default_factory=list)


class FilesystemSettings(StrictConfigModel):
    enabled: bool = True
    workdir: Path | None = None
    allow_write: bool = True
    allow_delete: bool = False
    max_read_bytes: int = Field(default=1_048_576, ge=1)
    max_write_bytes: int = Field(default=1_048_576, ge=1)
    max_list_entries: int = Field(default=500, ge=1)


class ChartSettings(StrictConfigModel):
    enabled: bool = False
    workdir: Path | None = None
    max_width: int = Field(default=1920, ge=1)
    max_height: int = Field(default=1080, ge=1)
    default_dpi: int = Field(default=100, ge=1)
    max_points: int = Field(default=10000, ge=1)
    font_family: str = ""


class UploadSettings(StrictConfigModel):
    enabled: bool = True
    max_size_mb: int = Field(default=10, ge=1)
    allowed_extensions: list[str] = Field(
        default_factory=lambda: [
            ".txt", ".csv", ".json", ".md", ".py", ".log", ".yaml", ".yml",
            ".xml", ".html", ".js", ".ts", ".sql", ".sh", ".cfg", ".ini",
            ".conf", ".toml", ".rst",
        ]
    )
    workdir: Path | None = None


class TomlSettingsSource(PydanticBaseSettingsSource):
    """Read the optional TOML file selected by ``config_file``."""

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        configured_path = self.current_state.get("config_file")
        path = Path(configured_path) if configured_path else Path(
            os.environ.get("ORIONXCORE_CONFIG_FILE", PROJECT_ROOT / "config.toml")
        )
        path = _resolve(path)
        if path is None or not path.exists():
            return {}
        if not path.is_file():
            raise ValueError(f"Configuration file does not exist: {path}")
        try:
            with path.open("rb") as config_file:
                data = tomllib.load(config_file)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"Invalid TOML configuration in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"TOML configuration must contain a table: {path}")
        return data


class Settings(BaseSettings):
    """Validated application settings grouped by ownership area."""

    model_config = SettingsConfigDict(
        env_prefix="ORIONXCORE_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    config_version: int = Field(default=1, ge=1)
    config_file: Path = Field(default=PROJECT_ROOT / "config.toml")
    client: ClientSettings = Field(default_factory=ClientSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    terminal: TerminalSettings = Field(default_factory=TerminalSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    filesystem: FilesystemSettings = Field(default_factory=FilesystemSettings)
    chart: ChartSettings = Field(default_factory=ChartSettings)
    upload: UploadSettings = Field(default_factory=UploadSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    cors: CorsSettings = Field(default_factory=CorsSettings)
    tool_policy: ToolPolicySettings = Field(default_factory=ToolPolicySettings)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_config(cls, values: Any) -> Any:
        """Accept the original flat LLM keys while exposing migration metadata."""
        if not isinstance(values, dict):
            return values
        data = dict(values)
        version = int(data.get("config_version", 1))
        if version > 1:
            raise ValueError(f"Configuration version {version} is newer than this Rill release supports.")
        if version < 1:
            version = 1
        data["config_version"] = version
        llm = dict(data.get("llm") or {})
        for old_key, new_key in (("model", "model"), ("api_key", "api_key"), ("api_base_url", "api_base_url")):
            if old_key in data and new_key not in llm:
                llm[new_key] = data[old_key]
        if llm:
            data["llm"] = llm
        legacy_keys = {"model", "api_key", "api_base_url"} & data.keys()
        if legacy_keys:
            logging.getLogger(__name__).warning(
                "Migrating legacy top-level LLM keys into the [llm] table: %s",
                ", ".join(sorted(legacy_keys)),
            )
        return data

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            TomlSettingsSource(settings_cls),
        )

    @model_validator(mode="after")
    def resolve_paths(self) -> "Settings":
        self.config_file = _resolve(self.config_file) or PROJECT_ROOT / "config.toml"
        self.logging.file = _resolve(self.logging.file)
        self.llm.api_key_file = _resolve(self.llm.api_key_file)
        self.terminal.workdir = _resolve(self.terminal.workdir) or PROJECT_ROOT
        self.filesystem.workdir = _resolve(self.filesystem.workdir)
        self.chart.workdir = _resolve(self.chart.workdir)
        self.upload.workdir = _resolve(self.upload.workdir)
        if self.llm.api_key_file and not self.llm.api_key:
            self.llm.api_key = self._read_secret(self.llm.api_key_file, "llm.api_key_file")
        for provider_name, provider in self.llm.providers.items():
            provider.api_key_file = _resolve(provider.api_key_file)
            if provider.api_key_file and not provider.api_key:
                provider.api_key = self._read_secret(provider.api_key_file, f"llm.providers.{provider_name}.api_key_file")
        self.upload.allowed_extensions = [
            extension if extension.startswith(".") else f".{extension}"
            for extension in self.upload.allowed_extensions
        ]
        self.database.allowed_databases = [
            database.strip()
            for database in self.database.allowed_databases
            if database.strip()
        ]
        self.auth.api_keys = [key for key in self.auth.api_keys if key]
        self.auth.agent_api_keys = [key for key in self.auth.agent_api_keys if key]
        self.auth.tool_api_keys = [key for key in self.auth.tool_api_keys if key]
        self.auth.diagnostic_api_keys = [key for key in self.auth.diagnostic_api_keys if key]
        if self.auth.enabled and not any((
            self.auth.api_keys,
            self.auth.agent_api_keys,
            self.auth.tool_api_keys,
            self.auth.diagnostic_api_keys,
        )) and not self.auth.allow_local_without_key:
            raise ValueError("Authentication is enabled but no API keys are configured.")
        self.tool_policy.allowed_tools = [name for name in self.tool_policy.allowed_tools if name]
        self.tool_policy.denied_tools = [name for name in self.tool_policy.denied_tools if name]
        valid_providers = set(self.llm.providers) | {"default"}
        if self.llm.provider not in valid_providers:
            raise ValueError(f"Unknown active provider: {self.llm.provider}")
        for provider_name, provider in self.llm.providers.items():
            if provider.fallback_provider and provider.fallback_provider not in valid_providers:
                raise ValueError(f"Provider {provider_name} references unknown fallback provider {provider.fallback_provider}")
            parsed = urlparse(provider.api_base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"Provider {provider_name} api_base_url must be an HTTP or HTTPS URL.")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(f"Provider {provider_name} api_base_url cannot include credentials, query parameters, or fragments.")
        for profile_name, profile in self.agent.profiles.items():
            if profile.provider and profile.provider not in valid_providers:
                raise ValueError(f"Agent profile {profile_name} references unknown provider {profile.provider}")
        parsed = urlparse(self.llm.api_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("llm.api_base_url must be an HTTP or HTTPS URL.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("llm.api_base_url cannot include credentials, query parameters, or fragments.")
        if self.database.url and not self.database.url.startswith(("clickhouse://", "clickhousedb://")):
            raise ValueError("database.url must use a ClickHouse URL.")
        return self

    def _read_secret(self, path: Path, field_name: str) -> str:
        try:
            secret = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"Unable to read configured secret file for {field_name}: {path}") from exc
        if not secret:
            raise ValueError(f"Configured secret file for {field_name} is empty: {path}")
        return secret

    def provider(self, name: str | None = None, model: str | None = None) -> ProviderSettings:
        """Resolve a provider profile with backwards-compatible LLM settings."""
        provider_name = name or self.llm.provider
        requested_model = self.llm.model_aliases.get(model, model) if model else None
        profile = self.llm.providers.get(provider_name)
        if profile is None and provider_name != "default":
            profile = self.llm.providers.get("default")
        if profile is None:
            profile = ProviderSettings(
                api_base_url=self.llm.api_base_url,
                api_key=self.llm.api_key,
                default_model=requested_model or self.llm.model,
                timeout_seconds=self.llm.http_timeout,
                retries=self.llm.retries,
                retry_backoff_seconds=self.llm.retry_backoff_seconds,
            )
        elif requested_model:
            profile = profile.model_copy(update={"default_model": requested_model})
        return profile


def setup_logging(settings: Settings) -> None:
    """Configure application and third-party logging."""
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.logging.level.upper())
    root_logger.handlers.clear()

    formatter = logging.Formatter(settings.logging.format)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(settings.logging.level.upper())
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if settings.logging.file:
        file_handler = logging.FileHandler(settings.logging.file, encoding="utf-8")
        file_handler.setLevel(settings.logging.level.upper())
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    for logger_name in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    logging.info("日志系统初始化完成，级别: %s", settings.logging.level)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process; changes take effect after restart."""
    config_path = _resolve(Path(os.environ.get("ORIONXCORE_CONFIG_FILE", PROJECT_ROOT / "config.toml")))
    return Settings(config_file=config_path, _env_file=config_path.parent / ".env")
