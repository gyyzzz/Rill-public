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
from typing import Any

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
    app_name: str = "OrionXCore"
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)


class LLMSettings(StrictConfigModel):
    model: str = "gpt-4.1-mini"
    api_key: str = ""
    api_base_url: str = "https://api.openai.com/v1"
    http_timeout: int = Field(default=120, ge=1)


class AgentSettings(StrictConfigModel):
    system_prompt: str = "You are OrionXCore, a lightweight AI coding agent."
    max_iterations: int = Field(default=6, ge=1)
    max_concurrent_requests: int = Field(default=4, ge=1)
    queue_timeout_seconds: float = Field(default=5.0, ge=0)


class LoggingSettings(StrictConfigModel):
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: Path | None = None


class TerminalSettings(StrictConfigModel):
    enabled: bool = True
    workdir: Path = Field(default=PROJECT_ROOT)
    timeout_seconds: int = Field(default=30, ge=1)
    allow_risky_commands: bool = False
    require_confirmation: bool = False


class DatabaseSettings(StrictConfigModel):
    enabled: bool = False
    url: str = ""
    max_rows: int = Field(default=200, ge=1)
    query_timeout_seconds: int = Field(default=30, ge=1)
    allow_mutation: bool = False
    allowed_databases: list[str] = Field(default_factory=list)


class FilesystemSettings(StrictConfigModel):
    enabled: bool = False
    workdir: Path | None = None
    allow_write: bool = True
    allow_delete: bool = False
    max_read_bytes: int = Field(default=1_048_576, ge=1)
    max_write_bytes: int = Field(default=1_048_576, ge=1)
    max_list_entries: int = Field(default=500, ge=1)


class ChartSettings(StrictConfigModel):
    enabled: bool = True
    workdir: Path | None = None
    max_width: int = Field(default=1920, ge=1)
    max_height: int = Field(default=1080, ge=1)
    default_dpi: int = Field(default=100, ge=1)
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

    config_file: Path = Field(default=PROJECT_ROOT / "config.toml")
    server: ServerSettings = Field(default_factory=ServerSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    terminal: TerminalSettings = Field(default_factory=TerminalSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    filesystem: FilesystemSettings = Field(default_factory=FilesystemSettings)
    chart: ChartSettings = Field(default_factory=ChartSettings)
    upload: UploadSettings = Field(default_factory=UploadSettings)

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
            TomlSettingsSource(settings_cls),
            file_secret_settings,
        )

    @model_validator(mode="after")
    def resolve_paths(self) -> "Settings":
        self.config_file = _resolve(self.config_file) or PROJECT_ROOT / "config.toml"
        self.logging.file = _resolve(self.logging.file)
        self.terminal.workdir = _resolve(self.terminal.workdir) or PROJECT_ROOT
        self.filesystem.workdir = _resolve(self.filesystem.workdir)
        self.chart.workdir = _resolve(self.chart.workdir)
        self.upload.workdir = _resolve(self.upload.workdir)
        self.upload.allowed_extensions = [
            extension if extension.startswith(".") else f".{extension}"
            for extension in self.upload.allowed_extensions
        ]
        self.database.allowed_databases = [
            database.strip()
            for database in self.database.allowed_databases
            if database.strip()
        ]
        return self


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
    return Settings()
