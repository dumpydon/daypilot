from __future__ import annotations

import base64
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from backend.app.config import Settings
from backend.app.domain.errors import FileAccessError, OAuthError, ProviderUnavailableError
from backend.app.graph.workflow import _verify_result
from backend.app.mcp.gateway import MCPGateway
from backend.app.persistence.repository import DayPilotRepository
from backend.app.providers.composio import (
    GOOGLE_TOOLKIT,
    MANAGED_TOOL_SLUGS,
    X_TOOLKIT,
    ComposioManagedClient,
    ManagedCalendarService,
    ManagedGoogleWorkspaceService,
    ManagedTasksService,
    ManagedXService,
    _unwrap_result,
)
from backend.app.providers.credentials import EncryptedCredentialStore
from backend.app.providers.factory import build_dynamic_service
from backend.app.providers.google import GmailService, GoogleCalendarService, GoogleTasksService
from backend.app.providers.local_files import LocalFilesService
from backend.app.providers.managed_state import ManagedStateStore
from backend.app.providers.manager import ConnectionManager
from backend.app.providers.models import CredentialRecord
from backend.app.providers.oauth import GOOGLE_SCOPES, OAuthFlowStore, google_authorization_url
from backend.app.providers.x_api import ConnectedXService
from mcp_servers.common.database import ensure_demo_database_schema


def settings_for(tmp_path: Path, **overrides: object) -> Settings:
    values = {
        "_env_file": None,
        "database_url": f"sqlite:///{tmp_path / 'provider.db'}",
        "daypilot_demo_mode": False,
        "mail_provider": "gmail",
        "calendar_provider": "google_calendar",
        "tasks_provider": "google_tasks",
        "files_provider": "local",
        "x_provider": "x_api",
        "google_client_id": "google-client",
        "google_client_secret": "google-secret",
        "x_client_id": "x-client",
        "x_client_secret": "x-secret",
        "credential_store_path": str(tmp_path / "credentials.enc"),
        "credential_key_file": str(tmp_path / "credentials.key"),
    }
    values.update(overrides)
    return Settings(**values)


def test_connected_defaults_use_managed_mode_without_provider_oauth_values(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'default.db'}",
        daypilot_demo_mode=False,
        composio_api_key="composio-test-key",
    )

    assert settings.provider_mode == "managed"
    assert {
        service: settings.configured_provider(service)
        for service in ("mail", "calendar", "tasks", "x")
    } == {
        "mail": "managed",
        "calendar": "managed",
        "tasks": "managed",
        "x": "managed",
    }
    assert settings.configured_provider("files") == "local"
    assert "composio-test-key" in settings.mcp_environment()["COMPOSIO_API_KEY"]
    assert "COMPOSIO_BASE_URL" not in settings.mcp_environment()


async def initialized_repository(settings: Settings) -> DayPilotRepository:
    ensure_demo_database_schema(settings.database_path)
    repository = DayPilotRepository(settings.database_path)
    await repository.initialize()
    await repository.ensure_provider_modes(
        {
            service: settings.configured_provider(service)
            for service in ("mail", "calendar", "tasks", "files", "x")
        }
    )
    return repository


def save_google(settings: Settings, *, scopes: tuple[str, ...] = GOOGLE_SCOPES) -> None:
    EncryptedCredentialStore(settings.credential_path, settings.credential_key_path).set(
        CredentialRecord(
            provider="google",
            access_token="google-access-token",
            refresh_token="google-refresh-token",
            expires_at=None,
            scopes=scopes,
            account_label="alex@example.com",
        )
    )


def save_x(settings: Settings) -> None:
    EncryptedCredentialStore(settings.credential_path, settings.credential_key_path).set(
        CredentialRecord(
            provider="x",
            access_token="x-access-token",
            refresh_token="x-refresh-token",
            expires_at=None,
            scopes=("tweet.read", "tweet.write", "users.read", "offline.access"),
            account_label="@alex",
        )
    )


def response_for(request: httpx.Request) -> httpx.Response:
    if request.url.host == "oauth2.googleapis.com":
        return httpx.Response(200, json={"access_token": "refreshed-google", "expires_in": 3600})
    if request.url.host == "gmail.googleapis.com":
        if request.method == "GET" and request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "msg-1"}]})
        if request.method == "GET" and request.url.path.endswith("/messages/msg-1"):
            return httpx.Response(
                200,
                json={
                    "id": "msg-1",
                    "threadId": "thread-1",
                    "snippet": "Interview confirmed",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "rahul@example.com"},
                            {"name": "To", "value": "alex@example.com"},
                            {"name": "Subject", "value": "Interview confirmed"},
                            {"name": "Date", "value": "Thu, 28 Aug 2026 11:00:00 +0530"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": base64.urlsafe_b64encode(b"See you tomorrow").decode()},
                    },
                },
            )
        if request.method == "GET" and request.url.path.endswith("/threads/thread-1"):
            return httpx.Response(
                200,
                json={
                    "id": "thread-1",
                    "messages": [
                        {
                            "id": "msg-1",
                            "threadId": "thread-1",
                            "payload": {
                                "headers": [
                                    {"name": "From", "value": "rahul@example.com"},
                                    {"name": "Subject", "value": "Interview confirmed"},
                                ],
                                "mimeType": "text/plain",
                                "body": {
                                    "data": base64.urlsafe_b64encode(b"See you tomorrow").decode()
                                },
                            },
                        }
                    ],
                },
            )
        if request.method == "POST" and request.url.path.endswith("/drafts"):
            body = request.read()
            assert b"raw" in body
            return httpx.Response(200, json={"id": "draft-1", "message": {"id": "msg-draft-1"}})
    if request.url.host == "www.googleapis.com":
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "event-1",
                            "summary": "Team sync",
                            "start": {"dateTime": "2026-08-28T09:00:00+05:30"},
                            "end": {"dateTime": "2026-08-28T10:00:00+05:30"},
                            "htmlLink": "https://calendar.google.com/event-1",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "event-created",
                "summary": "Deep Work",
                "start": {"dateTime": "2026-08-28T15:00:00+05:30"},
                "end": {"dateTime": "2026-08-28T15:30:00+05:30"},
                "htmlLink": "https://calendar.google.com/event-created",
            },
        )
    if request.url.host == "tasks.googleapis.com":
        if request.url.path.endswith("/users/@me/lists"):
            return httpx.Response(200, json={"items": [{"id": "list-1", "title": "My tasks"}]})
        if request.method == "GET":
            return httpx.Response(
                200, json={"items": [{"id": "task-1", "title": "Review", "status": "needsAction"}]}
            )
        if request.method == "PATCH":
            return httpx.Response(
                200, json={"id": "task-1", "title": "Review", "status": "completed"}
            )
        return httpx.Response(
            200, json={"id": "task-created", "title": "New task", "status": "needsAction"}
        )
    if request.url.host == "api.x.com":
        if request.url.path.endswith("/tweets/search/recent"):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "post-1", "text": "MCP update", "author_id": "user-1"}],
                    "includes": {"users": [{"id": "user-1", "username": "alex", "name": "Alex"}]},
                },
            )
        if request.url.path.endswith("/users/me"):
            return httpx.Response(
                200, json={"data": {"id": "user-1", "username": "alex", "name": "Alex"}}
            )
        if request.method == "POST":
            return httpx.Response(
                200, json={"data": {"id": "post-created", "text": "DayPilot connected"}}
            )
        return httpx.Response(
            200, json={"data": {"id": "post-1", "text": "MCP update", "author_id": "user-1"}}
        )
    return httpx.Response(404)


def test_encrypted_credentials_never_store_plaintext_token(tmp_path: Path) -> None:
    path = tmp_path / "credentials.enc"
    store = EncryptedCredentialStore(path, tmp_path / "credentials.key")
    store.set(CredentialRecord("google", "super-secret-access", "refresh", None, ("scope",)))

    assert b"super-secret-access" not in path.read_bytes()
    assert store.get("google").access_token == "super-secret-access"
    assert oct((tmp_path / "credentials.key").stat().st_mode & 0o777) == "0o600"


def test_oauth_url_uses_state_and_s256_pkce() -> None:
    flows = OAuthFlowStore()
    flow = flows.create("google")
    parsed = parse_qs(
        urlparse(google_authorization_url("client", "http://localhost/callback", flow)).query
    )
    assert parsed["state"] == [flow.state]
    assert parsed["code_challenge_method"] == ["S256"]
    assert set(parsed["scope"][0].split()) == set(GOOGLE_SCOPES)
    with pytest.raises(OAuthError):
        flows.consume("google", "wrong-state")


@pytest.mark.asyncio
async def test_demo_mode_rejects_real_account_connection(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, daypilot_demo_mode=True)
    repository = await initialized_repository(settings)
    manager = ConnectionManager(settings, repository)

    with pytest.raises(OAuthError, match="DAYPILOT_DEMO_MODE"):
        manager.start_google()
    with pytest.raises(FileAccessError, match="DAYPILOT_DEMO_MODE"):
        await manager.add_file_root(str(tmp_path))


@pytest.mark.asyncio
async def test_file_root_registration_rejects_filesystem_root(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    repository = await initialized_repository(settings)
    manager = ConnectionManager(settings, repository)

    with pytest.raises(FileAccessError, match="filesystem root"):
        await manager.add_file_root("/")


@pytest.mark.asyncio
async def test_file_root_registration_persists_a_canonical_folder(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    repository = await initialized_repository(settings)
    manager = ConnectionManager(settings, repository)
    root = tmp_path / "notes"
    root.mkdir()

    added = await manager.add_file_root(str(root / ".." / "notes"))

    assert added.exists is True
    assert Path(added.path) == root.resolve()
    assert (await manager.list_file_roots())[0].id == added.id


def test_connected_google_services_normalize_real_api_payloads(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    save_google(settings)
    transport = httpx.MockTransport(response_for)

    mail = GmailService(settings, transport)
    calendar = GoogleCalendarService(settings, transport)
    tasks = GoogleTasksService(settings, transport)

    assert mail.search_mail("interview")["threads"][0]["provider"] == "Gmail"
    assert mail.get_thread("thread-1")["messages"][0]["sender"] == "rahul@example.com"
    assert "See you tomorrow" in mail.get_message("msg-1")["body"]
    draft = mail.create_draft("rahul@example.com", "Thanks", "Draft")
    assert draft["status"] == "created"
    assert draft["id"] == "draft-1"
    assert draft["message_id"] == "msg-draft-1"
    assert (
        calendar.list_events("2026-08-28T08:00:00+05:30", "2026-08-28T12:00:00+05:30")["events"][0][
            "id"
        ]
        == "event-1"
    )
    assert calendar.create_event(
        "Deep Work", "2026-08-28T15:00:00+05:30", "2026-08-28T15:30:00+05:30"
    )["external_url"]
    assert tasks.list_tasks()["tasks"][0]["id"] == "task-1"
    assert tasks.create_task("New task")["provider"] == "Google Tasks"
    assert tasks.complete_task("task-1")["completed"] is True


def test_google_access_token_refresh_is_persisted_without_falling_back(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    store = EncryptedCredentialStore(settings.credential_path, settings.credential_key_path)
    store.set(
        CredentialRecord(
            provider="google",
            access_token="expired-google",
            refresh_token="google-refresh",
            expires_at=0,
            scopes=GOOGLE_SCOPES,
        )
    )

    result = GmailService(settings, httpx.MockTransport(response_for)).search_mail("interview")

    assert result["provider"] == "Gmail"
    assert store.get("google").access_token == "refreshed-google"


def test_connected_x_reads_publish_and_local_draft_are_distinct(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    save_x(settings)
    service = ConnectedXService(settings, httpx.MockTransport(response_for))

    assert service.search_posts("MCP")["posts"][0]["provider"] == "X"
    draft = service.create_post_draft("DayPilot connected mode")
    assert draft["provider"] == "DayPilot X draft"
    published = service.publish_post(draft["text"], draft["id"])
    assert published["external_url"].endswith("post-created")


def test_connected_x_draft_requires_an_authorized_identity(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    service = ConnectedXService(settings, httpx.MockTransport(response_for))

    with pytest.raises(ProviderUnavailableError, match="not connected"):
        service.create_post_draft("DayPilot connected mode")


def test_connected_provider_never_falls_back_to_demo_without_credentials(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with pytest.raises(ProviderUnavailableError, match="not connected"):
        GmailService(settings, httpx.MockTransport(response_for)).search_mail("anything")


def test_managed_provider_requires_composio_key_without_demo_fallback(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'managed.db'}",
        daypilot_demo_mode=False,
        provider_mode="managed",
    )
    with pytest.raises(ProviderUnavailableError, match="COMPOSIO_API_KEY"):
        ManagedGoogleWorkspaceService(settings).search_mail("anything")


def test_mcp_service_selection_stays_dynamic_and_fail_closed(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    ensure_demo_database_schema(settings.database_path)
    with pytest.raises(ProviderUnavailableError, match="not connected"):
        build_dynamic_service("mail", settings).search_mail("anything")


@pytest.mark.asyncio
async def test_connected_mode_keeps_all_semantic_mcp_tools_discoverable(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    repository = await initialized_repository(settings)
    gateway = MCPGateway(settings)

    tools = await gateway.discover(force=True)

    assert len(tools) == 21
    catalog = {server["name"]: server for server in gateway.catalog()}
    assert all(catalog[name]["connected"] for name in ("mail", "calendar", "tasks", "files", "x"))
    assert catalog["web"]["provider_state"] == "unavailable"
    assert (await repository.list_provider_modes())["mail"] == "gmail"


@pytest.mark.asyncio
async def test_google_callback_persists_one_account_without_exposing_tokens(
    tmp_path: Path, monkeypatch
) -> None:
    settings = settings_for(tmp_path)
    repository = await initialized_repository(settings)
    manager = ConnectionManager(settings, repository)
    start = manager.start_google()
    state = parse_qs(urlparse(start.authorization_url).query)["state"][0]

    async def exchange(**_kwargs):
        return {
            "access_token": "google-access",
            "refresh_token": "google-refresh",
            "expires_in": 3600,
            "scope": " ".join(GOOGLE_SCOPES),
        }

    async def profile(*_args, **_kwargs):
        return {"email": "alex@example.com", "sub": "subject-1"}

    monkeypatch.setattr("backend.app.providers.manager.exchange_code", exchange)
    monkeypatch.setattr("backend.app.providers.manager.profile_for", profile)
    await manager.complete_google("auth-code", state)

    catalog = manager.catalog()
    assert all(item.state.value == "connected" for item in catalog.connections[:3])
    assert all("google-access" not in item.model_dump_json() for item in catalog.connections)
    assert await repository.list_provider_modes() == {
        "mail": "gmail",
        "calendar": "google_calendar",
        "tasks": "google_tasks",
        "files": "local",
        "x": "x_api",
    }
    await manager.disconnect_google()
    assert manager.credentials.get("google") is None
    assert not settings.credential_path.exists()
    assert all(item.state.value == "disconnected" for item in manager.catalog().connections[:3])


def test_local_files_allowlist_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "notes.md").write_text("LangGraph grounded notes", encoding="utf-8")
    unsupported = root / "archive.bin"
    unsupported.write_bytes(b"binary")
    oversized = root / "large.txt"
    oversized.write_text("x" * 64, encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret outside", encoding="utf-8")
    os.symlink(outside, root / "escape.txt")
    (root / ".env").write_text("SECRET=never", encoding="utf-8")
    settings = settings_for(tmp_path, local_file_max_bytes=32)
    ensure_demo_database_schema(settings.database_path)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "CREATE TABLE provider_modes(service TEXT PRIMARY KEY, mode TEXT, updated_at TEXT)"
        )
        connection.execute(
            "CREATE TABLE file_roots("
            "id TEXT PRIMARY KEY, path TEXT UNIQUE, label TEXT, added_at TEXT)"
        )
        connection.execute(
            "INSERT INTO file_roots VALUES ('root-1', ?, 'allowed', datetime('now'))", (str(root),)
        )
        connection.commit()
    service = LocalFilesService(settings)
    files = service.search_files("LangGraph")
    assert files["count"] == 1
    file_id = files["files"][0]["id"]
    assert "grounded notes" in service.read_file(file_id)["content"]
    with pytest.raises(FileAccessError):
        service.read_file("local:" + "0" * 16)
    unsupported_id = service._file_id(root, unsupported.relative_to(root))
    with pytest.raises(FileAccessError, match="Unsupported"):
        service.read_file(unsupported_id)
    oversized_id = service._file_id(root, oversized.relative_to(root))
    with pytest.raises(FileAccessError, match="larger"):
        service.read_file(oversized_id)


class _FakeConnectionRequest:
    id = "ca-managed"
    redirect_url = "https://connect.composio.dev/link/test"


class _FakeAccount:
    status = "ACTIVE"
    alias = "alex@example.com"
    data: ClassVar[dict[str, str]] = {"email": "alex@example.com"}


class _FakeSession:
    session_id = "session-managed"
    mcp = type(
        "MCP",
        (),
        {
            "url": "https://backend.composio.dev/mcp/session",
            "type": "HTTP",
            "headers": {"x-api-key": "secret"},
        },
    )()

    def authorize(self, toolkit: str, *, callback_url: str):
        assert toolkit == GOOGLE_TOOLKIT
        assert "provider=google" in callback_url
        return _FakeConnectionRequest()


class _FakeSessions:
    def __init__(self):
        self.create_count = 0
        self.use_count = 0

    def create(self, **kwargs):
        self.create_count += 1
        self.created = kwargs
        return _FakeSession()

    def use(self, session_id: str, *, mcp: bool):
        self.use_count += 1
        assert session_id == "session-managed"
        assert mcp is True
        return _FakeSession()


class _FakeAccounts:
    def get(self, account_id: str):
        assert account_id == "ca-managed"
        return _FakeAccount()

    def delete(self, account_id: str, **kwargs):
        assert account_id == "ca-managed"
        assert kwargs.get("revoke_on_delete") is True


class _FakeComposio:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.sessions = _FakeSessions()
        self.connected_accounts = _FakeAccounts()


class _FakeMCPTool:
    def __init__(self, name: str, value: dict):
        self.name = name
        self.value = value

    async def ainvoke(self, arguments):
        self.arguments = arguments
        return {"data": self.value, "successful": True, "error": None}


class _FakeMCPClient:
    def __init__(self, connections, **kwargs):
        assert connections["composio"]["transport"] == "http"
        assert connections["composio"]["headers"] == {"x-api-key": "secret"}
        assert kwargs["handle_tool_errors"] is False

    async def get_tools(self, *, server_name: str):
        assert server_name == "composio"
        return [_FakeMCPTool("GOOGLESUPER_FETCH_EMAILS", {"messages": []})]


def test_managed_composio_session_is_curated_and_auth_is_server_side(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, composio_api_key="composio-secret")
    state = ManagedStateStore(settings.database_path)
    client = ComposioManagedClient(
        settings,
        state=state,
        composio_factory=_FakeComposio,
        mcp_client_factory=_FakeMCPClient,
    )

    auth = client.authorize(
        GOOGLE_TOOLKIT, "http://localhost:8000/api/connections/managed/callback?provider=google"
    )
    assert auth.redirect_url.startswith("https://connect.composio.dev/")
    fake = client._client_instance
    assert fake.sessions.created["toolkits"] == [GOOGLE_TOOLKIT]
    assert set(fake.sessions.created["tools"]) == {GOOGLE_TOOLKIT}
    assert fake.sessions.created["tools"][GOOGLE_TOOLKIT]["enable"] == list(
        MANAGED_TOOL_SLUGS[GOOGLE_TOOLKIT]
    )
    assert state.account(GOOGLE_TOOLKIT)["status"] == "INITIATED"
    assert state.session(GOOGLE_TOOLKIT)["session_id"] == "session-managed"
    assert state.session(X_TOOLKIT) is None

    completed = client.complete(GOOGLE_TOOLKIT, "ca-managed", "success")
    assert completed["account_label"] == "alex@example.com"
    assert state.account(GOOGLE_TOOLKIT)["status"] == "ACTIVE"
    assert state.session(GOOGLE_TOOLKIT) is None
    assert "composio-secret" not in str(completed)

    payload = client.execute(GOOGLE_TOOLKIT, "GOOGLESUPER_FETCH_EMAILS", {"query": "hello"})
    assert payload == {"messages": []}
    assert state.session(GOOGLE_TOOLKIT)["session_id"] == "session-managed"
    assert fake.sessions.created["connected_accounts"] == {GOOGLE_TOOLKIT: ["ca-managed"]}
    client.disconnect(GOOGLE_TOOLKIT)
    assert state.account(GOOGLE_TOOLKIT) is None


def test_managed_composio_reuses_session_and_tool_catalog(tmp_path: Path) -> None:
    class CountingMCPClient(_FakeMCPClient):
        calls = 0

        async def get_tools(self, *, server_name: str):
            type(self).calls += 1
            return await super().get_tools(server_name=server_name)

    settings = settings_for(tmp_path, composio_api_key="composio-secret")
    state = ManagedStateStore(settings.database_path)
    state.set_session(GOOGLE_TOOLKIT, "session-managed", "daypilot-test")
    client = ComposioManagedClient(
        settings,
        state=state,
        composio_factory=_FakeComposio,
        mcp_client_factory=CountingMCPClient,
    )

    assert client.execute(GOOGLE_TOOLKIT, "GOOGLESUPER_FETCH_EMAILS", {"query": "first"}) == {
        "messages": []
    }
    assert client.execute(GOOGLE_TOOLKIT, "GOOGLESUPER_FETCH_EMAILS", {"query": "second"}) == {
        "messages": []
    }

    fake = client._client_instance
    assert fake.sessions.use_count == 1
    assert CountingMCPClient.calls == 1


def test_managed_sessions_have_independent_toolkit_failure_boundaries(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    import backend.app.providers.composio as composio_module

    class Session:
        def __init__(self, toolkit: str):
            self.session_id = f"session-{toolkit}"

    class Sessions:
        def create(self, **kwargs):
            toolkit = kwargs["toolkits"][0]
            if toolkit == GOOGLE_TOOLKIT:
                raise RuntimeError("google auth config failed with internal response details")
            return Session(toolkit)

        def use(self, session_id: str, *, mcp: bool):
            assert mcp is True
            return Session(session_id.removeprefix("session-"))

    class IndependentComposio:
        def __init__(self, **_kwargs):
            self.sessions = Sessions()

    monkeypatch.setattr(composio_module, "MANAGED_AUTH_UNAVAILABLE", frozenset())
    settings = settings_for(tmp_path, composio_api_key="composio-secret")
    state = ManagedStateStore(settings.database_path)
    client = ComposioManagedClient(settings, state=state, composio_factory=IndependentComposio)

    with pytest.raises(ProviderUnavailableError, match="Try again shortly") as failure:
        client._session(GOOGLE_TOOLKIT)
    assert "internal response" not in str(failure.value)
    assert "internal response" not in caplog.text
    assert "google auth config" not in caplog.text
    assert client._session(X_TOOLKIT).session_id == "session-twitter"
    assert state.session(GOOGLE_TOOLKIT) is None
    assert state.session(X_TOOLKIT)["session_id"] == "session-twitter"


@pytest.mark.asyncio
async def test_managed_catalog_keeps_google_files_available_when_x_managed_auth_is_unavailable(
    tmp_path: Path,
) -> None:
    settings = settings_for(
        tmp_path,
        provider_mode="managed",
        mail_provider="managed",
        calendar_provider="managed",
        tasks_provider="managed",
        x_provider="managed",
        composio_api_key="composio-secret",
    )
    repository = await initialized_repository(settings)
    manager = ConnectionManager(settings, repository)

    catalog = manager.catalog()
    google = [item for item in catalog.connections if item.service in {"mail", "calendar", "tasks"}]
    x = next(item for item in catalog.connections if item.service == "x")
    files = next(item for item in catalog.connections if item.service == "files")

    assert all(item.state.value == "disconnected" for item in google)
    assert x.state.value == "unavailable"
    assert x.last_error == "Managed connection is currently unavailable for X."
    assert files.provider == "Local Mac"
    assert "ToolRouter" not in catalog.model_dump_json()
    with pytest.raises(OAuthError, match="unavailable for X"):
        manager.start_x()


def test_managed_semantic_adapters_keep_curated_provenance(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)

    class FakeManaged:
        def __init__(self):
            self.calls = []

        def execute(self, toolkit, slug, arguments):
            self.calls.append((toolkit, slug, arguments))
            if slug == "GOOGLESUPER_FETCH_EMAILS":
                return {"messages": [{"messageId": "m1", "threadId": "t1", "subject": "Hello"}]}
            if slug == "GOOGLESUPER_EVENTS_LIST":
                return {
                    "items": [
                        {
                            "id": "e1",
                            "summary": "Focus",
                            "start": {"dateTime": "2026-08-29T10:00:00+05:30"},
                            "end": {"dateTime": "2026-08-29T11:00:00+05:30"},
                        }
                    ]
                }
            if slug == "GOOGLESUPER_LIST_TASKS":
                return {"items": [{"id": "task-1", "title": "Review", "status": "needsAction"}]}
            if slug == "TWITTER_RECENT_SEARCH":
                return {"data": [{"id": "post-1", "text": "Hello"}]}
            return {"id": "created-1", "summary": "Focus", "status": "needsAction"}

        def account(self, _toolkit):
            return {"status": "ACTIVE", "account_label": "@alex"}

    fake = FakeManaged()
    assert (
        ManagedGoogleWorkspaceService(settings, fake).search_mail("hello")["connection_mode"]
        == "managed"
    )
    assert (
        ManagedCalendarService(settings, fake).list_events(
            "2026-08-29T09:00:00+05:30", "2026-08-29T12:00:00+05:30"
        )["events"][0]["id"]
        == "e1"
    )
    assert ManagedTasksService(settings, fake).list_tasks()["tasks"][0]["id"] == "task-1"
    assert ManagedXService(settings, fake).search_posts("hello")["posts"][0]["id"] == "post-1"


def test_managed_tasks_use_current_schema_and_preserve_local_due_date(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)

    class FakeManaged:
        def __init__(self):
            self.calls: list[tuple[str, str, dict[str, Any]]] = []

        def execute(self, toolkit, slug, arguments):
            self.calls.append((toolkit, slug, arguments))
            if slug == "GOOGLESUPER_LIST_TASKS":
                return {"items": []}
            return _unwrap_result(
                {
                    "value": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "data": {"id": "task-real-1", "title": "Review"},
                                    "successful": True,
                                }
                            ),
                        }
                    ]
                }
            )

    fake = FakeManaged()
    service = ManagedTasksService(settings, fake)
    assert service.list_tasks()["count"] == 0
    created = service.create_task("Review", due_at="2026-08-31T19:00:00+05:30")
    assert fake.calls[0][2] == {"tasklistId": "@default", "showCompleted": True, "maxResults": 100}
    assert fake.calls[1][1] == "GOOGLESUPER_INSERT_TASK"
    assert fake.calls[1][2]["tasklist_id"] == "@default"
    assert fake.calls[1][2]["due"] == "2026-08-31T00:00:00Z"
    assert created["id"] == "task-real-1"
    assert created["due_date"] == "2026-08-31"
    assert created["requested_due_time"] == "7:00 PM"
    assert created["due_time_supported"] is False


def test_managed_mcp_content_blocks_are_normalized_and_calendar_wall_time_is_preserved(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)

    class FakeManaged:
        def __init__(self):
            self.calls: list[tuple[str, str, dict[str, Any]]] = []

        def execute(self, toolkit, slug, arguments):
            self.calls.append((toolkit, slug, arguments))
            if slug == "GOOGLESUPER_CREATE_EVENT":
                return _unwrap_result(
                    {
                        "value": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "data": {
                                            "id": "event-real-1",
                                            "summary": "DayPilot Test",
                                            "htmlLink": "https://calendar.google.com/event/real-1",
                                        },
                                        "successful": True,
                                        "error": None,
                                    }
                                ),
                            }
                        ]
                    }
                )
            return {"value": []}

    fake = FakeManaged()
    result = ManagedCalendarService(settings, fake).create_event(
        "DayPilot Test",
        "2026-08-31T17:00:00+05:30",
        "2026-08-31T17:15:00+05:30",
    )
    args = fake.calls[0][2]
    assert args["start_datetime"] == "2026-08-31T17:00:00"
    assert args["end_datetime"] == "2026-08-31T17:15:00"
    assert args["timezone"] == "Asia/Kolkata"
    assert result["id"] == "event-real-1"
    assert result["external_url"] == "https://calendar.google.com/event/real-1"

    assert _unwrap_result(
        {"value": [{"type": "text", "text": '{"data":{"id":"one"},"successful":true}'}]}
    ) == {"id": "one"}


def test_managed_gmail_draft_resolves_myself_and_uses_current_provider_schema(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)

    class FakeManaged:
        def __init__(self):
            self.calls: list[tuple[str, str, dict[str, Any]]] = []

        def execute(self, toolkit, slug, arguments):
            self.calls.append((toolkit, slug, arguments))
            if slug == "GOOGLESUPER_GET_PROFILE":
                return {"emailAddress": "alex@example.com"}
            return {"draft": {"id": "draft-1", "message": {"id": "message-1"}}}

    fake = FakeManaged()
    result = ManagedGoogleWorkspaceService(settings, fake).create_draft(
        "myself", "DayPilot verification", "DayPilot managed Gmail write test."
    )
    profile_call = fake.calls[0]
    draft_call = fake.calls[1]
    assert profile_call[1] == "GOOGLESUPER_GET_PROFILE"
    assert draft_call[1] == "GOOGLESUPER_CREATE_EMAIL_DRAFT"
    assert draft_call[2] == {
        "user_id": "me",
        "recipient_email": "alex@example.com",
        "subject": "DayPilot verification",
        "body": "DayPilot managed Gmail write test.",
    }
    assert result["id"] == "draft-1"
    assert result["message_id"] == "message-1"
    assert result["recipient"] == "alex@example.com"


@pytest.mark.asyncio
async def test_calendar_verification_recovers_one_exact_event_without_guessing_duplicates() -> None:
    class FakeGateway:
        def __init__(self, events):
            self.events = events

        async def invoke(self, tool_name, _arguments):
            assert tool_name == "list_events"
            return {"events": self.events}

    result = {
        "action_id": "calendar-1",
        "tool_name": "create_event",
        "result": {
            "title": "DayPilot Test",
            "start_at": "2026-08-31T17:00:00+05:30",
            "end_at": "2026-08-31T17:15:00+05:30",
        },
    }
    verification = await _verify_result(
        "run-1",
        FakeGateway(
            [
                {
                    "id": "event-recovered",
                    "title": "DayPilot Test",
                    "start_at": "2026-08-31T11:30:00Z",
                    "end_at": "2026-08-31T11:45:00Z",
                    "htmlLink": "https://calendar.google.com/event/recovered",
                }
            ]
        ),
        None,
        result,
    )
    assert verification["verified"] is True
    assert result["result"]["id"] == "event-recovered"
    assert result["result"]["external_url"].endswith("recovered")

    duplicate = await _verify_result(
        "run-1",
        FakeGateway(
            [
                {
                    "id": "event-a",
                    "title": "DayPilot Test",
                    "start_at": "2026-08-31T17:00:00+05:30",
                    "end_at": "2026-08-31T17:15:00+05:30",
                },
                {
                    "id": "event-b",
                    "title": "DayPilot Test",
                    "start_at": "2026-08-31T17:00:00+05:30",
                    "end_at": "2026-08-31T17:15:00+05:30",
                },
            ]
        ),
        None,
        {"action_id": "calendar-2", "tool_name": "create_event", "result": result["result"].copy()},
    )
    assert duplicate["verified"] is False
    assert "multiple" in duplicate["detail"]
