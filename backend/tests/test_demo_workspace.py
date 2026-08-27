from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.app.config import Settings
from backend.app.domain.errors import (
    DemoModeRequiredError,
    DemoWorkspaceError,
    RunConflictError,
)
from backend.app.domain.models import EventState, PreferenceSet
from backend.app.persistence.repository import DayPilotRepository
from backend.app.services.demo_workspace import DemoWorkspaceService
from mcp_servers.common.database import initialize_demo_database
from mcp_servers.common.store import DemoServiceStore


async def build_service(
    tmp_path: Path, *, demo_mode: bool = True
) -> tuple[DemoWorkspaceService, DayPilotRepository, Path]:
    database_path = tmp_path / "demo-workspace.db"
    initialize_demo_database(database_path, "Asia/Kolkata")
    repository = DayPilotRepository(database_path)
    await repository.initialize()
    settings = Settings(
        database_url=f"sqlite:///{database_path}",
        daypilot_demo_mode=demo_mode,
        daypilot_timezone="Asia/Kolkata",
    )
    return DemoWorkspaceService(settings, repository), repository, database_path


async def create_terminal_run(repository: DayPilotRepository, run_id: str) -> None:
    await repository.create_run(run_id, f"thread-{run_id}", f"Request {run_id}")
    await repository.append_event(
        run_id,
        "request_received",
        EventState.COMPLETED,
        "Request received",
        "Test request",
    )
    await repository.finish_run(run_id, "Completed test request")


@pytest.mark.asyncio
async def test_reset_restores_all_mutable_demo_services_and_preserves_app_state(
    tmp_path: Path,
) -> None:
    service, repository, database_path = await build_service(tmp_path)
    store = DemoServiceStore(database_path, "Asia/Kolkata")
    preferences = PreferenceSet(
        preferred_focus_block_minutes=120,
        avoid_scheduling_after="21:00",
        preferred_task_due_time="17:00",
    )
    await repository.update_preferences(preferences)
    await create_terminal_run(repository, "run-preserved")
    await repository.begin_execution("run-preserved", "action-1", "create_event", {})
    await repository.complete_execution(
        "run-preserved", "action-1", success=True, result={"id": "event"}
    )

    seed_event_ids = {
        event["id"]
        for event in store.list_events("2000-01-01T00:00:00+05:30", "2100-01-01T00:00:00+05:30")[
            "events"
        ]
    }
    seed_mail_thread_ids = {thread["thread_id"] for thread in store.search_mail("", 20)["threads"]}
    seed_task_ids = {task["id"] for task in store.list_tasks()["tasks"]}
    seed_file_ids = {file["id"] for file in store.list_files()["files"]}
    seed_post_ids = {post["id"] for post in store.search_posts("", 20)["posts"]}

    store.create_event(
        "Temporary study block",
        "2030-01-01T20:00:00+05:30",
        "2030-01-01T21:00:00+05:30",
    )
    store.create_task("Temporary task")
    temporary_draft = store.create_draft("person@example.com", "Temporary draft", "Draft body")
    store.create_post_draft("Temporary X draft")
    store.publish_post("Temporary X post")

    result = await service.reset_demo_workspace()

    assert result.status == "reset"
    assert result.services == ["Mail", "Calendar", "Tasks", "Files", "X"]
    assert result.preserved_runs == 1
    assert {
        event["id"]
        for event in store.list_events("2000-01-01T00:00:00+05:30", "2100-01-01T00:00:00+05:30")[
            "events"
        ]
    } == seed_event_ids
    assert {
        thread["thread_id"] for thread in store.search_mail("", 20)["threads"]
    } == seed_mail_thread_ids
    with pytest.raises(ValueError, match="not found"):
        store.get_message(temporary_draft["id"])
    assert {task["id"] for task in store.list_tasks()["tasks"]} == seed_task_ids
    assert store.search_mail("Temporary draft")["count"] == 0
    assert {file["id"] for file in store.list_files()["files"]} == seed_file_ids
    assert {post["id"] for post in store.search_posts("", 20)["posts"]} == seed_post_ids
    assert await repository.get_preferences() == preferences
    assert [run.id for run in await repository.list_runs()] == ["run-preserved"]
    assert len(await repository.list_executions("run-preserved")) == 1


@pytest.mark.asyncio
async def test_reset_is_blocked_by_active_or_pending_runs(tmp_path: Path) -> None:
    service, repository, database_path = await build_service(tmp_path)
    store = DemoServiceStore(database_path, "Asia/Kolkata")
    store.create_task("Temporary task")
    await repository.create_run("run-pending", "thread-pending", "Pending request")

    with pytest.raises(RunConflictError, match="active or awaiting approval"):
        await service.reset_demo_workspace()

    assert store.list_tasks()["count"] == 4


@pytest.mark.asyncio
async def test_reset_rejects_non_demo_mode_before_touching_demo_store(
    tmp_path: Path, monkeypatch
) -> None:
    service, _, database_path = await build_service(tmp_path, demo_mode=False)
    store = DemoServiceStore(database_path, "Asia/Kolkata")
    store.create_task("Temporary task")
    called = False

    def forbidden_reset(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(
        "backend.app.services.demo_workspace.initialize_demo_database", forbidden_reset
    )
    with pytest.raises(DemoModeRequiredError, match="DAYPILOT_DEMO_MODE"):
        await service.reset_demo_workspace()

    assert called is False
    assert store.list_tasks()["count"] == 4


@pytest.mark.asyncio
async def test_reset_failure_is_explicit_and_does_not_claim_success(
    tmp_path: Path, monkeypatch
) -> None:
    service, _, database_path = await build_service(tmp_path)
    store = DemoServiceStore(database_path, "Asia/Kolkata")
    store.create_task("Temporary task")

    def fail_reset(*_args, **_kwargs):
        raise OSError("seed storage unavailable")

    monkeypatch.setattr("backend.app.services.demo_workspace.initialize_demo_database", fail_reset)
    with pytest.raises(DemoWorkspaceError, match="seed storage unavailable"):
        await service.reset_demo_workspace()

    assert store.list_tasks()["count"] == 4


@pytest.mark.asyncio
async def test_duplicate_reset_requests_are_serialized_and_safe(tmp_path: Path) -> None:
    service, _, database_path = await build_service(tmp_path)
    store = DemoServiceStore(database_path, "Asia/Kolkata")
    store.create_task("Temporary task")

    first, second = await asyncio.gather(
        service.reset_demo_workspace(),
        service.reset_demo_workspace(),
    )

    assert first.status == second.status == "reset"
    assert store.list_tasks()["count"] == 3


@pytest.mark.asyncio
async def test_clear_history_removes_runs_checkpoints_and_writes_but_preserves_demo_state(
    tmp_path: Path,
) -> None:
    service, repository, database_path = await build_service(tmp_path)
    store = DemoServiceStore(database_path, "Asia/Kolkata")
    store.create_task("Mutation that should survive history clear")
    preferences = PreferenceSet(preferred_focus_block_minutes=60)
    await repository.update_preferences(preferences)
    await create_terminal_run(repository, "run-one")
    await create_terminal_run(repository, "run-two")
    await repository.begin_execution("run-one", "action-1", "create_task", {})
    await repository.complete_execution("run-one", "action-1", success=True, result={"id": "task"})
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                parent_checkpoint_id TEXT,
                type TEXT,
                checkpoint BLOB,
                metadata BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
            );
            CREATE TABLE IF NOT EXISTS writes (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                channel TEXT NOT NULL,
                type TEXT,
                value BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
            );
            INSERT INTO checkpoints(thread_id, checkpoint_id)
            VALUES ('thread-run-one', 'checkpoint-1');
            INSERT INTO writes(thread_id, checkpoint_id, task_id, idx, channel)
            VALUES ('thread-run-one', 'checkpoint-1', 'task-1', 0, 'channel');
            """
        )
        connection.commit()

    result = await service.clear_run_history()

    assert result.status == "cleared"
    assert result.runs_removed == 2
    assert result.events_removed == 2
    assert result.executions_removed == 1
    assert result.checkpoints_removed == 1
    assert result.writes_removed == 1
    assert await repository.list_runs() == []
    assert store.list_tasks()["count"] == 4
    assert await repository.get_preferences() == preferences
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_clear_history_is_blocked_by_pending_run(tmp_path: Path) -> None:
    service, repository, _ = await build_service(tmp_path)
    await repository.create_run("run-pending", "thread-pending", "Pending request")

    with pytest.raises(RunConflictError, match="active or awaiting approval"):
        await service.clear_run_history()

    assert len(await repository.list_runs()) == 1


@pytest.mark.asyncio
async def test_absolute_calendar_timestamp_does_not_float_with_today(tmp_path: Path) -> None:
    database_path = tmp_path / "dates.db"
    timezone = ZoneInfo("Asia/Kolkata")
    initialize_demo_database(database_path, "Asia/Kolkata")
    now = datetime.now(timezone).replace(second=0, microsecond=0)
    start = now.replace(hour=19, minute=0)
    end = start + timedelta(hours=1)
    store = DemoServiceStore(database_path, "Asia/Kolkata")
    created = store.create_event("Study today", start.isoformat(), end.isoformat())

    later_start = start + timedelta(days=2)
    later_end = end + timedelta(days=2)
    old_event = store.list_events(start.isoformat(), end.isoformat())["events"]
    later_events = store.list_events(later_start.isoformat(), later_end.isoformat())["events"]

    assert created["start_at"] == start.isoformat()
    assert old_event[0]["start_at"] == start.isoformat()
    assert not any(event["id"] == created["id"] for event in later_events)
