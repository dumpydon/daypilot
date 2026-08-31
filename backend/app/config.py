from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ProviderMode = Literal["demo", "managed", "direct"]


class Settings(BaseSettings):
    """Runtime configuration with a no-credentials local demo default."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "daypilot-local"
    database_url: str = "sqlite:///./data/daypilot.db"
    daypilot_timezone: str = "Asia/Kolkata"
    daypilot_demo_mode: bool = True
    provider_mode: ProviderMode = Field(
        default="managed",
        validation_alias=AliasChoices("PROVIDER_MODE", "DAYPILOT_PROVIDER_MODE"),
    )
    site_url: str = Field(
        default="http://localhost:3000",
        validation_alias=AliasChoices("SITE_URL", "DAYPILOT_SITE_URL"),
    )
    mail_provider: Literal["demo", "managed", "direct", "gmail", "unavailable"] | None = Field(
        default=None, validation_alias=AliasChoices("MAIL_PROVIDER", "DAYPILOT_MAIL_PROVIDER")
    )
    calendar_provider: (
        Literal["demo", "managed", "direct", "google_calendar", "unavailable"] | None
    ) = Field(
        default=None,
        validation_alias=AliasChoices("CALENDAR_PROVIDER", "DAYPILOT_CALENDAR_PROVIDER"),
    )
    tasks_provider: Literal["demo", "managed", "direct", "google_tasks", "unavailable"] | None = (
        Field(
            default=None, validation_alias=AliasChoices("TASKS_PROVIDER", "DAYPILOT_TASKS_PROVIDER")
        )
    )
    files_provider: Literal["demo", "managed", "direct", "local", "unavailable"] | None = Field(
        default=None, validation_alias=AliasChoices("FILES_PROVIDER", "DAYPILOT_FILES_PROVIDER")
    )
    x_provider: Literal["demo", "managed", "direct", "x_api", "unavailable"] | None = Field(
        default=None, validation_alias=AliasChoices("X_PROVIDER", "DAYPILOT_X_PROVIDER")
    )
    composio_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("COMPOSIO_API_KEY", "DAYPILOT_COMPOSIO_API_KEY"),
    )
    composio_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("COMPOSIO_BASE_URL", "DAYPILOT_COMPOSIO_BASE_URL"),
    )
    composio_google_toolkit: Literal["googlesuper"] = "googlesuper"
    composio_x_toolkit: Literal["twitter"] = "twitter"
    composio_callback_url: str = Field(
        default="http://localhost:8000/api/connections/managed/callback",
        validation_alias=AliasChoices("COMPOSIO_CALLBACK_URL", "DAYPILOT_COMPOSIO_CALLBACK_URL"),
    )
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://localhost:8000/api/connections/google/callback"
    google_task_list_id: str | None = None
    x_client_id: str | None = None
    x_client_secret: str | None = None
    x_redirect_uri: str = "http://localhost:8000/api/connections/x/callback"
    credential_store_path: str = Field(
        default="data/daypilot.credentials.enc",
        validation_alias=AliasChoices("CREDENTIAL_STORE_PATH", "DAYPILOT_CREDENTIAL_STORE_PATH"),
    )
    credential_key_file: str = Field(
        default="data/.daypilot-credential.key",
        validation_alias=AliasChoices("CREDENTIAL_KEY_FILE", "DAYPILOT_CREDENTIAL_KEY_FILE"),
    )
    provider_http_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        le=120,
        validation_alias=AliasChoices(
            "PROVIDER_HTTP_TIMEOUT_SECONDS", "DAYPILOT_PROVIDER_HTTP_TIMEOUT_SECONDS"
        ),
    )
    local_file_max_bytes: int = Field(
        default=1_000_000,
        gt=0,
        le=10_000_000,
        validation_alias=AliasChoices("LOCAL_FILE_MAX_BYTES", "DAYPILOT_LOCAL_FILE_MAX_BYTES"),
    )
    local_file_max_results: int = Field(
        default=25,
        ge=1,
        le=100,
        validation_alias=AliasChoices("LOCAL_FILE_MAX_RESULTS", "DAYPILOT_LOCAL_FILE_MAX_RESULTS"),
    )
    local_file_max_depth: int = Field(
        default=4,
        ge=1,
        le=12,
        validation_alias=AliasChoices("LOCAL_FILE_MAX_DEPTH", "DAYPILOT_LOCAL_FILE_MAX_DEPTH"),
    )
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    @field_validator(
        "openai_api_key",
        "langsmith_api_key",
        "google_client_id",
        "google_client_secret",
        "x_client_id",
        "x_client_secret",
        "composio_api_key",
        "composio_base_url",
        "mail_provider",
        "calendar_provider",
        "tasks_provider",
        "files_provider",
        "x_provider",
        mode="before",
    )
    @classmethod
    def blank_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def database_path(self) -> Path:
        explicit = os.getenv("DAYPILOT_DATABASE_PATH")
        if explicit:
            return Path(explicit).expanduser().resolve()
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("Local DayPilot currently requires a sqlite:/// DATABASE_URL")
        raw_path = self.database_url.removeprefix(prefix)
        path = Path(raw_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    def resolve_path(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    @property
    def credential_path(self) -> Path:
        return self.resolve_path(self.credential_store_path)

    @property
    def credential_key_path(self) -> Path:
        return self.resolve_path(self.credential_key_file)

    def configured_provider(self, service: str) -> str:
        values = {
            "mail": self.mail_provider,
            "calendar": self.calendar_provider,
            "tasks": self.tasks_provider,
            "files": self.files_provider,
            "x": self.x_provider,
        }
        try:
            explicit = values[service]
        except KeyError as exc:
            raise ValueError(f"Unknown provider service {service!r}") from exc
        if self.daypilot_demo_mode:
            return "demo"
        selected = explicit or self.provider_mode
        if selected == "direct":
            return {
                "mail": "gmail",
                "calendar": "google_calendar",
                "tasks": "google_tasks",
                "files": "local",
                "x": "x_api",
            }[service]
        if selected == "managed" and service == "files":
            return "local"
        return selected

    def mcp_environment(self) -> dict[str, str]:
        """Pass non-token provider configuration to the isolated MCP children."""
        values = {
            "DAYPILOT_DEMO_MODE": str(self.daypilot_demo_mode).lower(),
            "DAYPILOT_PROVIDER_MODE": self.provider_mode,
            "DAYPILOT_MAIL_PROVIDER": self.configured_provider("mail"),
            "DAYPILOT_CALENDAR_PROVIDER": self.configured_provider("calendar"),
            "DAYPILOT_TASKS_PROVIDER": self.configured_provider("tasks"),
            "DAYPILOT_FILES_PROVIDER": self.configured_provider("files"),
            "DAYPILOT_X_PROVIDER": self.configured_provider("x"),
            "COMPOSIO_API_KEY": self.composio_api_key or "",
            "COMPOSIO_GOOGLE_TOOLKIT": self.composio_google_toolkit,
            "COMPOSIO_X_TOOLKIT": self.composio_x_toolkit,
            "COMPOSIO_CALLBACK_URL": self.composio_callback_url,
            "GOOGLE_CLIENT_ID": self.google_client_id or "",
            "GOOGLE_CLIENT_SECRET": self.google_client_secret or "",
            "GOOGLE_TASK_LIST_ID": self.google_task_list_id or "",
            "X_CLIENT_ID": self.x_client_id or "",
            "X_CLIENT_SECRET": self.x_client_secret or "",
            "DAYPILOT_CREDENTIAL_STORE_PATH": str(self.credential_path),
            "DAYPILOT_CREDENTIAL_KEY_FILE": str(self.credential_key_path),
            "DAYPILOT_PROVIDER_HTTP_TIMEOUT_SECONDS": str(self.provider_http_timeout_seconds),
            "DAYPILOT_LOCAL_FILE_MAX_BYTES": str(self.local_file_max_bytes),
            "DAYPILOT_LOCAL_FILE_MAX_RESULTS": str(self.local_file_max_results),
            "DAYPILOT_LOCAL_FILE_MAX_DEPTH": str(self.local_file_max_depth),
        }
        if self.composio_base_url:
            values["COMPOSIO_BASE_URL"] = self.composio_base_url
        return values

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
