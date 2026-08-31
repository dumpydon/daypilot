from __future__ import annotations

from typing import Any

from backend.app.config import Settings
from backend.app.domain.errors import ProviderUnavailableError
from backend.app.providers.composio import (
    ManagedCalendarService,
    ManagedGoogleWorkspaceService,
    ManagedTasksService,
    ManagedXService,
)
from backend.app.providers.google import GmailService, GoogleCalendarService, GoogleTasksService
from backend.app.providers.local_files import LocalFilesService
from backend.app.providers.mode_store import ProviderModeStore
from backend.app.providers.x_api import ConnectedXService
from mcp_servers.common.store import DemoServiceStore


class UnavailableService:
    def __init__(self, message: str) -> None:
        self.message = message

    def __getattr__(self, _name: str) -> Any:
        def unavailable(*_args: Any, **_kwargs: Any) -> Any:
            raise ProviderUnavailableError(self.message)

        return unavailable


class ProvenanceService:
    """Add a small, non-sensitive provenance envelope to MCP results."""

    def __init__(
        self,
        inner: Any,
        provider: str,
        source: str,
        connection_mode: str = "demo",
        real: bool = False,
    ) -> None:
        self.inner = inner
        self.provider = provider
        self.source = source
        self.connection_mode = connection_mode
        self.real = real

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.inner, name)

        def invoke(*args: Any, **kwargs: Any) -> Any:
            result = method(*args, **kwargs)
            if isinstance(result, dict):
                return {
                    **result,
                    "provider": self.provider,
                    "source": self.source,
                    "connection_mode": self.connection_mode,
                    "real": self.real,
                }
            return result

        return invoke


class DynamicService:
    """Refresh the adapter when the persisted connection selection changes."""

    def __init__(self, service: str, settings: Settings) -> None:
        self.service = service
        self.settings = settings
        self._mode: str | None = None
        self._inner: Any | None = None

    def __getattr__(self, name: str) -> Any:
        def invoke(*args: Any, **kwargs: Any) -> Any:
            return getattr(self._current(), name)(*args, **kwargs)

        return invoke

    def _current(self) -> Any:
        mode = ProviderModeStore(
            self.settings.database_path,
            {name: self.settings.configured_provider(name) for name in ProviderModeStore.SERVICES},
        ).get(self.service)
        if self.settings.daypilot_demo_mode:
            mode = "demo"
        if mode != self._mode or self._inner is None:
            self._inner = build_service(self.service, self.settings)
            self._mode = mode
        return self._inner


def build_dynamic_service(service: str, settings: Settings) -> DynamicService:
    return DynamicService(service, settings)


def build_service(service: str, settings: Settings) -> Any:
    mode = ProviderModeStore(
        settings.database_path,
        {name: settings.configured_provider(name) for name in ProviderModeStore.SERVICES},
    ).get(service)
    if mode == "direct":
        mode = {
            "mail": "gmail",
            "calendar": "google_calendar",
            "tasks": "google_tasks",
            "files": "local",
            "x": "x_api",
        }[service]
    if settings.daypilot_demo_mode or mode == "demo":
        return ProvenanceService(
            DemoServiceStore(settings.database_path, settings.daypilot_timezone),
            "DayPilot demo",
            "demo",
            "demo",
            False,
        )
    if mode == "unavailable":
        return UnavailableService(f"{service.title()} is unavailable in the current configuration.")
    if mode == "managed":
        if service == "mail":
            return ManagedGoogleWorkspaceService(settings)
        if service == "calendar":
            return ManagedCalendarService(settings)
        if service == "tasks":
            return ManagedTasksService(settings)
        if service == "x":
            return ManagedXService(settings)
        return UnavailableService("Local Files stays on DayPilot's local read-only adapter.")
    if service == "mail" and mode == "gmail":
        return ProvenanceService(GmailService(settings), "Gmail", "direct", "direct", True)
    if service == "calendar" and mode == "google_calendar":
        return ProvenanceService(
            GoogleCalendarService(settings), "Google Calendar", "direct", "direct", True
        )
    if service == "tasks" and mode == "google_tasks":
        return ProvenanceService(
            GoogleTasksService(settings), "Google Tasks", "direct", "direct", True
        )
    if service == "files" and mode == "local":
        return ProvenanceService(LocalFilesService(settings), "Local Mac", "direct", "local", True)
    if service == "x" and mode == "x_api":
        return ProvenanceService(ConnectedXService(settings), "X", "direct", "direct", True)
    return UnavailableService(f"No adapter is available for {service.title()} provider {mode!r}.")
