from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.config import Settings


def test_health_is_available_while_runtime_bootstrap_is_pending(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'startup.db'}",
        daypilot_demo_mode=False,
        public_demo_mode=True,
    )

    async def pending_bootstrap(app, bootstrap_settings, shutdown_event):
        assert bootstrap_settings is settings
        await shutdown_event.wait()

    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "_bootstrap_application", pending_bootstrap)

    with TestClient(main_module.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["runtime_state"] == "starting"
        assert health.json()["database"] == "initializing"
        assert health.json()["graph"] == "initializing"
        assert client.get("/api/readiness").json()["state"] == "starting"
        assert client.post("/api/runs", json={"request": "What is 2 + 2?"}).status_code == 503

        main_module.app.state.database_state = "unavailable"
        main_module.app.state.graph_state = "unavailable"
        failed_health = client.get("/health")
        assert failed_health.status_code == 503
        assert failed_health.json()["status"] == "degraded"


def test_postgres_connection_url_adds_bounded_timeout_without_losing_pooler_options() -> None:
    settings = Settings(
        _env_file=None,
        database_url=(
            "postgresql://user:password@pooler.example.com:6543/postgres"
            "?sslmode=require&pgbouncer=true"
        ),
        database_connect_timeout_seconds=7,
    )

    assert settings.database_connection_url.endswith(
        "sslmode=require&pgbouncer=true&connect_timeout=7"
    )

    existing = Settings(
        _env_file=None,
        database_url="postgres://user:password@host/postgres?connect_timeout=3",
        database_connect_timeout_seconds=7,
    )
    assert existing.database_connection_url.endswith("connect_timeout=3")
