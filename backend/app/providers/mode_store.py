from __future__ import annotations

import sqlite3
from pathlib import Path


class ProviderModeStore:
    """Small persisted selection boundary shared by the API and MCP children."""

    SERVICES = ("mail", "calendar", "tasks", "files", "x")

    def __init__(self, database_path: Path, defaults: dict[str, str]) -> None:
        self.database_path = database_path
        self.defaults = defaults

    def get(self, service: str) -> str:
        if service not in self.SERVICES:
            raise ValueError(f"Unknown provider service {service!r}")
        try:
            with sqlite3.connect(self.database_path) as connection:
                row = connection.execute(
                    "SELECT mode FROM provider_modes WHERE service = ?", (service,)
                ).fetchone()
        except sqlite3.Error:
            row = None
        return str(row[0]) if row else self.defaults[service]

    def set(self, service: str, mode: str) -> None:
        if service not in self.SERVICES:
            raise ValueError(f"Unknown provider service {service!r}")
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO provider_modes(service, mode, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(service) DO UPDATE SET
                    mode = excluded.mode,
                    updated_at = excluded.updated_at
                """,
                (service, mode),
            )
            connection.commit()
