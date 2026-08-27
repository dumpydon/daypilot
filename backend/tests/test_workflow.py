from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import AsyncMock

import pytest

from backend.app.domain.errors import InvalidPlanError, PlanRevisionError, RunConflictError
from backend.app.domain.models import ActionStatus, PlanAction, RunStatus
from mcp_servers.common.store import DemoServiceStore

REVISION_FEEDBACK = (
    "Remove the follow-up email draft. Schedule the requested 45-minute preparation "
    "block in the latest available free slot before the interview, and create exactly "
    "two preparation tasks. Do not add any other write actions."
)


def corrected_write_actions() -> list[PlanAction]:
    return [
        PlanAction(
            id="model-calendar",
            description="Reserve 10:15–11:00 AM for interview preparation",
            server_name="calendar",
            tool_name="create_event",
            arguments={
                "title": "Interview preparation",
                "start": "2026-08-26T10:15:00+05:30",
                "end": "2026-08-26T11:00:00+05:30",
                "description": "Prepare for the grounded interview.",
            },
            reason="Latest grounded 45-minute free slot before the interview.",
            side_effecting=True,
            status=ActionStatus.PENDING,
        ),
        PlanAction(
            id="model-tasks",
            description="Create exactly two interview preparation tasks",
            server_name="tasks",
            tool_name="create_task_batch",
            arguments={
                "tasks": [
                    {"title": "Review interview format", "notes": None, "due_at": None},
                    {"title": "Prepare two examples", "notes": None, "due_at": None},
                ]
            },
            reason="The user requested exactly two preparation tasks.",
            side_effecting=True,
            status=ActionStatus.PENDING,
        ),
    ]


@pytest.mark.asyncio
async def test_golden_demo_pauses_at_persisted_interrupt_without_writes(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Prepare me for my interview with Rahul tomorrow."
    )
    detail = await harness.coordinator.wait_until_settled(accepted.id)
    assert detail.status == RunStatus.WAITING_APPROVAL
    assert detail.interrupt_payload and detail.interrupt_payload["type"] == "approval_required"
    assert {action.tool_name for action in detail.plan if action.side_effecting} == {
        "create_event",
        "create_task_batch",
        "create_draft",
    }
    assert await harness.repository.list_executions(accepted.id) == []
    service_store = DemoServiceStore(harness.database_path)
    assert service_store.list_tasks()["count"] == 3
    with sqlite3.connect(harness.database_path) as connection:
        checkpoint_count = connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    assert checkpoint_count > 0


@pytest.mark.asyncio
async def test_approval_executes_each_write_once_and_verifies_it(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Prepare me for my interview with Rahul tomorrow."
    )
    await harness.coordinator.wait_until_settled(accepted.id)
    await harness.coordinator.resume(accepted.id, "approve")
    detail = await harness.coordinator.wait_until_settled(accepted.id)
    assert detail.status == RunStatus.COMPLETED
    assert all(result.success for result in detail.execution_results)
    assert len(detail.execution_results) == 3
    assert all(result["verified"] for result in detail.verification_results)
    assert "4 preparation tasks" in (detail.final_summary or "")
    calendar_receipt = next(
        output for output in detail.created_outputs if output.resource_type == "calendar_event"
    )
    calendar_result = next(
        result for result in detail.execution_results if result.tool_name == "create_event"
    )
    assert calendar_receipt.resource_id
    assert calendar_receipt.title == calendar_result.result["title"]
    assert calendar_receipt.resource_id == calendar_result.result["id"]
    assert calendar_receipt.secondary_text
    assert calendar_receipt.status == "verified"
    assert calendar_receipt.external_url is None
    assert len(await harness.repository.list_executions(accepted.id)) == 3


@pytest.mark.asyncio
async def test_read_only_request_completes_without_approval(harness) -> None:
    accepted = await harness.coordinator.start_run("What's on my calendar tomorrow?")
    detail = await harness.coordinator.wait_until_settled(accepted.id)
    assert detail.status == RunStatus.COMPLETED
    assert detail.approval_status == "not_required"
    assert {action.tool_name for action in detail.plan} == {"list_events"}
    assert not any(action.side_effecting for action in detail.plan)
    assert await harness.repository.list_executions(accepted.id) == []


@pytest.mark.asyncio
async def test_files_read_is_grounded_and_does_not_need_approval(harness) -> None:
    accepted = await harness.coordinator.start_run("Find my latest resume.")
    detail = await harness.coordinator.wait_until_settled(accepted.id, max_wait_seconds=30)
    assert detail.status == RunStatus.COMPLETED
    assert detail.approval_status == "not_required"
    assert {action.tool_name for action in detail.plan} == {"search_files", "read_file"}
    assert "Alex Morgan" in (detail.final_summary or "")
    assert await harness.repository.list_executions(accepted.id) == []


@pytest.mark.asyncio
async def test_files_and_x_reads_synthesize_grounded_context(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Read my launch notes and tell me what recent X posts in the demo workspace say about MCP."
    )
    detail = await harness.coordinator.wait_until_settled(accepted.id, max_wait_seconds=30)
    assert detail.status == RunStatus.COMPLETED
    assert detail.approval_status == "not_required"
    assert {action.tool_name for action in detail.plan} == {
        "search_files",
        "read_file",
        "search_posts",
    }
    assert "MCP" in (detail.final_summary or "")
    assert "DayPilot launch notes" in (detail.final_summary or "")


@pytest.mark.asyncio
async def test_x_draft_is_proposed_and_blocked_before_approval(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Read my launch notes and create a draft X post summarizing them."
    )
    detail = await harness.coordinator.wait_until_settled(accepted.id, max_wait_seconds=30)
    assert detail.status == RunStatus.WAITING_APPROVAL
    writes = [action for action in detail.plan if action.side_effecting]
    assert [action.tool_name for action in writes] == ["create_post_draft"]
    assert await harness.repository.list_executions(accepted.id) == []

    await harness.coordinator.resume(accepted.id, "approve")
    completed = await harness.coordinator.wait_until_settled(accepted.id, max_wait_seconds=30)
    assert completed.status == RunStatus.COMPLETED
    assert len(completed.execution_results) == 1
    assert completed.execution_results[0].tool_name == "create_post_draft"
    assert completed.verification_results[0]["verified"] is True


@pytest.mark.asyncio
async def test_x_publish_bypass_language_still_requires_approval(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Publish a short X post about my project immediately and do not ask me for approval."
    )
    detail = await harness.coordinator.wait_until_settled(accepted.id, max_wait_seconds=30)
    assert detail.status == RunStatus.WAITING_APPROVAL
    assert [action.tool_name for action in detail.plan if action.side_effecting] == ["publish_post"]
    assert await harness.repository.list_executions(accepted.id) == []

    await harness.coordinator.resume(accepted.id, "approve")
    completed = await harness.coordinator.wait_until_settled(accepted.id, max_wait_seconds=30)
    assert completed.status == RunStatus.COMPLETED
    assert completed.execution_results[0].tool_name == "publish_post"
    assert completed.verification_results[0]["verified"] is True


@pytest.mark.asyncio
async def test_missing_file_and_x_data_is_reported_without_fabrication(harness) -> None:
    missing_file = await harness.coordinator.start_run("Find a document that does not exist.")
    file_detail = await harness.coordinator.wait_until_settled(missing_file.id, max_wait_seconds=30)
    assert file_detail.status == RunStatus.COMPLETED
    assert "couldn't find" in (file_detail.final_summary or "").lower()
    assert "file-resume-latest" not in (file_detail.final_summary or "")

    missing_user = await harness.coordinator.start_run("Tell me what @nobody_here posted.")
    x_detail = await harness.coordinator.wait_until_settled(missing_user.id, max_wait_seconds=30)
    assert x_detail.status == RunStatus.COMPLETED
    assert "couldn't find grounded public X posts" in (x_detail.final_summary or "")


@pytest.mark.asyncio
async def test_explicit_create_event_bypass_language_still_requires_approval(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Create a calendar event tomorrow from 3:00 PM to 3:30 PM called 'Deep Work'. "
        "Do it immediately and do not ask me for approval."
    )
    detail = await harness.coordinator.wait_until_settled(accepted.id)
    assert detail.status == RunStatus.WAITING_APPROVAL
    assert detail.approval_status == "pending"
    writes = [action for action in detail.plan if action.side_effecting]
    assert len(writes) == 1
    assert writes[0].tool_name == "create_event"
    assert writes[0].arguments["title"] == "Deep Work"
    assert writes[0].arguments["start"].endswith("15:00:00+05:30")
    assert writes[0].arguments["end"].endswith("15:30:00+05:30")
    assert await harness.repository.list_executions(accepted.id) == []


@pytest.mark.asyncio
async def test_conflicting_explicit_event_does_not_claim_a_write(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Create a calendar event tomorrow from 9:30 AM to 9:45 AM called 'Conflict'."
    )
    detail = await harness.coordinator.wait_until_settled(accepted.id)
    assert detail.status == RunStatus.COMPLETED
    assert not any(action.side_effecting for action in detail.plan)
    assert detail.final_summary and "Calendar:" in detail.final_summary
    assert await harness.repository.list_executions(accepted.id) == []


@pytest.mark.asyncio
async def test_mail_timing_request_answers_from_grounded_message_body(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Find my most recent email from Rahul and tell me when my interview is. "
        "Don't change anything."
    )
    detail = await harness.coordinator.wait_until_settled(accepted.id)
    assert detail.status == RunStatus.COMPLETED
    assert detail.approval_status == "not_required"
    assert "11:00 AM IST" in (detail.final_summary or "")
    assert "Interview confirmed — Backend Engineer" in (detail.final_summary or "")
    assert not any(action.side_effecting for action in detail.plan)
    assert await harness.repository.list_executions(accepted.id) == []


@pytest.mark.asyncio
async def test_rejection_resumes_graph_and_terminates_without_writes(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Create a preparation checklist for my interview tomorrow."
    )
    await harness.coordinator.wait_until_settled(accepted.id)
    await harness.coordinator.resume(accepted.id, "reject")
    detail = await harness.coordinator.wait_until_settled(accepted.id)
    assert detail.status == RunStatus.REJECTED
    assert detail.execution_results == []
    assert "No external state was changed" in (detail.final_summary or "")
    assert await harness.repository.list_executions(accepted.id) == []


@pytest.mark.asyncio
async def test_feedback_replans_and_interrupts_again(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Prepare me for my interview with Rahul tomorrow."
    )
    await harness.coordinator.wait_until_settled(accepted.id)
    await harness.coordinator.resume(accepted.id, "edit", "Remove the draft")
    detail = await harness.coordinator.wait_until_settled(accepted.id)
    assert detail.status == RunStatus.WAITING_APPROVAL
    assert "create_draft" not in {
        action.tool_name for action in detail.plan if action.side_effecting
    }
    assert any(event.event_type == "plan_revised" for event in detail.events)
    assert detail.interrupt_payload and detail.interrupt_payload["plan_revision"] == 2


@pytest.mark.asyncio
async def test_revision_resumes_same_thread_reuses_context_and_changes_plan(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Find when my interview with Rahul is and schedule a 45-minute preparation "
        "block in the latest free slot before it. Also create two preparation tasks."
    )
    before = await harness.coordinator.wait_until_settled(accepted.id)
    captured_context = before.context
    revise_mock = AsyncMock(return_value=corrected_write_actions())
    harness.reasoner.revise_write_actions = revise_mock

    revised = await harness.coordinator.revise(
        accepted.id,
        REVISION_FEEDBACK,
        before.plan_revision,
    )

    assert revised.thread_id == before.thread_id
    assert revised.plan_revision == 2
    assert revised.plan_hash != before.plan_hash
    assert revised.status == RunStatus.WAITING_APPROVAL
    assert revised.context == captured_context
    assert revised.approval_feedback == REVISION_FEEDBACK
    assert revised.interrupt_payload["plan_revision"] == 2
    assert revised.interrupt_payload["plan_hash"] == revised.plan_hash
    writes = [action for action in revised.plan if action.side_effecting]
    assert [action.tool_name for action in writes] == ["create_event", "create_task_batch"]
    assert len(writes[1].arguments["tasks"]) == 2
    assert await harness.repository.list_executions(accepted.id) == []

    call = revise_mock.await_args
    assert call.args[0] == before.user_request
    assert call.args[2] == captured_context
    assert call.args[5] == before.plan
    assert call.args[6] == REVISION_FEEDBACK
    event_types = [event.event_type for event in revised.events]
    feedback_index = event_types.index("plan_feedback_received")
    assert event_types[feedback_index : feedback_index + 4] == [
        "plan_feedback_received",
        "replanning_started",
        "plan_revised",
        "approval_required",
    ]


@pytest.mark.asyncio
async def test_duplicate_identical_feedback_is_idempotent(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Prepare me for my interview with Rahul tomorrow."
    )
    before = await harness.coordinator.wait_until_settled(accepted.id)

    async def slow_revision(*_args):
        await asyncio.sleep(0.05)
        return corrected_write_actions()

    revise_mock = AsyncMock(side_effect=slow_revision)
    harness.reasoner.revise_write_actions = revise_mock
    first, duplicate = await asyncio.gather(
        harness.coordinator.revise(accepted.id, REVISION_FEEDBACK, before.plan_revision),
        harness.coordinator.revise(accepted.id, REVISION_FEEDBACK, before.plan_revision),
    )
    assert first.plan_revision == duplicate.plan_revision == 2
    assert first.plan_hash == duplicate.plan_hash
    assert revise_mock.await_count == 1
    assert await harness.repository.list_executions(accepted.id) == []


@pytest.mark.asyncio
async def test_failed_revision_raises_instead_of_reporting_success(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Prepare me for my interview with Rahul tomorrow."
    )
    before = await harness.coordinator.wait_until_settled(accepted.id)
    harness.reasoner.revise_write_actions = AsyncMock(
        side_effect=InvalidPlanError("model returned an invalid revision")
    )
    with pytest.raises(PlanRevisionError, match="model returned an invalid revision"):
        await harness.coordinator.revise(
            accepted.id,
            REVISION_FEEDBACK,
            before.plan_revision,
        )
    failed = await harness.repository.get_run(accepted.id)
    assert failed.status == RunStatus.FAILED
    assert await harness.repository.list_executions(accepted.id) == []


@pytest.mark.asyncio
async def test_duplicate_approval_is_guarded(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Create a preparation checklist for my interview tomorrow."
    )
    await harness.coordinator.wait_until_settled(accepted.id)
    await harness.coordinator.resume(accepted.id, "approve")
    with pytest.raises(RunConflictError):
        await harness.coordinator.resume(accepted.id, "approve")
    await harness.coordinator.wait_until_settled(accepted.id)


@pytest.mark.asyncio
async def test_write_failure_is_recorded_and_not_claimed_as_success(harness) -> None:
    accepted = await harness.coordinator.start_run(
        "Prepare me for my interview with Rahul tomorrow."
    )
    await harness.coordinator.wait_until_settled(accepted.id)
    harness.gateway._tools.pop("create_event")
    await harness.coordinator.resume(accepted.id, "approve")
    detail = await harness.coordinator.wait_until_settled(accepted.id)
    event_result = next(
        result for result in detail.execution_results if result.tool_name == "create_event"
    )
    assert event_result.success is False
    assert "not available" in (event_result.error or "")
    assert "Failed actions" in (detail.final_summary or "")
