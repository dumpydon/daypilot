from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from backend.app.domain.errors import RunConflictError, RunNotFoundError
from backend.app.domain.models import (
    ApprovalStatus,
    EventState,
    FileRoot,
    PlanAction,
    PreferenceSet,
    RunRecord,
    RunStatus,
    TimelineEvent,
)

APP_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL UNIQUE,
    user_request TEXT NOT NULL,
    status TEXT NOT NULL,
    approval_status TEXT NOT NULL,
    approval_feedback TEXT,
    plan_json TEXT NOT NULL DEFAULT '[]',
    final_summary TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    state TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_events_run_id_id ON run_events(run_id, id);

CREATE TABLE IF NOT EXISTS execution_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    action_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    result_json TEXT,
    success INTEGER,
    error TEXT,
    verification_json TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(run_id, action_id)
);

CREATE TABLE IF NOT EXISTS preferences (
    profile_id TEXT PRIMARY KEY,
    preferred_focus_block_minutes INTEGER NOT NULL,
    avoid_scheduling_after TEXT NOT NULL,
    preferred_task_due_time TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_modes (
    service TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_roots (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS installation_identity (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    user_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS managed_sessions (
    provider TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS managed_accounts (
    toolkit TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    status TEXT NOT NULL,
    account_label TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


class DayPilotRepository:
    """Small SQL repository; SQL is portable except for local connection setup."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.maintenance_lock = asyncio.Lock()

    async def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as connection:
            await connection.executescript(APP_SCHEMA)
            preferences = PreferenceSet()
            await connection.execute(
                """
                INSERT OR IGNORE INTO preferences(
                    profile_id,
                    preferred_focus_block_minutes,
                    avoid_scheduling_after,
                    preferred_task_due_time,
                    updated_at
                ) VALUES ('default', ?, ?, ?, ?)
                """,
                (
                    preferences.preferred_focus_block_minutes,
                    preferences.avoid_scheduling_after,
                    preferences.preferred_task_due_time,
                    _now(),
                ),
            )
            await connection.commit()

    async def create_run(self, run_id: str, thread_id: str, user_request: str) -> RunRecord:
        async with self.maintenance_lock:
            timestamp = _now()
            async with self._connect() as connection:
                await connection.execute(
                    """
                    INSERT INTO runs(
                        id, thread_id, user_request, status, approval_status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        thread_id,
                        user_request,
                        RunStatus.QUEUED,
                        ApprovalStatus.NOT_REQUIRED,
                        timestamp,
                        timestamp,
                    ),
                )
                await connection.commit()
        return await self.get_run(run_id)

    async def get_run(self, run_id: str) -> RunRecord:
        async with self._connect() as connection:
            row = await self._fetchone(connection, "SELECT * FROM runs WHERE id = ?", (run_id,))
        if row is None:
            raise RunNotFoundError(f"Run {run_id!r} was not found")
        return self._run_record(row)

    async def list_runs(self, limit: int = 25) -> list[RunRecord]:
        async with self._connect() as connection:
            cursor = await connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),)
            )
            rows = await cursor.fetchall()
        return [self._run_record(dict(row)) for row in rows]

    async def ensure_provider_modes(self, modes: dict[str, str]) -> None:
        allowed = {"mail", "calendar", "tasks", "files", "x"}
        if set(modes) != allowed:
            raise ValueError("Provider mode defaults must cover every DayPilot service")
        timestamp = _now()
        async with self._connect() as connection:
            await connection.executemany(
                """
                INSERT OR IGNORE INTO provider_modes(service, mode, updated_at)
                VALUES (?, ?, ?)
                """,
                [(service, mode, timestamp) for service, mode in modes.items()],
            )
            await connection.commit()

    async def list_provider_modes(self) -> dict[str, str]:
        async with self._connect() as connection:
            cursor = await connection.execute("SELECT service, mode FROM provider_modes")
            rows = await cursor.fetchall()
        return {row["service"]: row["mode"] for row in rows}

    async def set_provider_mode(self, service: str, mode: str) -> None:
        if service not in {"mail", "calendar", "tasks", "files", "x"}:
            raise ValueError(f"Unknown provider service {service!r}")
        async with self._connect() as connection:
            await connection.execute(
                """
                INSERT INTO provider_modes(service, mode, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(service) DO UPDATE SET
                    mode = excluded.mode,
                    updated_at = excluded.updated_at
                """,
                (service, mode, _now()),
            )
            await connection.commit()

    async def get_installation_user_id(self) -> str | None:
        async with self._connect() as connection:
            row = await self._fetchone(
                connection,
                "SELECT user_id FROM installation_identity WHERE id = 1",
            )
        return str(row["user_id"]) if row else None

    async def set_installation_user_id(self, user_id: str) -> None:
        async with self._connect() as connection:
            await connection.execute(
                """
                INSERT INTO installation_identity(id, user_id, created_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET user_id = excluded.user_id
                """,
                (user_id, _now()),
            )
            await connection.commit()

    async def get_managed_session(self, provider: str = "composio") -> dict[str, str] | None:
        async with self._connect() as connection:
            row = await self._fetchone(
                connection,
                "SELECT provider, session_id, user_id, updated_at FROM managed_sessions "
                "WHERE provider = ?",
                (provider,),
            )
        return row

    async def set_managed_session(
        self,
        session_id: str,
        user_id: str,
        provider: str = "composio",
    ) -> None:
        async with self._connect() as connection:
            await connection.execute(
                """
                INSERT INTO managed_sessions(provider, session_id, user_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    session_id = excluded.session_id,
                    user_id = excluded.user_id,
                    updated_at = excluded.updated_at
                """,
                (provider, session_id, user_id, _now()),
            )
            await connection.commit()

    async def delete_managed_session(self, provider: str = "composio") -> None:
        async with self._connect() as connection:
            await connection.execute("DELETE FROM managed_sessions WHERE provider = ?", (provider,))
            await connection.commit()

    async def list_managed_accounts(self) -> list[dict[str, Any]]:
        async with self._connect() as connection:
            cursor = await connection.execute("SELECT * FROM managed_accounts ORDER BY toolkit")
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def set_managed_account(
        self,
        toolkit: str,
        account_id: str,
        status: str,
        account_label: str | None = None,
        last_error: str | None = None,
    ) -> None:
        async with self._connect() as connection:
            await connection.execute(
                """
                INSERT INTO managed_accounts(
                    toolkit, account_id, status, account_label, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(toolkit) DO UPDATE SET
                    account_id = excluded.account_id,
                    status = excluded.status,
                    account_label = excluded.account_label,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (toolkit, account_id, status, account_label, last_error, _now()),
            )
            await connection.commit()

    async def delete_managed_account(self, toolkit: str) -> None:
        async with self._connect() as connection:
            await connection.execute("DELETE FROM managed_accounts WHERE toolkit = ?", (toolkit,))
            await connection.commit()

    async def list_file_roots(self) -> list[FileRoot]:
        async with self._connect() as connection:
            cursor = await connection.execute("SELECT * FROM file_roots ORDER BY added_at")
            rows = await cursor.fetchall()
        return [self._file_root(dict(row)) for row in rows]

    async def add_file_root(self, root_id: str, path: str, label: str) -> FileRoot:
        timestamp = _now()
        async with self._connect() as connection:
            await connection.execute(
                "INSERT INTO file_roots(id, path, label, added_at) VALUES (?, ?, ?, ?)",
                (root_id, path, label, timestamp),
            )
            await connection.commit()
        return FileRoot(
            id=root_id,
            path=path,
            label=label,
            exists=True,
            added_at=datetime.fromisoformat(timestamp),
        )

    async def remove_file_root(self, root_id: str) -> None:
        async with self._connect() as connection:
            await connection.execute("DELETE FROM file_roots WHERE id = ?", (root_id,))
            await connection.commit()

    async def set_running(self, run_id: str) -> None:
        await self._update_run(
            run_id,
            status=RunStatus.RUNNING,
            error=None,
        )

    async def set_plan(
        self,
        run_id: str,
        plan: list[dict[str, Any]],
        *,
        approval_required: bool,
    ) -> None:
        await self._update_run(
            run_id,
            plan_json=_json(plan),
            status=RunStatus.WAITING_APPROVAL if approval_required else RunStatus.RUNNING,
            approval_status=(
                ApprovalStatus.PENDING if approval_required else ApprovalStatus.NOT_REQUIRED
            ),
        )

    async def set_approval(
        self,
        run_id: str,
        approval_status: ApprovalStatus,
        feedback: str | None = None,
    ) -> None:
        await self._update_run(
            run_id,
            approval_status=approval_status,
            approval_feedback=feedback,
            status=(
                RunStatus.REJECTED
                if approval_status == ApprovalStatus.REJECTED
                else RunStatus.RUNNING
            ),
        )

    async def claim_resume(self, run_id: str) -> None:
        async with self.maintenance_lock:
            async with self._connect() as connection:
                cursor = await connection.execute(
                    """
                    UPDATE runs SET status = ?, updated_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (RunStatus.RESUMING, _now(), run_id, RunStatus.WAITING_APPROVAL),
                )
                await connection.commit()
        if cursor.rowcount != 1:
            run = await self.get_run(run_id)
            raise RunConflictError(
                f"Run {run_id!r} cannot be resumed from status {run.status.value!r}"
            )

    async def list_unsafe_runs(self) -> list[RunRecord]:
        unsafe_statuses = (
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            RunStatus.RESUMING,
            RunStatus.WAITING_APPROVAL,
        )
        placeholders = ", ".join("?" for _ in unsafe_statuses)
        async with self._connect() as connection:
            # A waiting row is only actionable when LangGraph left a durable
            # checkpoint for its thread. Older crashes could leave the app row
            # behind without a resumable checkpoint; those rows remain visible
            # in history but must not block maintenance forever.
            checkpoint_filter = " AND status <> ?"
            if await self._table_exists(connection, "checkpoints"):
                checkpoint_filter = (
                    " AND (status <> ? OR EXISTS ("
                    "SELECT 1 FROM checkpoints "
                    "WHERE checkpoints.thread_id = runs.thread_id "
                    "AND checkpoints.checkpoint IS NOT NULL))"
                )
            cursor = await connection.execute(
                f"SELECT * FROM runs WHERE status IN ({placeholders}){checkpoint_filter} "
                "ORDER BY created_at DESC",
                (*[status.value for status in unsafe_statuses], RunStatus.WAITING_APPROVAL.value),
            )
            rows = await cursor.fetchall()
        return [self._run_record(dict(row)) for row in rows]

    async def clear_run_history(self) -> dict[str, int]:
        async with self._connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = await connection.execute("SELECT id FROM runs")
                run_rows = await cursor.fetchall()
                run_ids = [row["id"] for row in run_rows]
                counts = {
                    "runs_removed": len(run_ids),
                    "events_removed": 0,
                    "executions_removed": 0,
                    "checkpoints_removed": 0,
                    "writes_removed": 0,
                }
                if run_ids:
                    run_placeholders = ", ".join("?" for _ in run_ids)
                    event_cursor = await connection.execute(
                        f"DELETE FROM run_events WHERE run_id IN ({run_placeholders})",
                        tuple(run_ids),
                    )
                    execution_cursor = await connection.execute(
                        f"DELETE FROM execution_records WHERE run_id IN ({run_placeholders})",
                        tuple(run_ids),
                    )
                    counts["events_removed"] = event_cursor.rowcount
                    counts["executions_removed"] = execution_cursor.rowcount
                    await connection.execute(
                        f"DELETE FROM runs WHERE id IN ({run_placeholders})",
                        tuple(run_ids),
                    )
                for table, count_key in (
                    ("writes", "writes_removed"),
                    ("checkpoints", "checkpoints_removed"),
                ):
                    if not await self._table_exists(connection, table):
                        continue
                    # Clear history removes every run, so every checkpoint and
                    # write is historical by definition. Deleting the whole
                    # tables also cleans rows orphaned by an earlier failure.
                    cursor = await connection.execute(f"DELETE FROM {table}")
                    counts[count_key] = cursor.rowcount
                await connection.commit()
                return counts
            except Exception:
                await connection.rollback()
                raise

    async def finish_run(self, run_id: str, summary: str, *, rejected: bool = False) -> None:
        await self._update_run(
            run_id,
            status=RunStatus.REJECTED if rejected else RunStatus.COMPLETED,
            final_summary=summary,
        )

    async def fail_run(self, run_id: str, error: str) -> None:
        await self._update_run(run_id, status=RunStatus.FAILED, error=error)

    async def append_event(
        self,
        run_id: str,
        event_type: str,
        state: EventState,
        title: str,
        detail: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> TimelineEvent:
        timestamp = _now()
        async with self._connect() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO run_events(
                    run_id, event_type, state, title, detail, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, event_type, state, title, detail, _json(payload or {}), timestamp),
            )
            await connection.commit()
            event_id = cursor.lastrowid
        return TimelineEvent(
            id=event_id,
            run_id=run_id,
            event_type=event_type,
            state=state,
            title=title,
            detail=detail,
            payload=payload or {},
            created_at=datetime.fromisoformat(timestamp),
        )

    async def list_events(self, run_id: str, after_id: int = 0) -> list[TimelineEvent]:
        await self.get_run(run_id)
        async with self._connect() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM run_events
                WHERE run_id = ? AND id > ?
                ORDER BY id
                """,
                (run_id, after_id),
            )
            rows = await cursor.fetchall()
        return [
            TimelineEvent(
                id=row["id"],
                run_id=row["run_id"],
                event_type=row["event_type"],
                state=EventState(row["state"]),
                title=row["title"],
                detail=row["detail"],
                payload=json.loads(row["payload_json"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    async def begin_execution(
        self,
        run_id: str,
        action_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        started_at = _now()
        async with self._connect() as connection:
            try:
                await connection.execute(
                    """
                    INSERT INTO execution_records(
                        run_id, action_id, tool_name, arguments_json, started_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_id, action_id, tool_name, _json(arguments), started_at),
                )
                await connection.commit()
                return {
                    "run_id": run_id,
                    "action_id": action_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "started_at": started_at,
                    "is_new": True,
                    "success": None,
                }
            except aiosqlite.IntegrityError:
                existing = await self._fetchone(
                    connection,
                    """
                    SELECT * FROM execution_records WHERE run_id = ? AND action_id = ?
                    """,
                    (run_id, action_id),
                )
                if existing is None:
                    raise
                return {**self._execution_record(existing), "is_new": False}

    async def complete_execution(
        self,
        run_id: str,
        action_id: str,
        *,
        success: bool,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        async with self._connect() as connection:
            await connection.execute(
                """
                UPDATE execution_records
                SET result_json = ?, success = ?, error = ?, completed_at = ?
                WHERE run_id = ? AND action_id = ?
                """,
                (_json(result), int(success), error, _now(), run_id, action_id),
            )
            await connection.commit()

    async def set_verification(
        self,
        run_id: str,
        action_id: str,
        verification: dict[str, Any],
    ) -> None:
        async with self._connect() as connection:
            await connection.execute(
                """
                UPDATE execution_records SET verification_json = ?
                WHERE run_id = ? AND action_id = ?
                """,
                (_json(verification), run_id, action_id),
            )
            await connection.commit()

    async def list_executions(self, run_id: str) -> list[dict[str, Any]]:
        async with self._connect() as connection:
            cursor = await connection.execute(
                "SELECT * FROM execution_records WHERE run_id = ? ORDER BY id", (run_id,)
            )
            rows = await cursor.fetchall()
        return [self._execution_record(dict(row)) for row in rows]

    async def get_preferences(self) -> PreferenceSet:
        async with self._connect() as connection:
            row = await self._fetchone(
                connection,
                "SELECT * FROM preferences WHERE profile_id = 'default'",
            )
        if row is None:
            return PreferenceSet()
        return PreferenceSet(
            preferred_focus_block_minutes=row["preferred_focus_block_minutes"],
            avoid_scheduling_after=row["avoid_scheduling_after"],
            preferred_task_due_time=row["preferred_task_due_time"],
        )

    async def update_preferences(self, preferences: PreferenceSet) -> PreferenceSet:
        async with self._connect() as connection:
            await connection.execute(
                """
                UPDATE preferences SET
                    preferred_focus_block_minutes = ?,
                    avoid_scheduling_after = ?,
                    preferred_task_due_time = ?,
                    updated_at = ?
                WHERE profile_id = 'default'
                """,
                (
                    preferences.preferred_focus_block_minutes,
                    preferences.avoid_scheduling_after,
                    preferences.preferred_task_due_time,
                    _now(),
                ),
            )
            await connection.commit()
        return preferences

    async def _update_run(self, run_id: str, **updates: Any) -> None:
        allowed = {
            "status",
            "approval_status",
            "approval_feedback",
            "plan_json",
            "final_summary",
            "error",
        }
        if not updates or not set(updates).issubset(allowed):
            raise ValueError("Invalid run update")
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{column} = ?" for column in updates)
        values = [str(value) if hasattr(value, "value") else value for value in updates.values()]
        async with self._connect() as connection:
            cursor = await connection.execute(
                f"UPDATE runs SET {assignments} WHERE id = ?",
                (*values, run_id),
            )
            await connection.commit()
        if cursor.rowcount != 1:
            raise RunNotFoundError(f"Run {run_id!r} was not found")

    def _run_record(self, row: dict[str, Any]) -> RunRecord:
        return RunRecord(
            id=row["id"],
            thread_id=row["thread_id"],
            user_request=row["user_request"],
            status=RunStatus(row["status"]),
            approval_status=ApprovalStatus(row["approval_status"]),
            approval_feedback=row["approval_feedback"],
            plan=[PlanAction.model_validate(item) for item in json.loads(row["plan_json"])],
            final_summary=row["final_summary"],
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _file_root(self, row: dict[str, Any]) -> FileRoot:
        return FileRoot(
            id=row["id"],
            path=row["path"],
            label=row["label"],
            exists=Path(row["path"]).is_dir(),
            added_at=datetime.fromisoformat(row["added_at"]),
        )

    def _execution_record(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "action_id": row["action_id"],
            "tool_name": row["tool_name"],
            "arguments": json.loads(row["arguments_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "success": bool(row["success"]) if row["success"] is not None else None,
            "error": row["error"],
            "verification": (
                json.loads(row["verification_json"]) if row["verification_json"] else None
            ),
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }

    @asynccontextmanager
    async def _connect(self):
        connection = await aiosqlite.connect(self.database_path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            await connection.close()

    async def _fetchone(
        self,
        connection: aiosqlite.Connection,
        query: str,
        parameters: tuple[Any, ...] = (),
    ) -> dict[str, Any] | None:
        cursor = await connection.execute(query, parameters)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def _table_exists(self, connection: aiosqlite.Connection, table_name: str) -> bool:
        row = await self._fetchone(
            connection,
            "SELECT 1 AS present FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        )
        return row is not None
