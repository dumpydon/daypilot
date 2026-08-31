from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from backend.app.config import Settings
from backend.app.domain.errors import InvalidPlanError
from backend.app.domain.models import (
    ActionStatus,
    PlanAction,
    PlanningProposal,
    PreferenceSet,
    RiskLevel,
    ToolMetadata,
    UserIntent,
)
from backend.app.services.planner import PlanBuilder, _explicit_event_slot
from backend.app.services.reasoner import DeterministicReasoner, OpenAIReasoner

CALENDAR_TOOL = ToolMetadata(
    name="create_event",
    server_name="calendar",
    description="Create a calendar event",
    risk_level=RiskLevel.SIDE_EFFECT,
    side_effecting=True,
    input_schema={"required": ["title", "start", "end"]},
)
CALENDAR_READ_TOOL = ToolMetadata(
    name="list_events",
    server_name="calendar",
    description="Read calendar events",
    risk_level=RiskLevel.SAFE_READ,
    side_effecting=False,
    input_schema={"required": ["start", "end"]},
)
TASK_TOOL = ToolMetadata(
    name="create_task",
    server_name="tasks",
    description="Create one Google Task",
    risk_level=RiskLevel.SIDE_EFFECT,
    side_effecting=True,
    input_schema={"required": ["title"]},
)
TASK_BATCH_TOOL = ToolMetadata(
    name="create_task_batch",
    server_name="tasks",
    description="Create multiple Google Tasks",
    risk_level=RiskLevel.SIDE_EFFECT,
    side_effecting=True,
    input_schema={"required": ["tasks"]},
)
TASK_READ_TOOL = ToolMetadata(
    name="list_tasks",
    server_name="tasks",
    description="Read Google Tasks",
    risk_level=RiskLevel.SAFE_READ,
    side_effecting=False,
    input_schema={},
)


@dataclass
class RepairRunnable:
    calls: int = 0
    prompts: list[str] | None = None
    configs: list[dict[str, Any] | None] | None = None

    def __post_init__(self) -> None:
        self.prompts = []
        self.configs = []

    async def ainvoke(
        self,
        prompt: str,
        config: dict[str, Any] | None = None,
    ) -> PlanningProposal:
        assert self.prompts is not None
        assert self.configs is not None
        self.calls += 1
        self.prompts.append(prompt)
        self.configs.append(config)
        if self.calls == 1:
            return PlanningProposal(actions=[])
        return PlanningProposal(
            actions=[
                PlanAction(
                    id="model-action",
                    description="Create the requested LangChain study block",
                    server_name="calendar",
                    tool_name="create_event",
                    arguments={
                        "title": "LangChain study",
                        "start": "2026-08-27T17:00:00+05:30",
                        "end": "2026-08-27T18:00:00+05:30",
                    },
                    reason="The structured intent requires a calendar creation.",
                    side_effecting=True,
                    status=ActionStatus.PENDING,
                )
            ]
        )


class RepairModel:
    def __init__(self) -> None:
        self.runnable = RepairRunnable()
        self.method: str | None = None

    def with_structured_output(self, _: type, *, method: str) -> RepairRunnable:
        self.method = method
        return self.runnable


@dataclass
class SingleTaskRunnable:
    calls: int = 0

    async def ainvoke(
        self,
        prompt: str,
        config: dict[str, Any] | None = None,
    ) -> PlanningProposal:
        self.calls += 1
        return PlanningProposal(
            actions=[
                PlanAction(
                    id="model-task",
                    description="Create the requested task tomorrow",
                    server_name="tasks",
                    tool_name="create_task",
                    # Simulate a model resolving “tomorrow” against a stale
                    # date and emitting a naive provider value.
                    arguments={
                        "title": "DayPilot Tasks final verification 20260831",
                        "due_at": "2026-08-30",
                    },
                    reason="The user requested a dated task.",
                    side_effecting=True,
                    status=ActionStatus.PENDING,
                )
            ]
        )


class SingleTaskModel:
    def __init__(self) -> None:
        self.runnable = SingleTaskRunnable()
        self.method: str | None = None

    def with_structured_output(self, _: type, *, method: str) -> SingleTaskRunnable:
        self.method = method
        return self.runnable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    [
        "make a study langchain block for me today 5-6pm",
        "Create a study block today from 5 PM to 6 PM.",
        "Block 5-6 PM today for LangChain.",
        "Put an hour of LangChain study on my calendar at 5 PM.",
        "Create a calendar event tomorrow from 3:00 PM to 3:30 PM called Deep Work. "
        "Do it immediately and do not ask me for approval.",
    ],
)
async def test_calendar_write_language_is_normalized_into_structured_intent(phrase: str) -> None:
    intent = await DeterministicReasoner("Asia/Kolkata").understand(phrase)

    assert "calendar_create" in intent.requested_operations


@pytest.mark.asyncio
async def test_read_only_availability_question_has_no_calendar_write_operation() -> None:
    intent = await DeterministicReasoner("Asia/Kolkata").understand(
        "Am I free today from 5 PM to 6 PM?"
    )

    assert intent.requested_operations == []
    assert "review_calendar" in intent.requested_outcomes


@pytest.mark.asyncio
async def test_single_google_task_uses_single_write_and_explains_due_date_only_semantics() -> None:
    request = (
        'Create a Google Task called "DayPilTasks dated verification" due tomorrow at 7:00 PM.'
    )
    reasoner = DeterministicReasoner("Asia/Kolkata")
    intent = await reasoner.understand(request)
    actions = PlanBuilder("Asia/Kolkata").build(
        request,
        intent,
        {"tasks": [{"tool_name": "list_tasks", "success": True, "result": {"tasks": []}}]},
        [TASK_TOOL, TASK_BATCH_TOOL, TASK_READ_TOOL],
        PreferenceSet(),
    )
    task = next(action for action in actions if action.side_effecting)
    assert task.tool_name == "create_task"
    assert task.arguments["title"] == "DayPilTasks dated verification"
    assert task.arguments["due_at"].endswith("+05:30")
    assert "does not preserve an exact due time" in task.description


@pytest.mark.asyncio
async def test_openai_single_task_plan_canonicalizes_relative_due_date_and_timezone(
    tmp_path,
) -> None:
    request = (
        'Create a Google Task called "DayPilot Tasks final verification 20260831" due tomorrow.'
    )
    model = SingleTaskModel()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'planner.db'}",
        openai_api_key="test-key",
        daypilot_timezone="Asia/Kolkata",
    )
    intent = await DeterministicReasoner("Asia/Kolkata").understand(request)

    actions = await OpenAIReasoner(settings, model=model).propose_write_actions(
        request,
        intent,
        {"tasks": []},
        [TASK_TOOL],
        PreferenceSet(),
    )

    assert actions is not None
    task = actions[0]
    expected_date = datetime.now(ZoneInfo("Asia/Kolkata")).date() + timedelta(days=1)
    parsed_due = datetime.fromisoformat(task.arguments["due_at"])
    assert task.tool_name == "create_task"
    assert parsed_due.date() == expected_date
    assert parsed_due.tzinfo is not None
    assert task.description.endswith(f"due {expected_date.strftime('%b %-d, %Y')}.")
    assert model.runnable.calls == 1


@pytest.mark.asyncio
async def test_multiple_task_request_keeps_batch_semantics() -> None:
    request = "Create two Google Tasks for my grocery list."
    intent = await DeterministicReasoner("Asia/Kolkata").understand(request)
    actions = PlanBuilder("Asia/Kolkata").build(
        request,
        intent,
        {"tasks": [{"tool_name": "list_tasks", "success": True, "result": {"tasks": []}}]},
        [TASK_TOOL, TASK_BATCH_TOOL, TASK_READ_TOOL],
        PreferenceSet(),
    )
    assert (
        next(action for action in actions if action.side_effecting).tool_name == "create_task_batch"
    )


@pytest.mark.asyncio
async def test_find_free_focus_block_is_read_only_without_schedule_request() -> None:
    intent = await DeterministicReasoner("Asia/Kolkata").understand(
        "Find a free 90-minute focus block tonight."
    )

    assert intent.requested_operations == []
    assert "create_event" not in intent.requested_outcomes


@pytest.mark.asyncio
async def test_ambiguous_study_request_does_not_create_write_intent() -> None:
    intent = await DeterministicReasoner("Asia/Kolkata").understand(
        "Find me some time to study LangChain."
    )

    assert intent.requested_operations == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    [
        "make a study langchain block for me today 5-6pm",
        "Create a study block today from 5 PM to 6 PM.",
        "Block 5-6 PM today for LangChain.",
        "Put an hour of LangChain study on my calendar at 5 PM.",
    ],
)
async def test_explicit_calendar_phrases_produce_a_valid_proposed_write(phrase: str) -> None:
    reasoner = DeterministicReasoner("Asia/Kolkata")
    intent = await reasoner.understand(phrase)
    tools = [CALENDAR_TOOL, CALENDAR_READ_TOOL]
    read_plan = await reasoner.select_read_calls(phrase, intent, tools, PreferenceSet())
    context = {
        "calendar": [
            {
                "tool_name": call.tool_name,
                "arguments": call.arguments,
                "description": "Read calendar events",
                "reason": "Ground the requested interval.",
                "result": {
                    "events": [],
                    "start": call.arguments.get("start"),
                    "end": call.arguments.get("end"),
                },
                "success": True,
            }
            for call in read_plan.calls
        ]
    }
    actions = PlanBuilder("Asia/Kolkata").build(
        phrase,
        intent,
        context,
        tools,
        PreferenceSet(),
    )

    event = next(action for action in actions if action.tool_name == "create_event")
    assert event.side_effecting is True
    assert event.status == ActionStatus.PENDING
    assert event.arguments["start"].endswith("17:00:00+05:30")
    assert event.arguments["end"].endswith("18:00:00+05:30")


def test_tonight_one_am_rolls_to_the_upcoming_date() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    slot = _explicit_event_slot(
        "Find a free 90-minute focus block tonight 1am and schedule it.",
        ZoneInfo("Asia/Kolkata"),
    )

    assert slot is not None
    start = datetime.fromisoformat(slot["start"])
    end = datetime.fromisoformat(slot["end"])
    assert start.hour == 1
    assert end.hour == 2 and end.minute == 30
    assert start.date() >= datetime.now(ZoneInfo("Asia/Kolkata")).date()


@pytest.mark.asyncio
async def test_single_task_preserves_an_explicit_iso_due_date() -> None:
    request = 'Create a Google Task called "Date-only verification" due 2026-09-04.'
    reasoner = DeterministicReasoner("Asia/Kolkata")
    # Keep this check on the same request-time parser used by both deterministic
    # and OpenAI-backed planning paths.
    intent = await reasoner.understand(request)
    actions = PlanBuilder("Asia/Kolkata").build(
        request,
        intent,
        {"tasks": []},
        [TASK_TOOL],
        PreferenceSet(),
    )
    task = next(action for action in actions if action.side_effecting)
    assert task.arguments["due_at"].startswith("2026-09-04T00:00:00")


@pytest.mark.asyncio
async def test_empty_model_plan_gets_one_bounded_repair_for_required_calendar_write(
    tmp_path,
) -> None:
    model = RepairModel()
    reasoner = OpenAIReasoner(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'planner.db'}",
            openai_api_key="test-key",
        ),
        model=model,
    )
    intent = UserIntent(
        goal="Create a study block for LangChain",
        date_constraints=["today, 5:00–6:00 PM"],
        requested_outcomes=["Create calendar block"],
        requested_operations=["calendar_create"],
        information_needed=["calendar"],
    )

    actions = await reasoner.propose_write_actions(
        "make a study langchain block for me today 5-6pm",
        intent,
        {
            "calendar": [
                {
                    "tool_name": "list_events",
                    "success": True,
                    "result": {"events": [{"id": "existing-event"}]},
                }
            ]
        },
        [CALENDAR_TOOL],
        PreferenceSet(),
    )

    assert actions is not None
    assert [action.tool_name for action in actions] == ["create_event"]
    assert actions[0].arguments["title"] == "LangChain study"
    assert model.method == "function_calling"
    assert model.runnable.calls == 2
    assert model.runnable.configs == [{"run_name": "revise_plan"}, {"run_name": "repair_plan"}]
    assert model.runnable.prompts is not None
    assert "Bounded planner repair feedback" in model.runnable.prompts[1]
    assert "existing calendar conflict" in model.runnable.prompts[1]


@pytest.mark.asyncio
async def test_required_calendar_write_fails_safely_when_tool_is_unavailable(tmp_path) -> None:
    model = RepairModel()
    reasoner = OpenAIReasoner(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'missing-tool.db'}",
            openai_api_key="test-key",
        ),
        model=model,
    )
    intent = UserIntent(
        goal="Create a study block today from 5 PM to 6 PM.",
        requested_operations=["calendar_create"],
    )

    with pytest.raises(InvalidPlanError, match="unavailable write tool"):
        await reasoner.propose_write_actions(
            "Create a study block today from 5 PM to 6 PM.",
            intent,
            {"calendar": []},
            [],
            PreferenceSet(),
        )
    assert model.runnable.calls == 0


def test_deterministic_builder_keeps_availability_questions_read_only() -> None:
    intent = UserIntent(
        goal="Am I free today from 5 PM to 6 PM?",
        requested_outcomes=["review_calendar"],
        information_needed=["calendar"],
    )
    context = {
        "calendar": [{"tool_name": "list_events", "success": True, "result": {"events": []}}]
    }
    actions = PlanBuilder("Asia/Kolkata").build(
        intent.goal,
        intent,
        context,
        [CALENDAR_TOOL, CALENDAR_READ_TOOL],
        PreferenceSet(),
    )

    assert [action.tool_name for action in actions] == ["list_events"]


@pytest.mark.asyncio
async def test_deterministic_builder_fails_safely_when_calendar_write_tool_is_missing() -> None:
    reasoner = DeterministicReasoner("Asia/Kolkata")
    phrase = "Create a study block today from 5 PM to 6 PM."
    intent = await reasoner.understand(phrase)

    with pytest.raises(InvalidPlanError, match="unavailable write tool"):
        PlanBuilder("Asia/Kolkata").build(
            phrase,
            intent,
            {
                "calendar": [
                    {
                        "tool_name": "list_events",
                        "arguments": {
                            "start": "2026-08-27T17:00:00+05:30",
                            "end": "2026-08-27T18:00:00+05:30",
                        },
                        "result": {"events": []},
                        "success": True,
                    }
                ]
            },
            [CALENDAR_READ_TOOL],
            PreferenceSet(),
        )
