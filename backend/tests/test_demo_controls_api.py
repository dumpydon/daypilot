from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import router
from backend.app.domain.models import DemoResetResponse, RunHistoryClearResponse


class StubDemoWorkspace:
    async def reset_demo_workspace(self) -> DemoResetResponse:
        return DemoResetResponse(
            services=["Mail", "Calendar", "Tasks", "Files", "X"],
            preserved_runs=2,
            message="Demo workspace restored.",
        )

    async def clear_run_history(self) -> RunHistoryClearResponse:
        return RunHistoryClearResponse(
            runs_removed=2,
            events_removed=4,
            executions_removed=1,
            checkpoints_removed=3,
            writes_removed=2,
            message="Removed 2 saved run(s).",
        )


def test_demo_controls_use_separate_application_endpoints() -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.demo_workspace = StubDemoWorkspace()
    client = TestClient(app)

    reset = client.post("/api/demo-workspace/reset")
    clear = client.post("/api/run-history/clear")

    assert reset.status_code == 200
    assert reset.json()["status"] == "reset"
    assert reset.json()["services"] == ["Mail", "Calendar", "Tasks", "Files", "X"]
    assert clear.status_code == 200
    assert clear.json()["status"] == "cleared"
    assert clear.json()["runs_removed"] == 2
