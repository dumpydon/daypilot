from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from backend.app.domain.errors import PlanRevisionError, RunConflictError
from backend.app.domain.models import (
    ApprovalStatus,
    EventState,
    ExecutionResult,
    PreferenceSet,
    RunAccepted,
    RunDetail,
    RunStatus,
    ToolMetadata,
    UserIntent,
)
from backend.app.graph.state import DayPilotState
from backend.app.mcp.gateway import MCPGateway
from backend.app.persistence.repository import DayPilotRepository
from backend.app.services.receipts import build_resource_receipts

TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.REJECTED, RunStatus.FAILED}
SETTLED_STATUSES = {*TERMINAL_STATUSES, RunStatus.WAITING_APPROVAL}
REVISION_TIMEOUT_SECONDS = 45


class RunCoordinator:
    """Owns background graph invocations while checkpoints own durable workflow position."""

    def __init__(self, graph: Any, repository: DayPilotRepository, gateway: MCPGateway) -> None:
        self.graph = graph
        self.repository = repository
        self.gateway = gateway
        self._tasks: set[asyncio.Task[Any]] = set()
        self._revision_locks: dict[str, asyncio.Lock] = {}

    async def start_run(self, user_request: str) -> RunAccepted:
        run_id = f"run-{uuid4().hex[:12]}"
        thread_id = f"thread-{uuid4().hex}"
        await self.repository.create_run(run_id, thread_id, user_request)
        await self.repository.append_event(
            run_id,
            "request_received",
            EventState.COMPLETED,
            "Request received",
            user_request,
        )
        preferences = await self.repository.get_preferences()
        now = datetime.now(UTC).isoformat()
        state: DayPilotState = {
            "run_id": run_id,
            "thread_id": thread_id,
            "user_request": user_request,
            "context": {server_name: [] for server_name in self.gateway.connections},
            "plan": [],
            "read_actions": [],
            "write_actions": [],
            "approval_status": ApprovalStatus.NOT_REQUIRED.value,
            "approval_feedback": None,
            "approved_plan_hash": None,
            "plan_revision": 0,
            "plan_hash": None,
            "execution_results": [],
            "verification_results": [],
            "errors": [],
            "final_summary": None,
            "preferences": preferences.model_dump(mode="json"),
            "reasoning_mode": "pending",
            "created_at": now,
            "updated_at": now,
        }
        self._spawn(self._invoke_initial(run_id, thread_id, state))
        return RunAccepted(id=run_id, status=RunStatus.QUEUED)

    async def resume(
        self,
        run_id: str,
        decision: str,
        feedback: str | None = None,
    ) -> RunAccepted:
        run = await self.repository.get_run(run_id)
        await self.repository.claim_resume(run_id)
        self._spawn(self._invoke_resume(run_id, run.thread_id, decision, feedback))
        return RunAccepted(id=run_id, status=RunStatus.RESUMING)

    async def get_detail(self, run_id: str) -> RunDetail:
        run = await self.repository.get_run(run_id)
        snapshot = await self.graph.aget_state(self._config(run.thread_id))
        values = dict(snapshot.values) if snapshot and snapshot.values else {}
        events = await self.repository.list_events(run_id)
        interrupts = getattr(snapshot, "interrupts", ()) if snapshot else ()
        interrupt_payload = interrupts[0].value if interrupts else None
        executions = [
            ExecutionResult.model_validate(result) for result in values.get("execution_results", [])
        ]
        verification_results = values.get("verification_results", [])
        return RunDetail(
            **run.model_dump(),
            intent=(UserIntent.model_validate(values["intent"]) if values.get("intent") else None),
            available_tools=[
                ToolMetadata.model_validate(tool) for tool in values.get("available_tools", [])
            ],
            context=values.get("context", {}),
            execution_results=executions,
            verification_results=verification_results,
            created_outputs=build_resource_receipts(
                [execution.model_dump(mode="json") for execution in executions],
                verification_results,
            ),
            events=events,
            preferences=PreferenceSet.model_validate(
                values.get("preferences", PreferenceSet().model_dump())
            ),
            reasoning_mode=values.get("reasoning_mode", "pending"),
            interrupt_payload=interrupt_payload,
            plan_revision=values.get("plan_revision", 0),
            plan_hash=values.get("plan_hash"),
        )

    async def revise(
        self,
        run_id: str,
        feedback: str,
        expected_revision: int,
    ) -> RunDetail:
        lock = self._revision_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            current = await self.get_detail(run_id)
            if current.plan_revision > expected_revision and current.approval_feedback == feedback:
                return current
            if current.status != RunStatus.WAITING_APPROVAL:
                raise RunConflictError(
                    f"Run {run_id!r} cannot be revised from status {current.status.value!r}"
                )
            if current.plan_revision != expected_revision:
                raise RunConflictError(
                    f"Plan revision changed from {expected_revision} to "
                    f"{current.plan_revision}; refresh before revising"
                )

            await self.repository.claim_resume(run_id)
            try:
                async with asyncio.timeout(REVISION_TIMEOUT_SECONDS):
                    await self.graph.ainvoke(
                        Command(resume={"decision": "edit", "feedback": feedback}),
                        config=self._config(current.thread_id),
                    )
            except TimeoutError as exc:
                message = f"Plan revision timed out after {REVISION_TIMEOUT_SECONDS} seconds"
                await self._record_failure(run_id, PlanRevisionError(message))
                raise PlanRevisionError(message) from exc
            except Exception as exc:
                await self._record_failure(run_id, exc)
                raise PlanRevisionError(f"Plan revision failed: {exc}") from exc

            revised = await self.get_detail(run_id)
            if revised.status == RunStatus.FAILED:
                raise PlanRevisionError(revised.error or "Plan revision failed")
            if (
                revised.status != RunStatus.WAITING_APPROVAL
                or revised.plan_revision != expected_revision + 1
            ):
                raise PlanRevisionError("Plan revision did not return to the approval checkpoint")
            return revised

    async def wait_until_settled(self, run_id: str, max_wait_seconds: float = 15) -> RunDetail:
        deadline = asyncio.get_running_loop().time() + max_wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            run = await self.repository.get_run(run_id)
            if run.status in SETTLED_STATUSES:
                detail = await self.get_detail(run_id)
                if run.status != RunStatus.WAITING_APPROVAL or (
                    detail.plan_revision > 0 and detail.interrupt_payload is not None
                ):
                    return detail
            await asyncio.sleep(0.025)
        raise TimeoutError(f"Run {run_id} did not settle within {max_wait_seconds} seconds")

    async def shutdown(self) -> None:
        if not self._tasks:
            return
        done, pending = await asyncio.wait(self._tasks, timeout=5)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.exception() if not task.cancelled() else None

    async def _invoke_initial(
        self,
        run_id: str,
        thread_id: str,
        state: DayPilotState,
    ) -> None:
        await self.repository.set_running(run_id)
        try:
            await self.graph.ainvoke(state, config=self._config(thread_id))
        except Exception as exc:
            await self._record_failure(run_id, exc)

    async def _invoke_resume(
        self,
        run_id: str,
        thread_id: str,
        decision: str,
        feedback: str | None,
    ) -> None:
        try:
            await self.graph.ainvoke(
                Command(resume={"decision": decision, "feedback": feedback}),
                config=self._config(thread_id),
            )
        except Exception as exc:
            await self._record_failure(run_id, exc)

    async def _record_failure(self, run_id: str, exc: Exception) -> None:
        await self.repository.fail_run(run_id, str(exc))
        await self.repository.append_event(
            run_id,
            "run_failed",
            EventState.FAILED,
            "Run failed safely",
            str(exc),
        )

    def _spawn(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _config(self, thread_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": thread_id}}
