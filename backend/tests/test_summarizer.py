from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from backend.app.config import Settings
from backend.app.domain.models import (
    ActionStatus,
    PlanAction,
    PlanningProposal,
    PreferenceSet,
    ProposedToolCall,
    ReadCallPlan,
    RiskLevel,
    ToolMetadata,
    UserIntent,
)
from backend.app.services.reasoner import OpenAIReasoner
from backend.app.services.summarizer import summarize_read_only


def mail_context(body: str) -> dict[str, list[dict[str, Any]]]:
    return {
        "mail": [
            {
                "tool_name": "get_thread",
                "success": True,
                "result": {
                    "id": "thread-grounded",
                    "subject": "Interview confirmed — Backend Engineer",
                    "updated_at": "2026-08-25T14:35:00+05:30",
                    "messages": [
                        {
                            "id": "message-grounded",
                            "sender": "recruiter@example.test",
                            "sent_at": "2026-08-25T13:35:00+05:30",
                            "subject": "Interview confirmed — Backend Engineer",
                            "body": body,
                        }
                    ],
                },
            }
        ],
        "calendar": [],
        "tasks": [],
    }


def intent() -> UserIntent:
    return UserIntent(
        goal="Determine the interview time from the latest email.",
        people=["Recruiter"],
        requested_outcomes=["Report interview time"],
        information_needed=["mail"],
    )


@dataclass
class FakeMessage:
    content: str


class CapturingSummaryModel:
    def __init__(self) -> None:
        self.prompt = ""
        self.config: dict[str, Any] | None = None

    async def ainvoke(self, prompt: str, config: dict[str, Any] | None = None) -> FakeMessage:
        self.prompt = prompt
        self.config = config
        return FakeMessage(
            "Your interview is Wednesday, August 26 at 11:00 AM IST, according to the email."
        )


class FakeStructuredRunnable:
    async def ainvoke(self, _: str) -> ReadCallPlan:
        return ReadCallPlan(
            calls=[
                ProposedToolCall(
                    tool_name="search_mail",
                    arguments={"query": "Recruiter interview", "limit": 5},
                    reason="Find the relevant mail.",
                )
            ]
        )


class CapturingStructuredModel:
    def __init__(self) -> None:
        self.method: str | None = None

    def with_structured_output(self, _: type, *, method: str) -> FakeStructuredRunnable:
        self.method = method
        return FakeStructuredRunnable()


class FakeRevisionRunnable:
    def __init__(self, owner: CapturingRevisionModel) -> None:
        self.owner = owner

    async def ainvoke(
        self,
        prompt: str,
        config: dict[str, Any] | None = None,
    ) -> PlanningProposal:
        self.owner.prompt = prompt
        self.owner.config = config
        return PlanningProposal(
            actions=[
                PlanAction(
                    id="model-action",
                    description="Create exactly two tasks",
                    server_name="tasks",
                    tool_name="create_task_batch",
                    arguments={
                        "tasks": [
                            {"title": "Task one"},
                            {"title": "Task two"},
                        ]
                    },
                    reason="The feedback requires exactly two tasks.",
                    side_effecting=True,
                    status=ActionStatus.PENDING,
                )
            ]
        )


class CapturingRevisionModel:
    def __init__(self) -> None:
        self.method: str | None = None
        self.prompt = ""
        self.config: dict[str, Any] | None = None

    def with_structured_output(self, _: type, *, method: str) -> FakeRevisionRunnable:
        self.method = method
        return FakeRevisionRunnable(self)


def test_deterministic_summary_extracts_only_grounded_interview_time() -> None:
    context = mail_context("Your interview is confirmed for Wednesday, August 26 at 11:00 AM IST.")
    result = summarize_read_only(
        "When is my interview?",
        context,
        [],
    )
    assert "Wednesday, August 26 at 11:00 AM IST" in result
    assert "Interview confirmed — Backend Engineer" in result


def test_deterministic_summary_admits_when_time_is_missing() -> None:
    context = mail_context("Your interview is confirmed. We will share timing later.")
    result = summarize_read_only("When is my interview?", context, [])
    assert result == (
        "I found the interview email, but I couldn't determine the interview time "
        "from the available messages."
    )


@pytest.mark.asyncio
async def test_openai_summary_receives_full_grounded_thread_result(tmp_path) -> None:
    model = CapturingSummaryModel()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'summary.db'}",
        openai_api_key="test-key",
    )
    reasoner = OpenAIReasoner(settings, model=model)
    body = "Your interview is Wednesday, August 26 at 11:00 AM IST."
    result = await reasoner.summarize_read_only(
        "When is my interview?",
        intent(),
        mail_context(body),
        [],
    )
    assert body in model.prompt
    assert "message-grounded" in model.prompt
    assert "2026-08-25T13:35:00+05:30" in model.prompt
    assert model.config == {"run_name": "grounded_read_summary"}
    assert "11:00 AM IST" in result


@pytest.mark.asyncio
async def test_dynamic_read_call_schema_uses_function_calling(tmp_path) -> None:
    model = CapturingStructuredModel()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'selection.db'}",
        openai_api_key="test-key",
    )
    reasoner = OpenAIReasoner(settings, model=model)
    plan = await reasoner.select_read_calls(
        "Find the interview email.",
        intent(),
        [
            ToolMetadata(
                name="search_mail",
                server_name="mail",
                description="Search mail",
                risk_level=RiskLevel.SAFE_READ,
                side_effecting=False,
            )
        ],
        PreferenceSet(),
    )
    assert model.method == "function_calling"
    assert plan.calls[0].tool_name == "search_mail"


@pytest.mark.asyncio
async def test_openai_revision_receives_request_feedback_plan_and_context(tmp_path) -> None:
    model = CapturingRevisionModel()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'revision.db'}",
        openai_api_key="test-key",
    )
    reasoner = OpenAIReasoner(settings, model=model)
    previous = [
        PlanAction(
            id="previous-draft",
            description="Create an unwanted draft",
            server_name="mail",
            tool_name="create_draft",
            arguments={"recipient": "recruiter@example.test"},
            reason="Previous proposal",
            side_effecting=True,
        )
    ]
    context = mail_context("Interview is Wednesday at 11:00 AM IST.")
    feedback = "Remove the draft and create exactly two tasks."
    actions = await reasoner.revise_write_actions(
        "Prepare for my interview.",
        intent(),
        context,
        [
            ToolMetadata(
                name="create_task_batch",
                server_name="tasks",
                description="Create tasks",
                risk_level=RiskLevel.SIDE_EFFECT,
                side_effecting=True,
            )
        ],
        PreferenceSet(),
        previous,
        feedback,
    )
    assert model.method == "function_calling"
    assert model.config == {"run_name": "revise_plan"}
    assert "Prepare for my interview." in model.prompt
    assert feedback in model.prompt
    assert "previous-draft" in model.prompt
    assert "message-grounded" in model.prompt
    assert actions is not None
    assert actions[0].tool_name == "create_task_batch"
    assert len(actions[0].arguments["tasks"]) == 2
