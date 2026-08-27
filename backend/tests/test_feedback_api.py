from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import router
from backend.app.domain.errors import PlanRevisionError
from backend.app.main import plan_revision_failed


class FailingCoordinator:
    async def revise(self, run_id: str, feedback: str, expected_revision: int):
        raise PlanRevisionError(
            f"Plan revision {expected_revision} for {run_id} failed: {feedback}"
        )


def test_feedback_endpoint_returns_error_when_replanning_fails() -> None:
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(PlanRevisionError, plan_revision_failed)
    app.state.coordinator = FailingCoordinator()
    app.state.repository = object()
    app.state.gateway = object()
    app.state.settings = object()

    response = TestClient(app).post(
        "/api/runs/run-test/feedback",
        json={"feedback": "Create exactly two tasks", "plan_revision": 1},
    )

    assert response.status_code == 500
    assert "Plan revision 1" in response.json()["detail"]
