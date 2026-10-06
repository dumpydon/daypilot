from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from backend.app.api.routes import router
from backend.app.config import Settings
from backend.app.domain.models import ConnectionCatalog
from backend.app.persistence.repository import DayPilotRepository
from backend.app.providers import managed_state, mode_store
from backend.app.providers import manager as manager_module
from backend.app.providers.manager import ConnectionManager


@pytest.mark.asyncio
async def test_catalog_batches_fresh_metadata_without_composio_calls(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'metadata.db'}",
        daypilot_demo_mode=False,
        composio_api_key="test-key",
    )
    repo = DayPilotRepository(settings.database_target)
    await repo.initialize()
    manager = ConnectionManager(settings, repo)
    manager.managed_state.set_account(settings.composio_google_toolkit, "test-account", "ACTIVE")
    monkeypatch.setattr(
        manager.managed, "_client", lambda: pytest.fail("Catalog must not contact Composio")
    )
    original = manager_module.connect_sync
    opens = 0

    @contextmanager
    def counted(target):
        nonlocal opens
        opens += 1
        with original(target) as connection:
            yield connection

    for module in (manager_module, mode_store, managed_state):
        monkeypatch.setattr(module, "connect_sync", counted)
    before = [manager.connection(service) for service in mode_store.ProviderModeStore.SERVICES]
    assert opens == 9
    opens = 0
    after = manager.catalog().connections
    assert after == before
    assert opens == 1
    # This is a request snapshot, not a new stale cross-request cache.
    manager.managed_state.set_account(settings.composio_google_toolkit, "test-account", "REVOKED")
    assert manager.catalog().connections[0].requires_reauth


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/connections", "/api/tools"])
async def test_slow_catalog_does_not_block_health_or_readiness(path):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def catalog(**_kwargs):
        started.set()
        release.wait(timeout=3)
        finished.set()
        return (
            ConnectionCatalog(demo_mode=False, connections=[])
            if path.endswith("connections")
            else []
        )

    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(_env_file=None)
    app.state.coordinator = None
    app.state.repository = None
    app.state.connections = SimpleNamespace(catalog=catalog)
    app.state.gateway = SimpleNamespace(discover=AsyncMock(return_value=[]), catalog=catalog)
    app.state.readiness = {
        "state": "ready",
        "mcp_servers_ready": 6,
        "mcp_servers_total": 6,
        "degraded_services": [],
        "message": "Ready",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        loading = asyncio.create_task(client.get(path))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert (await asyncio.wait_for(client.get("/health"), 1)).status_code == 200
            assert (await asyncio.wait_for(client.get("/api/readiness"), 1)).json()[
                "state"
            ] == "ready"
            assert not finished.is_set()
        finally:
            release.set()
            await loading
