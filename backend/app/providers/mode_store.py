from __future__ import annotations

from backend.app.persistence.database import DatabaseTarget, connect_sync


class ProviderModeStore:
    """Small persisted selection boundary shared by the API and MCP children."""

    SERVICES = ("mail", "calendar", "tasks", "files", "x")

    def __init__(self, database_target: DatabaseTarget, defaults: dict[str, str]) -> None:
        self.database_target = database_target
        self.defaults = defaults

    def get(self, service: str) -> str:
        if service not in self.SERVICES:
            raise ValueError(f"Unknown provider service {service!r}")
        try:
            with connect_sync(self.database_target) as connection:
                row = connection.execute(
                    "SELECT mode FROM provider_modes WHERE service = ?", (service,)
                ).fetchone()
        except Exception:
            row = None
        return str(row["mode"]) if row else self.defaults[service]

    def set(self, service: str, mode: str) -> None:
        if service not in self.SERVICES:
            raise ValueError(f"Unknown provider service {service!r}")
        with connect_sync(self.database_target) as connection:
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
