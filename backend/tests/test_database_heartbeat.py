from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import router
from backend.app.config import Settings
from backend.app.persistence.repository import DayPilotRepository


@pytest.fixture
def heartbeat_client(tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'heartbeat.db'}",
        maintenance_secret="maintenance-only-test-secret",
        admin_secret="different-admin-secret",
    )
    return TestClient(app)


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Bearer different-admin-secret"])
def test_heartbeat_rejects_missing_wrong_and_admin_credentials(
    heartbeat_client, monkeypatch, authorization
):
    query = AsyncMock()
    monkeypatch.setattr(DayPilotRepository, "database_heartbeat", query)
    headers = {"Authorization": authorization} if authorization else {}
    response = heartbeat_client.get("/internal/database-heartbeat", headers=headers)
    assert response.status_code == 401
    query.assert_not_called()


def test_heartbeat_disabled_without_secret(heartbeat_client):
    heartbeat_client.app.state.settings.maintenance_secret = None
    assert heartbeat_client.get("/internal/database-heartbeat").status_code == 503


def test_heartbeat_performs_read_only_query_without_runtime_or_schema(heartbeat_client):
    # No graph, provider services, or schema initialization is needed for SELECT 1.
    response = heartbeat_client.get(
        "/internal/database-heartbeat",
        headers={"Authorization": "Bearer maintenance-only-test-secret"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["database"] == "sqlite"
    assert response.json()["checked_at"]
    import sqlite3

    with sqlite3.connect(heartbeat_client.app.state.settings.database_path) as connection:
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            == []
        )


@pytest.mark.parametrize("error", [RuntimeError("postgres://private:secret@host"), TimeoutError()])
def test_heartbeat_failure_is_bounded_and_redacted(heartbeat_client, monkeypatch, caplog, error):
    monkeypatch.setattr(DayPilotRepository, "database_heartbeat", AsyncMock(side_effect=error))
    with caplog.at_level(logging.WARNING):
        response = heartbeat_client.get(
            "/internal/database-heartbeat",
            headers={"Authorization": "Bearer maintenance-only-test-secret"},
        )
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "postgres://" not in caplog.text
    assert heartbeat_client.get("/health").status_code == 200


def test_postgres_heartbeat_uses_existing_connection_and_returns_database_clock(monkeypatch):
    from contextlib import asynccontextmanager

    from backend.app.persistence import repository as repository_module

    cursor = AsyncMock()
    cursor.fetchone.return_value = {"alive": 1, "checked_at": "2026-10-06 03:30:00+00"}
    connection = AsyncMock()
    connection.execute.return_value = cursor

    @asynccontextmanager
    async def connect(target):
        assert target == "postgresql://pooler.test/postgres"
        yield connection

    monkeypatch.setattr(repository_module, "connect_async", connect)
    repo = DayPilotRepository("postgresql://pooler.test/postgres")
    assert asyncio.run(repo.database_heartbeat()) == "2026-10-06 03:30:00+00"
    sql = connection.execute.await_args.args[0]
    assert sql.startswith("SELECT 1 AS alive, CURRENT_TIMESTAMP AS checked_at")
    connection.commit.assert_not_awaited()
