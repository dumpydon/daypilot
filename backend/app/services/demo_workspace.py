from __future__ import annotations

import asyncio

from backend.app.config import Settings
from backend.app.domain.errors import (
    DemoModeRequiredError,
    DemoWorkspaceError,
    RunConflictError,
)
from backend.app.domain.models import DemoResetResponse, RunHistoryClearResponse
from backend.app.persistence.repository import DayPilotRepository
from mcp_servers.common.database import initialize_demo_database

DEMO_SERVICES = ["Mail", "Calendar", "Tasks", "Files", "X"]


class DemoWorkspaceService:
    """Application-level controls for the known local demo stores only."""

    def __init__(self, settings: Settings, repository: DayPilotRepository) -> None:
        self.settings = settings
        self.repository = repository

    async def reset_demo_workspace(self) -> DemoResetResponse:
        self._require_demo_mode()
        async with self.repository.maintenance_lock:
            unsafe_runs = await self.repository.list_unsafe_runs()
            if unsafe_runs:
                raise RunConflictError(self._unsafe_message("reset demo workspace", unsafe_runs))
            try:
                await asyncio.to_thread(
                    initialize_demo_database,
                    self.settings.database_target,
                    self.settings.daypilot_timezone,
                    force_reset=True,
                )
            except Exception as exc:
                raise DemoWorkspaceError(f"Demo workspace reset failed: {exc}") from exc
            preserved_runs = len(await self.repository.list_runs(100))
        return DemoResetResponse(
            services=DEMO_SERVICES,
            preserved_runs=preserved_runs,
            message="Demo workspace restored.",
        )

    async def clear_run_history(self) -> RunHistoryClearResponse:
        async with self.repository.maintenance_lock:
            unsafe_runs = await self.repository.list_unsafe_runs()
            if unsafe_runs:
                raise RunConflictError(self._unsafe_message("clear run history", unsafe_runs))
            try:
                counts = await self.repository.clear_run_history()
            except Exception as exc:
                raise DemoWorkspaceError(f"Run history clear failed: {exc}") from exc
        return RunHistoryClearResponse(
            **counts,
            message=(
                f"Removed {counts['runs_removed']} saved run(s); demo service data and "
                "preferences were preserved."
            ),
        )

    def _require_demo_mode(self) -> None:
        if not self.settings.daypilot_demo_mode:
            raise DemoModeRequiredError(
                "Reset demo workspace is available only when DAYPILOT_DEMO_MODE is enabled."
            )

    @staticmethod
    def _unsafe_message(operation: str, runs) -> str:
        sample = ", ".join(run.id for run in runs[:3])
        suffix = "…" if len(runs) > 3 else ""
        return (
            f"Cannot {operation} while a run is active or awaiting approval "
            f"({sample}{suffix}). Finish or reject those runs first."
        )
