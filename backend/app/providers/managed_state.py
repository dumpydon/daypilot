from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4


class ManagedStateStore:
    """Synchronous, non-secret Composio session/account metadata storage.

    Composio owns provider credentials. DayPilot stores only the stable local
    user identity, session ID, and redacted connection status needed to route
    its MCP adapters.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.executescript(
                """
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
            )

    def ensure_user_id(self) -> str:
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT user_id FROM installation_identity WHERE id = 1"
            ).fetchone()
            if row:
                return str(row[0])
            user_id = f"daypilot-{uuid4().hex}"
            connection.execute(
                "INSERT INTO installation_identity(id, user_id, created_at) "
                "VALUES (1, ?, datetime('now'))",
                (user_id,),
            )
            connection.commit()
            return user_id

    @staticmethod
    def _session_key(toolkit: str) -> str:
        return f"composio:{toolkit}"

    def session(self, toolkit: str) -> dict[str, str] | None:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT provider, session_id, user_id, updated_at FROM managed_sessions "
                "WHERE provider = ?",
                (self._session_key(toolkit),),
            ).fetchone()
        return dict(row) if row else None

    def set_session(self, toolkit: str, session_id: str, user_id: str) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO managed_sessions(provider, session_id, user_id, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(provider) DO UPDATE SET
                    session_id = excluded.session_id,
                    user_id = excluded.user_id,
                    updated_at = excluded.updated_at
                """,
                (self._session_key(toolkit), session_id, user_id),
            )
            connection.commit()

    def delete_session(self, toolkit: str) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "DELETE FROM managed_sessions WHERE provider = ?",
                (self._session_key(toolkit),),
            )
            connection.commit()

    def account(self, toolkit: str) -> dict[str, str | None] | None:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT toolkit, account_id, status, account_label, last_error, updated_at "
                "FROM managed_accounts WHERE toolkit = ?",
                (toolkit,),
            ).fetchone()
        return dict(row) if row else None

    def set_account(
        self,
        toolkit: str,
        account_id: str,
        status: str,
        account_label: str | None = None,
        last_error: str | None = None,
    ) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO managed_accounts(
                    toolkit, account_id, status, account_label, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(toolkit) DO UPDATE SET
                    account_id = excluded.account_id,
                    status = excluded.status,
                    account_label = excluded.account_label,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (toolkit, account_id, status, account_label, last_error),
            )
            connection.commit()

    def delete_account(self, toolkit: str) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("DELETE FROM managed_accounts WHERE toolkit = ?", (toolkit,))
            connection.commit()
