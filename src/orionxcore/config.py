from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORIONXCORE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "OrionXCore"
    host: str = "0.0.0.0"
    port: int = 8080
    model: str = "gpt-4.1-mini"
    api_key: str = ""
    api_base_url: str = "https://api.openai.com/v1"
    system_prompt: str = "You are OrionXCore, a lightweight AI coding agent."
    max_iterations: int = 6
    http_timeout: int = 120

    # 日志配置
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_file: str | None = None  # 日志文件路径，None 表示只输出到控制台

    enable_terminal: bool = True
    terminal_workdir: Path = Field(default_factory=lambda: Path(".").resolve())
    terminal_timeout: int = 30
    allow_risky_commands: bool = False
    require_terminal_confirmation: bool = False  # 每个命令都需用户确认

    enable_database: bool = False
    database_url: str = ""
    database_max_rows: int = 200
    database_query_timeout_seconds: int = 30
    database_allow_mutation: bool = False
    database_allowed_databases: str = ""

    enable_filesystem: bool = False
    filesystem_workdir: Path | None = None
    filesystem_allow_write: bool = True
    filesystem_allow_delete: bool = False
    filesystem_max_read_bytes: int = 1_048_576
    filesystem_max_write_bytes: int = 1_048_576
    filesystem_max_list_entries: int = 500


def setup_logging(settings: Settings) -> None:
    """配置日志系统"""
    import logging
    import sys

    # 获取根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level.upper())

    # 清除已有的 handlers
    root_logger.handlers.clear()

    # 创建格式化器
    formatter = logging.Formatter(settings.log_format)

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(settings.log_level.upper())
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 文件输出（如果配置了）
    if settings.log_file:
        file_handler = logging.FileHandler(settings.log_file, encoding="utf-8")
        file_handler.setLevel(settings.log_level.upper())
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # 设置第三方库日志级别
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logging.info(f"日志系统初始化完成，级别: {settings.log_level}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
