from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration with a no-credentials local demo default."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "daypilot-local"
    database_url: str = "sqlite:///./data/daypilot.db"
    daypilot_timezone: str = "Asia/Kolkata"
    daypilot_demo_mode: bool = True
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    @field_validator("openai_api_key", "langsmith_api_key", mode="before")
    @classmethod
    def blank_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def database_path(self) -> Path:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("Local DayPilot currently requires a sqlite:/// DATABASE_URL")
        raw_path = self.database_url.removeprefix(prefix)
        path = Path(raw_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    @property
    def reasoning_mode(self) -> str:
        return "openai" if self.openai_api_key else "deterministic_demo"

    def configure_observability(self) -> None:
        os.environ["LANGSMITH_TRACING"] = str(self.langsmith_tracing).lower()
        os.environ["LANGSMITH_PROJECT"] = self.langsmith_project
        if self.langsmith_api_key:
            os.environ["LANGSMITH_API_KEY"] = self.langsmith_api_key


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.configure_observability()
    return settings
