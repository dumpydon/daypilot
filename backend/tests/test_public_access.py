from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import router
from backend.app.config import Settings
from backend.app.domain.errors import UnauthorizedToolCallError
from backend.app.domain.models import (
    ConnectionCatalog,
    ProviderConnection,
    ProviderConnectionState,
    RiskLevel,
    RunAccepted,
    RunStatus,
    ToolMetadata,
)
from backend.app.main import _initialize_runtime
from backend.app.mcp.gateway import MCPGateway
from backend.app.persistence.repository import DayPilotRepository
from backend.app.services.access import requires_personal_access
from backend.app.services.admin_auth import AdminAuthService


class StubCoordinator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def start_run(self, request: str, *, admin_authorized: bool = False) -> RunAccepted:
        self.calls.append((request, admin_authorized))
        return RunAccepted(id="run-public-test", status=RunStatus.QUEUED)


class StubConnections:
    def catalog(self) -> ConnectionCatalog:
        return ConnectionCatalog(
            demo_mode=False,
            connections=[
                ProviderConnection(
                    service="mail",
                    provider="Google Workspace",
                    state=ProviderConnectionState.CONNECTED,
                    account_label="private@example.com",
                    capabilities=["search_mail"],
                    connection_mode="managed",
                )
            ],
        )

    def public_catalog(self) -> ConnectionCatalog:
        return ConnectionCatalog(
            demo_mode=False,
            connections=[
                ProviderConnection(
                    service="mail",
                    provider="Google Workspace",
                    state=ProviderConnectionState.UNAVAILABLE,
                    last_error="Available to admin only.",
                    connection_mode="managed",
                )
            ],
        )


class StubGateway:
    connections: ClassVar[dict[str, dict[str, str]]] = {
        name: {} for name in ("mail", "calendar", "tasks", "files", "x", "web")
    }

    async def discover(self, **_kwargs):
        return []

    def catalog(self, **_kwargs):
        return []


def _app(tmp_path: Path) -> tuple[FastAPI, StubCoordinator]:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'public.db'}",
        daypilot_demo_mode=False,
        public_demo_mode=True,
        admin_secret="test-admin-secret",
        site_url="http://localhost:3000",
    )
    repository = DayPilotRepository(settings.database_target)
    asyncio.run(repository.initialize())
    coordinator = StubCoordinator()
    app = FastAPI()
    app.include_router(router)
    app.state.settings = settings
    app.state.repository = repository
    app.state.coordinator = coordinator
    app.state.gateway = StubGateway()
    app.state.connections = StubConnections()
    app.state.admin_auth = AdminAuthService(settings, repository)
    app.state.runtime_state = "ready"
    app.state.readiness = {
        "state": "ready",
        "mcp_servers_ready": 6,
        "mcp_servers_total": 6,
        "degraded_services": [],
        "message": "Ready",
    }
    return app, coordinator


@pytest.mark.parametrize(
    "prompt",
    [
        "Show my latest Gmail emails",
        "What's on my calendar tomorrow?",
        "List my incomplete tasks",
        "Create a calendar event tomorrow",
        "Complete my first task",
    ],
)
def test_public_demo_blocks_personal_requests_and_hides_connection_metadata(
    tmp_path: Path,
    prompt: str,
) -> None:
    app, coordinator = _app(tmp_path)
    client = TestClient(app)

    response = client.post("/api/runs", json={"request": prompt})

    assert response.status_code == 403
    assert "disabled in the public demo" in response.json()["detail"]
    assert client.get("/api/connections").json()["connections"][0]["account_label"] is None
    assert coordinator.calls == []


def test_public_policy_does_not_block_ordinary_general_creation_prompts(tmp_path: Path) -> None:
    app, coordinator = _app(tmp_path)
    app.state.runtime_state = "degraded"
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        json={"request": "Create a Python function that reverses a linked list"},
    )

    assert response.status_code == 202
    assert coordinator.calls == [("Create a Python function that reverses a linked list", False)]
    assert requires_personal_access("Create a Python function") is False


def test_admin_session_unlocks_personal_routing_and_logout_locks_it_again(tmp_path: Path) -> None:
    app, coordinator = _app(tmp_path)
    client = TestClient(app)

    assert client.post("/api/admin/login", json={"access_code": "wrong"}).status_code == 401
    unlocked = client.post("/api/admin/login", json={"access_code": "test-admin-secret"})
    assert unlocked.status_code == 200
    assert unlocked.json()["authenticated"] is True
    assert (
        client.get("/api/connections").json()["connections"][0]["account_label"]
        == "private@example.com"
    )

    allowed = client.post("/api/runs", json={"request": "Show my latest Gmail emails"})
    assert allowed.status_code == 202
    assert coordinator.calls == [("Show my latest Gmail emails", True)]

    assert client.post("/api/admin/logout").status_code == 200
    assert client.get("/api/admin/status").json()["authenticated"] is False
    blocked = client.post("/api/runs", json={"request": "Create a calendar event tomorrow"})
    assert blocked.status_code == 403


def test_public_cannot_manage_google_connections(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    client = TestClient(app)

    assert client.post("/api/connections/google/start").status_code == 403
    assert client.post("/api/connections/google/disconnect").status_code == 403


def test_readiness_exposes_starting_and_degraded_states(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    client = TestClient(app)

    app.state.runtime_state = "starting"
    app.state.readiness = {
        "state": "starting",
        "mcp_servers_ready": 0,
        "mcp_servers_total": 6,
        "degraded_services": [],
        "message": "Waking",
    }
    assert client.get("/api/readiness").json()["state"] == "starting"
    assert client.post("/api/runs", json={"request": "What is 2 + 2?"}).status_code == 503

    app.state.runtime_state = "degraded"
    app.state.readiness = {
        "state": "degraded",
        "mcp_servers_ready": 5,
        "mcp_servers_total": 6,
        "degraded_services": ["calendar"],
        "message": "Calendar unavailable",
    }
    degraded = client.get("/api/readiness").json()
    assert degraded["state"] == "degraded"
    assert degraded["degraded_services"] == ["calendar"]


@pytest.mark.asyncio
async def test_public_gateway_rejects_personal_tool_invocation(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'gateway.db'}",
        daypilot_demo_mode=False,
        public_demo_mode=True,
        admin_secret="secret",
    )
    gateway = MCPGateway(settings)

    class FakeTool:
        async def ainvoke(self, _payload):
            return {"should": "not run"}

    gateway._tools["search_mail"] = FakeTool()  # type: ignore[assignment]
    gateway._tools["create_event"] = FakeTool()  # type: ignore[assignment]
    gateway._metadata["search_mail"] = ToolMetadata(
        name="search_mail",
        server_name="mail",
        description="",
        risk_level=RiskLevel.SAFE_READ,
        side_effecting=False,
    )
    gateway._metadata["create_event"] = ToolMetadata(
        name="create_event",
        server_name="calendar",
        description="",
        risk_level=RiskLevel.SIDE_EFFECT,
        side_effecting=True,
    )

    with pytest.raises(UnauthorizedToolCallError):
        await gateway.invoke("search_mail", {"query": "latest"})
    with pytest.raises(UnauthorizedToolCallError):
        await gateway.invoke("create_event", {"title": "private write"})

    assert await gateway.invoke("search_mail", {"query": "latest"}, admin_authorized=True) == {
        "should": "not run"
    }


@pytest.mark.asyncio
async def test_gateway_switches_public_and_admin_catalogs_without_leaking_private_tools(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'catalog.db'}",
        daypilot_demo_mode=False,
        public_demo_mode=True,
    )
    gateway = MCPGateway(settings)
    tool_names = {
        "mail": "search_mail",
        "calendar": "list_events",
        "tasks": "list_tasks",
        "files": "search_files",
        "x": "search_posts",
        "web": "search_web",
    }

    class FakeTool:
        description = ""
        args_schema: ClassVar[dict[str, object]] = {}

        def __init__(self, name: str) -> None:
            self.name = name

        async def ainvoke(self, _payload):
            return {"tool": self.name}

    class FakeClient:
        async def get_tools(self, *, server_name: str):
            return [FakeTool(tool_names[server_name])]

    gateway.client = FakeClient()  # type: ignore[assignment]
    await gateway.discover(admin_authorized=True)

    public_mail = next(
        item for item in gateway.catalog(admin_authorized=False) if item["name"] == "mail"
    )
    assert public_mail["tool_count"] == 0
    assert public_mail["tools"] == []
    assert public_mail["account_label"] is None

    await gateway.discover(force=True, admin_authorized=False)
    assert await gateway.invoke(
        "search_mail",
        {"query": "latest"},
        admin_authorized=True,
    ) == {"tool": "search_mail"}


@pytest.mark.asyncio
async def test_gateway_discovery_does_not_expose_raw_transport_errors(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'errors.db'}",
        daypilot_demo_mode=False,
        public_demo_mode=True,
    )
    gateway = MCPGateway(settings)

    class FailingClient:
        async def get_tools(self, *, server_name: str):
            raise RuntimeError(f"provider-secret-for-{server_name}")

    gateway.client = FailingClient()  # type: ignore[assignment]
    await gateway.discover(admin_authorized=False)

    web = next(item for item in gateway.catalog(admin_authorized=False) if item["name"] == "web")
    assert web["error"] == "Web capability could not initialize."
    assert "provider-secret" not in web["error"]


@pytest.mark.asyncio
async def test_runtime_initialization_marks_one_failed_provider_as_degraded() -> None:
    settings = Settings(_env_file=None, daypilot_demo_mode=False, public_demo_mode=False)

    class FakeGateway:
        connections: ClassVar[dict[str, dict[str, str]]] = {
            name: {} for name in ("mail", "calendar", "tasks", "files", "x", "web")
        }

        async def discover(self, **_kwargs):
            return [object()]

        def catalog(self, **_kwargs):
            return [
                {
                    "name": name,
                    "connected": name != "calendar",
                    "provider_state": "connected" if name != "calendar" else "error",
                }
                for name in self.connections
            ]

    app = SimpleNamespace(state=SimpleNamespace())
    await _initialize_runtime(app, FakeGateway(), settings)

    assert app.state.runtime_state == "degraded"
    assert app.state.readiness["degraded_services"] == ["calendar"]


@pytest.mark.asyncio
async def test_public_readiness_ignores_private_provider_failures() -> None:
    settings = Settings(_env_file=None, daypilot_demo_mode=False, public_demo_mode=True)

    class FakeGateway:
        connections: ClassVar[dict[str, dict[str, str]]] = {
            name: {} for name in ("mail", "calendar", "tasks", "files", "x", "web")
        }

        async def discover(self, **_kwargs):
            return [object()]

        def catalog(self, **_kwargs):
            return [
                {
                    "name": name,
                    "connected": name not in {"calendar", "tasks"},
                    "provider_state": "connected" if name == "web" else "error",
                }
                for name in self.connections
            ]

    app = SimpleNamespace(state=SimpleNamespace())
    await _initialize_runtime(app, FakeGateway(), settings)

    assert app.state.runtime_state == "ready"
    assert app.state.readiness["degraded_services"] == []


@pytest.mark.asyncio
async def test_admin_authorization_does_not_bypass_the_existing_hitl_gate(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Create a Google Task called 'admin gate regression test'.",
        admin_authorized=True,
    )
    detail = await harness.coordinator.wait_until_settled(accepted.id, max_wait_seconds=45)

    assert detail.status == "waiting_approval"
    assert detail.approval_status == "pending"
    assert await harness.repository.list_executions(accepted.id) == []
