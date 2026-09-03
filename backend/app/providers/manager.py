from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.app.config import Settings
from backend.app.domain.errors import FileAccessError, OAuthError
from backend.app.domain.models import (
    ConnectionCatalog,
    FileRoot,
    OAuthStartResponse,
    ProviderConnection,
    ProviderConnectionState,
)
from backend.app.persistence.database import DatabaseTarget, connect_sync
from backend.app.persistence.repository import DayPilotRepository
from backend.app.providers.composio import MANAGED_AUTH_UNAVAILABLE, ComposioManagedClient
from backend.app.providers.credentials import EncryptedCredentialStore
from backend.app.providers.mode_store import ProviderModeStore
from backend.app.providers.models import CredentialRecord
from backend.app.providers.oauth import (
    GOOGLE_SCOPES,
    X_SCOPES,
    OAuthFlowStore,
    exchange_code,
    google_authorization_url,
    profile_for,
    x_authorization_url,
)
from backend.app.timing import timed

SERVICE_CAPABILITIES = {
    "mail": ["search_mail", "get_thread", "get_message", "create_draft"],
    "calendar": ["list_events", "find_free_slots", "create_event"],
    "tasks": ["list_tasks", "create_task", "create_task_batch", "complete_task"],
    "files": ["search_files", "list_files", "get_file_metadata", "read_file"],
    "x": ["search_posts", "get_post", "get_user_posts", "create_post_draft", "publish_post"],
}
PROVIDER_LABELS = {
    "demo": "DayPilot demo",
    "gmail": "Gmail",
    "google_calendar": "Google Calendar",
    "google_tasks": "Google Tasks",
    "local": "Local Mac",
    "x_api": "X",
    "unavailable": "Unavailable",
}
GOOGLE_REQUIRED_SCOPES = {
    "mail": {
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
    },
    "calendar": {"https://www.googleapis.com/auth/calendar.events"},
    "tasks": {"https://www.googleapis.com/auth/tasks"},
}


class ConnectionManager:
    """Owns connection metadata and OAuth state; MCP tools remain elsewhere."""

    def __init__(self, settings: Settings, repository: DayPilotRepository) -> None:
        self.settings = settings
        self.repository = repository
        self.mode_store = ProviderModeStore(
            settings.database_target,
            {
                service: settings.configured_provider(service)
                for service in ProviderModeStore.SERVICES
            },
        )
        self._credentials: EncryptedCredentialStore | None = None
        self.managed = ComposioManagedClient(settings)
        self.managed_state = self.managed.state
        self.oauth_flows = OAuthFlowStore()

    @property
    def credentials(self) -> EncryptedCredentialStore:
        """Create the direct-mode credential store only when it is needed."""
        if self._credentials is None:
            self._credentials = EncryptedCredentialStore(
                self.settings.credential_path, self.settings.credential_key_path
            )
        return self._credentials

    def catalog(self) -> ConnectionCatalog:
        return ConnectionCatalog(
            demo_mode=self.settings.daypilot_demo_mode,
            connections=[self.connection(service) for service in ProviderModeStore.SERVICES],
        )

    def public_catalog(self) -> ConnectionCatalog:
        if not self.settings.public_demo_mode or self.settings.daypilot_demo_mode:
            return self.catalog()
        return ConnectionCatalog(
            demo_mode=False,
            connections=[
                ProviderConnection(
                    service=service,
                    provider=(
                        "Google Workspace"
                        if service in {"mail", "calendar", "tasks"}
                        else service.title()
                    ),
                    state=ProviderConnectionState.UNAVAILABLE,
                    capabilities=[],
                    last_error="Available to admin only.",
                    metadata={},
                    connection_mode="managed" if service != "files" else "local",
                )
                for service in ProviderModeStore.SERVICES
            ],
        )

    def status(self, service: str) -> dict[str, Any]:
        with timed("provider.status"):
            connection = self.connection(service)
        return {
            "provider": connection.provider,
            "provider_state": connection.state.value,
            "account_label": connection.account_label,
            "requires_reauth": connection.requires_reauth,
            "last_error": connection.last_error,
            "connection_mode": connection.connection_mode,
        }

    def connection(self, service: str) -> ProviderConnection:
        mode = self.mode_store.get(service)
        if mode == "direct":
            mode = {
                "mail": "gmail",
                "calendar": "google_calendar",
                "tasks": "google_tasks",
                "files": "local",
                "x": "x_api",
            }[service]
        capabilities = SERVICE_CAPABILITIES[service]
        if self.settings.daypilot_demo_mode or mode == "demo":
            return ProviderConnection(
                service=service,
                provider="DayPilot demo",
                state=ProviderConnectionState.CONNECTED,
                account_label="Demo workspace",
                capabilities=capabilities,
                metadata={"mode": "demo"},
                connection_mode="demo",
            )
        if mode == "unavailable":
            return ProviderConnection(
                service=service,
                provider="Unavailable",
                state=ProviderConnectionState.UNAVAILABLE,
                capabilities=capabilities,
                last_error="This capability is disabled in the current configuration.",
                metadata={"mode": mode},
                connection_mode="direct",
            )
        if mode == "managed":
            if service in {"mail", "calendar", "tasks"}:
                return self._managed_google_connection(service, capabilities)
            if service == "x":
                return self._managed_x_connection(capabilities)
            return self._files_connection(capabilities)
        if service in {"mail", "calendar", "tasks"}:
            return self._google_connection(service, mode, capabilities)
        if service == "x":
            return self._x_connection(capabilities)
        return self._files_connection(capabilities)

    def start_google(self) -> OAuthStartResponse:
        if self.settings.daypilot_demo_mode:
            raise OAuthError("Connected mode is disabled while DAYPILOT_DEMO_MODE is enabled.")
        if any(
            self.mode_store.get(service) == "managed" for service in ("mail", "calendar", "tasks")
        ):
            return self._start_managed("google", self.settings.composio_google_toolkit)
        if not self.settings.google_client_id:
            raise OAuthError("GOOGLE_CLIENT_ID is not configured on the backend.")
        flow = self.oauth_flows.create("google")
        return OAuthStartResponse(
            provider="google",
            authorization_url=google_authorization_url(
                self.settings.google_client_id, self.settings.google_redirect_uri, flow
            ),
            scopes=list(GOOGLE_SCOPES),
            mode="direct",
        )

    def _start_managed(self, provider: str, toolkit: str) -> OAuthStartResponse:
        if toolkit in MANAGED_AUTH_UNAVAILABLE:
            raise OAuthError("Managed connection is currently unavailable for X.")
        callback = f"{self.settings.composio_callback_url}?provider={provider}"
        authorization = self.managed.authorize(toolkit, callback)
        return OAuthStartResponse(
            provider="google" if provider == "google" else "x",
            authorization_url=authorization.redirect_url,
            scopes=[],
            mode="managed",
        )

    async def complete_google(
        self, code: str | None, state: str | None, error: str | None = None
    ) -> None:
        if error:
            raise OAuthError(f"Google authorization was cancelled: {error}.")
        if not code or not state:
            raise OAuthError("Google did not return an authorization code.")
        flow = self.oauth_flows.consume("google", state)
        if not self.settings.google_client_id:
            raise OAuthError("GOOGLE_CLIENT_ID is not configured on the backend.")
        payload = await exchange_code(
            provider="google",
            code=code,
            verifier=flow.verifier,
            client_id=self.settings.google_client_id,
            client_secret=self.settings.google_client_secret,
            redirect_uri=self.settings.google_redirect_uri,
        )
        access_token = str(payload["access_token"])
        profile = await profile_for("google", access_token)
        previous = self.credentials.get("google")
        scopes = tuple(str(payload.get("scope", "")).split()) or tuple(GOOGLE_SCOPES)
        missing = set().union(
            *(GOOGLE_REQUIRED_SCOPES[service] for service in GOOGLE_REQUIRED_SCOPES)
        ) - set(scopes)
        if missing:
            raise OAuthError(
                "Google authorization did not grant the required Gmail, Calendar, and Tasks scopes."
            )
        self.credentials.set(
            CredentialRecord(
                provider="google",
                access_token=access_token,
                refresh_token=payload.get("refresh_token")
                or (previous.refresh_token if previous else None),
                expires_at=datetime.now().timestamp() + float(payload["expires_in"])
                if payload.get("expires_in")
                else None,
                scopes=scopes,
                account_label=profile.get("email"),
                metadata={"subject": profile.get("sub")},
            )
        )
        for service, mode in (
            ("mail", "gmail"),
            ("calendar", "google_calendar"),
            ("tasks", "google_tasks"),
        ):
            await self.repository.set_provider_mode(service, mode)

    async def complete_managed(
        self,
        provider: str,
        status: str | None,
        account_id: str | None,
        error: str | None = None,
    ) -> None:
        if self.settings.daypilot_demo_mode:
            raise OAuthError(
                "Managed connections are disabled while DAYPILOT_DEMO_MODE is enabled."
            )
        if provider == "google":
            toolkit = self.settings.composio_google_toolkit
        elif provider == "x":
            toolkit = self.settings.composio_x_toolkit
        else:
            raise OAuthError("Unsupported managed provider.")
        await asyncio.to_thread(self.managed.complete, toolkit, account_id, status, error)
        if provider == "google":
            for service in ("mail", "calendar", "tasks"):
                await self.repository.set_provider_mode(service, "managed")
        else:
            await self.repository.set_provider_mode("x", "managed")

    async def disconnect_google(self) -> None:
        if any(
            self.mode_store.get(service) == "managed" for service in ("mail", "calendar", "tasks")
        ):
            await asyncio.to_thread(self.managed.disconnect, self.settings.composio_google_toolkit)
            for service in ("mail", "calendar", "tasks"):
                await self.repository.set_provider_mode(service, "managed")
            return
        self.credentials.delete("google")
        for service, mode in (
            ("mail", "gmail"),
            ("calendar", "google_calendar"),
            ("tasks", "google_tasks"),
        ):
            await self.repository.set_provider_mode(service, mode)

    def start_x(self) -> OAuthStartResponse:
        if self.settings.daypilot_demo_mode:
            raise OAuthError("Connected mode is disabled while DAYPILOT_DEMO_MODE is enabled.")
        if self.mode_store.get("x") == "managed":
            return self._start_managed("x", self.settings.composio_x_toolkit)
        if not self.settings.x_client_id:
            raise OAuthError("X_CLIENT_ID is not configured on the backend.")
        flow = self.oauth_flows.create("x")
        return OAuthStartResponse(
            provider="x",
            authorization_url=x_authorization_url(
                self.settings.x_client_id, self.settings.x_redirect_uri, flow
            ),
            scopes=list(X_SCOPES),
            mode="direct",
        )

    async def complete_x(
        self, code: str | None, state: str | None, error: str | None = None
    ) -> None:
        if error:
            raise OAuthError(f"X authorization was cancelled: {error}.")
        if not code or not state:
            raise OAuthError("X did not return an authorization code.")
        flow = self.oauth_flows.consume("x", state)
        if not self.settings.x_client_id:
            raise OAuthError("X_CLIENT_ID is not configured on the backend.")
        payload = await exchange_code(
            provider="x",
            code=code,
            verifier=flow.verifier,
            client_id=self.settings.x_client_id,
            client_secret=self.settings.x_client_secret,
            redirect_uri=self.settings.x_redirect_uri,
        )
        access_token = str(payload["access_token"])
        profile = await profile_for("x", access_token)
        previous = self.credentials.get("x")
        scopes = tuple(str(payload.get("scope", "")).split()) or tuple(X_SCOPES)
        if not set(X_SCOPES).issubset(scopes):
            raise OAuthError(
                "X authorization did not grant the read, publish, and offline-access scopes "
                "required by DayPilot."
            )
        self.credentials.set(
            CredentialRecord(
                provider="x",
                access_token=access_token,
                refresh_token=payload.get("refresh_token")
                or (previous.refresh_token if previous else None),
                expires_at=datetime.now().timestamp() + float(payload["expires_in"])
                if payload.get("expires_in")
                else None,
                scopes=scopes,
                account_label=f"@{profile.get('username')}"
                if profile.get("username")
                else profile.get("name"),
                metadata={"id": profile.get("id")},
            )
        )
        await self.repository.set_provider_mode("x", "x_api")

    async def disconnect_x(self) -> None:
        if self.mode_store.get("x") == "managed":
            await asyncio.to_thread(self.managed.disconnect, self.settings.composio_x_toolkit)
            await self.repository.set_provider_mode("x", "managed")
            return
        self.credentials.delete("x")
        await self.repository.set_provider_mode("x", "x_api")

    async def list_file_roots(self) -> list[FileRoot]:
        return await self.repository.list_file_roots()

    async def add_file_root(self, raw_path: str) -> FileRoot:
        if self.settings.daypilot_demo_mode:
            raise FileAccessError("Connected mode is disabled while DAYPILOT_DEMO_MODE is enabled.")
        path = _validate_root(raw_path)
        root_id = hashlib.sha256(str(path).encode()).hexdigest()[:20]
        try:
            root = await self.repository.add_file_root(root_id, str(path), path.name or str(path))
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise FileAccessError("That local folder is already connected.") from exc
            raise
        await self.repository.set_provider_mode("files", "local")
        return root

    async def remove_file_root(self, root_id: str) -> None:
        await self.repository.remove_file_root(root_id)
        roots = await self.repository.list_file_roots()
        if not roots:
            await self.repository.set_provider_mode("files", "local")

    def _google_connection(
        self, service: str, mode: str, capabilities: list[str]
    ) -> ProviderConnection:
        credential = self.credentials.get("google")
        provider = PROVIDER_LABELS.get(mode, mode)
        if credential is None:
            return ProviderConnection(
                service=service,
                provider=provider,
                state=ProviderConnectionState.DISCONNECTED,
                capabilities=capabilities,
                last_error="Connect Google Workspace to use this capability.",
                metadata={"mode": mode},
                connection_mode="direct",
            )
        if credential.last_error:
            return ProviderConnection(
                service=service,
                provider=provider,
                state=ProviderConnectionState.RECONNECT_REQUIRED,
                account_label=credential.account_label,
                capabilities=capabilities,
                last_error=credential.last_error,
                requires_reauth=True,
                metadata={"mode": mode},
                connection_mode="direct",
            )
        missing = GOOGLE_REQUIRED_SCOPES[service] - set(credential.scopes)
        if missing:
            return ProviderConnection(
                service=service,
                provider=provider,
                state=ProviderConnectionState.RECONNECT_REQUIRED,
                account_label=credential.account_label,
                capabilities=capabilities,
                last_error="Reconnect Google Workspace to grant the required scope.",
                requires_reauth=True,
                metadata={"mode": mode},
                connection_mode="direct",
            )
        return ProviderConnection(
            service=service,
            provider=provider,
            state=ProviderConnectionState.CONNECTED,
            account_label=credential.account_label,
            capabilities=capabilities,
            metadata={"mode": mode},
            connection_mode="direct",
        )

    def _managed_google_connection(
        self, service: str, capabilities: list[str]
    ) -> ProviderConnection:
        return self._managed_connection(
            service,
            self.settings.composio_google_toolkit,
            "Google Workspace",
            capabilities,
        )

    def _managed_x_connection(self, capabilities: list[str]) -> ProviderConnection:
        return self._managed_connection(
            "x",
            self.settings.composio_x_toolkit,
            "X",
            capabilities,
        )

    def _managed_connection(
        self,
        service: str,
        toolkit: str,
        provider: str,
        capabilities: list[str],
    ) -> ProviderConnection:
        if toolkit in MANAGED_AUTH_UNAVAILABLE:
            return ProviderConnection(
                service=service,
                provider=provider,
                state=ProviderConnectionState.UNAVAILABLE,
                capabilities=capabilities,
                last_error="Managed connection is currently unavailable for X.",
                metadata={"mode": "managed", "toolkit": toolkit},
                connection_mode="managed",
            )
        if not self.settings.composio_api_key:
            return ProviderConnection(
                service=service,
                provider=provider,
                state=ProviderConnectionState.UNAVAILABLE,
                capabilities=capabilities,
                last_error="Managed connections require COMPOSIO_API_KEY on the backend.",
                metadata={"mode": "managed", "toolkit": toolkit},
                connection_mode="managed",
            )
        account = self.managed_state.account(toolkit)
        if not account:
            return ProviderConnection(
                service=service,
                provider=provider,
                state=ProviderConnectionState.DISCONNECTED,
                capabilities=capabilities,
                last_error=f"Connect {provider} through Composio to use this capability.",
                metadata={"mode": "managed", "toolkit": toolkit},
                connection_mode="managed",
            )
        status = str(account.get("status") or "").upper()
        if status in {"INITIATED", "INITIALIZING"} and _managed_link_expired(account):
            status = "EXPIRED"
        state = {
            "ACTIVE": ProviderConnectionState.CONNECTED,
            "INITIATED": ProviderConnectionState.CONNECTING,
            "INITIALIZING": ProviderConnectionState.CONNECTING,
            "EXPIRED": ProviderConnectionState.RECONNECT_REQUIRED,
            "REVOKED": ProviderConnectionState.RECONNECT_REQUIRED,
            "FAILED": ProviderConnectionState.RECONNECT_REQUIRED,
            "INACTIVE": ProviderConnectionState.ERROR,
        }.get(status, ProviderConnectionState.DISCONNECTED)
        requires_reauth = status in {"EXPIRED", "REVOKED", "FAILED"}
        return ProviderConnection(
            service=service,
            provider=provider,
            state=state,
            account_label=account.get("account_label"),
            capabilities=capabilities,
            last_error=(f"Reconnect {provider}." if requires_reauth else None),
            requires_reauth=requires_reauth,
            metadata={"mode": "managed", "toolkit": toolkit},
            connection_mode="managed",
        )

    def _x_connection(self, capabilities: list[str]) -> ProviderConnection:
        credential = self.credentials.get("x")
        if credential is None:
            return ProviderConnection(
                service="x",
                provider="X",
                state=ProviderConnectionState.DISCONNECTED,
                capabilities=capabilities,
                last_error="Connect your X account to use this capability.",
                metadata={"mode": "x_api"},
                connection_mode="direct",
            )
        if credential.last_error:
            return ProviderConnection(
                service="x",
                provider="X",
                state=ProviderConnectionState.RECONNECT_REQUIRED,
                account_label=credential.account_label,
                capabilities=capabilities,
                last_error=credential.last_error,
                requires_reauth=True,
                metadata={"mode": "x_api"},
                connection_mode="direct",
            )
        missing = set(X_SCOPES) - set(credential.scopes)
        if missing:
            return ProviderConnection(
                service="x",
                provider="X",
                state=ProviderConnectionState.RECONNECT_REQUIRED,
                account_label=credential.account_label,
                capabilities=capabilities,
                last_error="Reconnect X to grant the read, publish, and offline-access scopes.",
                requires_reauth=True,
                metadata={"mode": "x_api"},
                connection_mode="direct",
            )
        return ProviderConnection(
            service="x",
            provider="X",
            state=ProviderConnectionState.CONNECTED,
            account_label=credential.account_label,
            capabilities=capabilities,
            metadata={"mode": "x_api"},
            connection_mode="direct",
        )

    def _files_connection(self, capabilities: list[str]) -> ProviderConnection:
        roots = _read_roots(self.settings.database_target)
        existing = [root for root in roots if Path(root["path"]).is_dir()]
        if existing and len(existing) == len(roots):
            state = ProviderConnectionState.CONNECTED
            error = None
        elif existing:
            state = ProviderConnectionState.ERROR
            error = "One or more configured local folders is missing or no longer readable."
        elif roots:
            state = ProviderConnectionState.ERROR
            error = "A configured local folder is missing or no longer readable."
        else:
            state = ProviderConnectionState.DISCONNECTED
            error = "Add an allowlisted local folder in Preferences."
        return ProviderConnection(
            service="files",
            provider="Local Mac",
            state=state,
            account_label=f"{len(existing)} folder{'s' if len(existing) != 1 else ''}",
            capabilities=capabilities,
            last_error=error,
            metadata={"mode": "local", "root_count": len(existing)},
            connection_mode="local",
        )


def _validate_root(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise FileAccessError("Folder paths must be absolute or start with ~.")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise FileAccessError("That local folder does not exist or cannot be read.") from exc
    if not resolved.is_dir():
        raise FileAccessError("The selected local path is not a folder.")
    if resolved == Path("/"):
        raise FileAccessError("The filesystem root cannot be connected.")
    return resolved


def _read_roots(database_target: DatabaseTarget) -> list[dict[str, str]]:
    try:
        with connect_sync(database_target) as connection:
            return [
                dict(row) for row in connection.execute("SELECT id, path, label FROM file_roots")
            ]
    except Exception:
        return []


def _managed_link_expired(account: dict[str, Any]) -> bool:
    raw = account.get("updated_at")
    if not raw:
        return False
    try:
        updated = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return datetime.now(UTC) - updated > timedelta(minutes=10)
