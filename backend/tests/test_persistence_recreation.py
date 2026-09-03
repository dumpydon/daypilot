from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.app.config import Settings
from backend.app.domain.models import EventState, PreferenceSet
from backend.app.graph.workflow import WorkflowDependencies, build_daypilot_graph
from backend.app.mcp.gateway import MCPGateway
from backend.app.persistence.database import _adapt_sql, is_postgres_target
from backend.app.persistence.repository import DayPilotRepository
from backend.app.providers.managed_state import ManagedStateStore
from backend.app.services.coordinator import RunCoordinator
from backend.app.services.planner import PlanBuilder
from backend.app.services.reasoner import DeterministicReasoner
from mcp_servers.common.database import initialize_demo_database


@pytest.mark.asyncio
async def test_application_state_survives_repository_recreation(tmp_path: Path) -> None:
    target = tmp_path / "persistent.db"
    first = DayPilotRepository(target)
    await first.initialize()
    await first.create_run("run-1", "thread-1", "A persisted prompt", admin_authorized=True)
    await first.append_event("run-1", "request_received", EventState.COMPLETED, "Request received")
    await first.update_preferences(
        PreferenceSet(
            preferred_focus_block_minutes=120,
            avoid_scheduling_after="21:30",
            preferred_task_due_time="17:00",
        )
    )
    await first.set_provider_mode("mail", "managed")
    await first.set_managed_account("googlesuper", "account-1", "ACTIVE", "Owner")
    first_execution = await first.begin_execution(
        "run-1",
        "action-1",
        "create_task",
        {"title": "Persisted idempotency record"},
    )
    assert first_execution["is_new"] is True
    await first.complete_execution(
        "run-1",
        "action-1",
        success=True,
        result={"id": "task-1"},
    )

    second = DayPilotRepository(target)
    await second.initialize()
    run = await second.get_run("run-1")
    events = await second.list_events("run-1")

    assert run.user_request == "A persisted prompt"
    assert await second.is_run_admin_authorized("run-1") is True
    assert events[0].event_type == "request_received"
    assert (await second.get_preferences()).preferred_focus_block_minutes == 120
    assert (await second.list_provider_modes())["mail"] == "managed"
    assert (await second.list_managed_accounts())[0]["account_id"] == "account-1"
    assert [item.id for item in await second.list_runs()] == ["run-1"]
    repeated = await second.begin_execution(
        "run-1",
        "action-1",
        "create_task",
        {"title": "Persisted idempotency record"},
    )
    assert repeated["is_new"] is False
    assert repeated["result"] == {"id": "task-1"}


def test_postgres_target_and_sqlite_compatibility_translation() -> None:
    assert is_postgres_target("postgresql://user:pass@host/db")
    assert is_postgres_target("postgres://user:pass@host/db")
    assert not is_postgres_target(Path("local.db"))
    assert _adapt_sql(
        "INSERT OR IGNORE INTO demo_metadata(key, value) VALUES (?, ?)",
        ("seed_version", "2"),
        postgres=True,
    ).startswith("INSERT INTO")
    assert "%s" in _adapt_sql("SELECT * FROM runs WHERE id = ?", ("run-1",), postgres=True)


def test_postgres_database_url_wins_over_stale_local_mcp_path(monkeypatch) -> None:
    from mcp_servers.common.database import database_path_from_env

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/daypilot")
    monkeypatch.setenv("DAYPILOT_DATABASE_PATH", "/tmp/stale-daypilot.db")

    assert database_path_from_env() == "postgresql://user:pass@host/daypilot"


def test_managed_composio_metadata_survives_store_recreation(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'managed.db'}")
    first = ManagedStateStore(settings.database_target)
    user_id = first.ensure_user_id()
    first.set_account("googlesuper", "account-1", "ACTIVE", "Owner")
    first.set_session("googlesuper", "session-1", user_id)

    second = ManagedStateStore(settings.database_target)

    assert second.account("googlesuper")["account_id"] == "account-1"
    assert second.session("googlesuper")["session_id"] == "session-1"
    assert second.ensure_user_id() == user_id


@pytest.mark.asyncio
async def test_pending_hitl_checkpoint_survives_graph_recreation(tmp_path: Path) -> None:
    target = tmp_path / "checkpoint.db"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{target}",
        daypilot_demo_mode=True,
        provider_mode="demo",
    )
    initialize_demo_database(target, settings.daypilot_timezone)
    first_repository = DayPilotRepository(target)
    await first_repository.initialize()
    first_gateway = MCPGateway(settings)
    reasoner = DeterministicReasoner(settings.daypilot_timezone)
    planner = PlanBuilder(settings.daypilot_timezone)

    async with AsyncSqliteSaver.from_conn_string(str(target)) as first_saver:
        await first_saver.setup()
        first_graph = build_daypilot_graph(
            WorkflowDependencies(first_repository, first_gateway, reasoner, planner),
            first_saver,
        )
        first_coordinator = RunCoordinator(first_graph, first_repository, first_gateway)
        accepted = await first_coordinator.start_run(
            "Create a task called checkpoint persistence test."
        )
        pending = await first_coordinator.wait_until_settled(accepted.id)
        assert pending.status == "waiting_approval"
        await first_coordinator.shutdown()

    second_repository = DayPilotRepository(target)
    await second_repository.initialize()
    second_gateway = MCPGateway(settings)
    async with AsyncSqliteSaver.from_conn_string(str(target)) as second_saver:
        await second_saver.setup()
        second_graph = build_daypilot_graph(
            WorkflowDependencies(second_repository, second_gateway, reasoner, planner),
            second_saver,
        )
        second_coordinator = RunCoordinator(second_graph, second_repository, second_gateway)
        restored = await second_coordinator.get_detail(accepted.id)
        assert restored.status == "waiting_approval"
        assert restored.interrupt_payload is not None
        await second_coordinator.resume(accepted.id, "reject")
        rejected = await second_coordinator.wait_until_settled(accepted.id)
        assert rejected.status == "rejected"
        assert await second_repository.list_executions(accepted.id) == []
        await second_coordinator.shutdown()
