from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
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
from backend.app.graph.workflow import (
    _blocked_read,
    _contains_unresolved_reference,
    _read_arguments_unavailable,
)
from backend.app.mcp.policy import plan_hash
from backend.app.services.planner import PlanBuilder, _validate_dependencies
from backend.app.services.reasoner import OpenAIReasoner


def tool(name: str, server: str, *, write: bool = False) -> ToolMetadata:
    return ToolMetadata(
        name=name,
        server_name=server,
        description=name,
        risk_level=RiskLevel.SIDE_EFFECT if write else RiskLevel.SAFE_READ,
        side_effecting=write,
        input_schema={},
    )


TOOLS = [
    tool("search_mail", "mail"),
    tool("get_thread", "mail"),
    tool("list_events", "calendar"),
    tool("find_free_slots", "calendar"),
    tool("list_tasks", "tasks"),
    tool("create_event", "calendar", write=True),
    tool("create_task", "tasks", write=True),
]


def read_record(tool_name: str, result: dict, *, success: bool = True) -> dict:
    return {
        "tool_name": tool_name,
        "arguments": {},
        "description": tool_name,
        "reason": "Ground the request.",
        "result": result,
        "success": success,
        "error": None if success else "read failed",
    }


def write_action(action_id: str, tool_name: str, server: str) -> PlanAction:
    return PlanAction(
        id=action_id,
        description=tool_name,
        server_name=server,
        tool_name=tool_name,
        arguments={"title": "Interview preparation"},
        reason="Grounded plan",
        side_effecting=True,
        status=ActionStatus.PENDING,
    )


def temporal_intent(goal: str) -> UserIntent:
    return UserIntent(
        goal=goal,
        requested_outcomes=["find_mail", "review_calendar", "create_event", "create_checklist"],
        requested_operations=["calendar_create", "tasks_create"],
        information_needed=["mail", "calendar", "tasks"],
    )


def grounded_context() -> dict[str, list[dict]]:
    return {
        "mail": [
            read_record("search_mail", {"threads": [{"thread_id": "thread-1"}]}),
            read_record(
                "get_thread",
                {
                    "messages": [
                        {
                            "subject": "Meeting confirmed",
                            "body": "The meeting is Sunday, August 31 at 11:00 AM IST.",
                        }
                    ]
                },
            ),
        ],
        "calendar": [
            read_record("list_events", {"events": []}),
            read_record(
                "find_free_slots",
                {
                    "slots": [
                        {
                            "start": "2026-08-31T09:00:00+05:30",
                            "end": "2026-08-31T10:00:00+05:30",
                        }
                    ]
                },
            ),
        ],
        "tasks": [read_record("list_tasks", {"tasks": []})],
    }


def test_plan_action_dependency_schema_is_backward_compatible() -> None:
    action = write_action("write-1", "create_task", "tasks")
    assert action.depends_on == []
    assert PlanAction.model_validate(action.model_dump()).depends_on == []


@pytest.mark.parametrize(
    ("actions", "message"),
    [
        (
            [
                write_action("a", "create_task", "tasks").model_copy(
                    update={"depends_on": ["missing"]}
                )
            ],
            "missing dependencies",
        ),
        (
            [write_action("a", "create_task", "tasks").model_copy(update={"depends_on": ["a"]})],
            "depend on itself",
        ),
        (
            [
                write_action("a", "create_task", "tasks"),
                write_action("b", "create_event", "calendar").model_copy(
                    update={"depends_on": ["a", "a"]}
                ),
            ],
            "Duplicate dependencies",
        ),
        (
            [
                write_action("a", "create_task", "tasks").model_copy(update={"depends_on": ["b"]}),
                write_action("b", "create_event", "calendar").model_copy(
                    update={"depends_on": ["a"]}
                ),
            ],
            "contains a cycle",
        ),
    ],
)
def test_dependency_validator_rejects_invalid_graphs(
    actions: list[PlanAction],
    message: str,
) -> None:
    with pytest.raises(InvalidPlanError, match=message):
        _validate_dependencies(actions)


def test_independent_reads_remain_parallel() -> None:
    context = {
        "mail": [
            read_record("search_mail", {"threads": [{"thread_id": "thread-1"}]}),
            read_record("get_thread", {"messages": []}),
        ],
        "tasks": [read_record("list_tasks", {"tasks": []})],
    }
    intent = UserIntent(
        goal="Show my latest emails and current tasks.",
        requested_outcomes=["find_mail", "review_tasks"],
        information_needed=["mail", "tasks"],
    )
    planner = PlanBuilder("Asia/Kolkata")
    actions = planner.finalize(
        planner.read_actions(context),
        intent.goal,
        intent,
        context,
        TOOLS,
    )
    by_tool = {action.tool_name: action for action in actions}
    assert by_tool["search_mail"].depends_on == []
    assert by_tool["list_tasks"].depends_on == []
    assert by_tool["get_thread"].depends_on == [by_tool["search_mail"].id]


def test_grounded_mail_result_binds_thread_and_calendar_read_arguments() -> None:
    request = (
        "Find my latest email about the DayPilot interview test, determine when the interview "
        "is scheduled, check my calendar, find a free 60-minute preparation slot before the "
        "interview, create a preparation block called 'DayPilot Interview Prep', and create "
        "one Google Task called 'Prepare for DayPilot interview'."
    )
    intent = temporal_intent(request)
    planner = PlanBuilder("Asia/Kolkata")
    context = {
        "mail": [
            read_record(
                "search_mail",
                {
                    "threads": [
                        {"thread_id": "thread-daypilot-test", "subject": "DayPilot interview test"}
                    ]
                },
            )
        ]
    }

    thread_arguments = planner.ground_read_arguments(request, intent, "get_thread", {}, context)
    assert thread_arguments == {"thread_id": "thread-daypilot-test"}

    context["mail"].append(
        read_record(
            "get_thread",
            {
                "subject": "DayPilot interview test",
                "messages": [
                    {
                        "subject": "DayPilot interview test",
                        "body": "Your interview is scheduled for September 2, 2026 at 4:00 PM IST.",
                    }
                ],
            },
        )
    )
    list_arguments = planner.ground_read_arguments(request, intent, "list_events", {}, context)
    slot_arguments = planner.ground_read_arguments(
        request,
        intent,
        "find_free_slots",
        {},
        context,
        PreferenceSet(preferred_focus_block_minutes=90),
    )

    assert list_arguments == {
        "start": "2026-09-02T13:00:00+05:30",
        "end": "2026-09-02T18:00:00+05:30",
    }
    assert slot_arguments == {
        "start": "2026-09-02T12:00:00+05:30",
        "end": "2026-09-02T16:00:00+05:30",
        "duration_minutes": 60,
    }


def test_missing_grounded_thread_id_blocks_instead_of_using_model_id() -> None:
    request = "Read the latest interview thread and find a slot before it."
    intent = temporal_intent(request)
    planner = PlanBuilder("Asia/Kolkata")
    arguments = planner.ground_read_arguments(
        request,
        intent,
        "get_thread",
        {"thread_id": "model-invented-id"},
        {"mail": [read_record("search_mail", {"threads": []})]},
    )

    assert arguments == {}


def test_calendar_reads_keep_failed_mail_grounding_as_a_true_dependency() -> None:
    request = "Find the interview email, check when it is, and find a free slot before it."
    context = {
        "mail": [
            read_record("search_mail", {"threads": [{"thread_id": "thread-1"}]}),
            read_record("get_thread", {}, success=False),
        ],
        "calendar": [
            read_record("list_events", {}, success=False),
            read_record("find_free_slots", {}, success=False),
        ],
    }
    planner = PlanBuilder("Asia/Kolkata")

    actions = planner.finalize(
        planner.read_actions(context),
        request,
        temporal_intent(request),
        context,
        TOOLS,
    )
    by_tool = {action.tool_name: action for action in actions}

    assert by_tool["get_thread"].depends_on == [by_tool["search_mail"].id]
    assert by_tool["list_events"].depends_on == [by_tool["get_thread"].id]
    assert by_tool["find_free_slots"].depends_on == [by_tool["get_thread"].id]


def test_exact_daypilot_request_reconstructs_both_grounded_writes() -> None:
    request = (
        "Find my latest email about the DayPilot interview test, determine when the interview "
        "is scheduled, check my calendar, find a free 60-minute preparation slot before the "
        "interview, create a preparation block called 'DayPilot Interview Prep', and create "
        "one Google Task called 'Prepare for DayPilot interview'."
    )
    intent = temporal_intent(request)
    context = {
        **grounded_context(),
        "mail": [
            read_record(
                "search_mail",
                {"threads": [{"thread_id": "thread-daypilot-test"}]},
            ),
            read_record(
                "get_thread",
                {
                    "subject": "DayPilot interview test",
                    "messages": [
                        {
                            "subject": "DayPilot interview test",
                            "body": (
                                "Your interview is scheduled for September 2, 2026 at 4:00 PM IST."
                            ),
                        }
                    ],
                },
            ),
        ],
        "calendar": [
            read_record("list_events", {"events": []}),
            read_record(
                "find_free_slots",
                {
                    "slots": [
                        {
                            "start": "2026-09-02T12:00:00+05:30",
                            "end": "2026-09-02T13:00:00+05:30",
                        }
                    ]
                },
            ),
        ],
    }
    planner = PlanBuilder("Asia/Kolkata")
    actions = planner.build(request, intent, context, TOOLS, PreferenceSet())

    event = next(action for action in actions if action.tool_name == "create_event")
    task = next(action for action in actions if action.tool_name == "create_task")
    assert event.arguments["title"] == "DayPilot Interview Prep"
    assert event.arguments["start"] == "2026-09-02T12:00:00+05:30"
    assert event.arguments["end"] == "2026-09-02T13:00:00+05:30"
    assert task.arguments["title"] == "Prepare for DayPilot interview"
    assert event.depends_on == [next(a.id for a in actions if a.tool_name == "find_free_slots")]
    assert task.depends_on == event.depends_on


@pytest.mark.asyncio
async def test_exact_daypilot_request_reaches_approval_after_runtime_grounding(harness) -> None:
    timezone = ZoneInfo("Asia/Kolkata")
    interview_at = datetime.now(timezone).replace(second=0, microsecond=0) + timedelta(days=2)
    with sqlite3.connect(harness.database_path) as connection:
        connection.execute(
            "INSERT INTO mail_threads(id, subject, participants, updated_at) VALUES (?, ?, ?, ?)",
            (
                "thread-daypilot-test",
                "DayPilot interview test",
                "recruiter@daypilot.example,alex.morgan@example.com",
                datetime.now(timezone).isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO mail_messages(id, thread_id, sender, recipients, subject, body, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "msg-daypilot-test",
                "thread-daypilot-test",
                "recruiter@daypilot.example",
                "alex.morgan@example.com",
                "DayPilot interview test",
                (
                    "Your interview is scheduled for "
                    f"{interview_at.strftime('%B %-d, %Y')} at 4:00 PM IST."
                ),
                datetime.now(timezone).isoformat(),
            ),
        )
        connection.commit()

    request = (
        "Find my latest email about the DayPilot interview test, determine when the interview "
        "is scheduled, check my calendar, find a free 60-minute preparation slot before the "
        "interview, create a preparation block called 'DayPilot Interview Prep', and create "
        "one Google Task called 'Prepare for DayPilot interview'. Do not draft or send any "
        "email. Show me the proposed plan and dependency graph before making any external "
        "changes."
    )
    accepted = await harness.coordinator.start_run(request)
    detail = await harness.coordinator.wait_until_settled(accepted.id, max_wait_seconds=45)

    assert detail.status == "waiting_approval"
    assert await harness.repository.list_executions(accepted.id) == []
    by_tool = {action.tool_name: action for action in detail.plan}
    assert by_tool["get_thread"].arguments["thread_id"] == "thread-daypilot-test"
    assert by_tool["list_events"].arguments["start"].endswith("+05:30")
    assert by_tool["find_free_slots"].arguments["duration_minutes"] == 60
    assert by_tool["create_event"].arguments["title"] == "DayPilot Interview Prep"
    slot_result = next(
        record["result"]
        for record in detail.context["calendar"]
        if record["tool_name"] == "find_free_slots" and record["success"]
    )
    selected_slot = slot_result["slots"][0]
    assert by_tool["create_event"].arguments["start"] == selected_slot["start"]
    assert by_tool["create_event"].arguments["end"] == selected_slot["end"]
    assert by_tool["create_task"].arguments["title"] == "Prepare for DayPilot interview"
    assert by_tool["create_event"].depends_on == [by_tool["find_free_slots"].id]
    assert by_tool["create_task"].depends_on == [by_tool["find_free_slots"].id]
    assert not any(action.tool_name == "create_draft" for action in detail.plan)


def test_grounded_cross_service_plan_forms_a_branching_dag() -> None:
    request = (
        "Find the meeting email, check when it is, find a free preparation slot before it, "
        "then create a preparation event and task."
    )
    context = grounded_context()
    intent = temporal_intent(request)
    planner = PlanBuilder("Asia/Kolkata")
    actions = [
        *planner.read_actions(context),
        write_action("write-calendar", "create_event", "calendar"),
        write_action("write-task", "create_task", "tasks"),
    ]
    finalized = planner.finalize(actions, request, intent, context, TOOLS)
    by_tool = {action.tool_name: action for action in finalized}

    assert by_tool["get_thread"].depends_on == [by_tool["search_mail"].id]
    assert by_tool["list_events"].depends_on == [by_tool["get_thread"].id]
    assert by_tool["find_free_slots"].depends_on == [by_tool["get_thread"].id]
    assert by_tool["create_event"].depends_on == [by_tool["find_free_slots"].id]
    assert by_tool["create_task"].depends_on == [by_tool["find_free_slots"].id]
    assert by_tool["list_tasks"].depends_on == []


def test_missing_temporal_evidence_omits_dependent_writes() -> None:
    request = "Find the meeting email, find a free slot before it, and create an event and task."
    context = grounded_context()
    context["mail"][1] = read_record("get_thread", {"messages": []})
    intent = temporal_intent(request)
    planner = PlanBuilder("Asia/Kolkata")
    actions = [
        *planner.read_actions(context),
        write_action("write-calendar", "create_event", "calendar"),
        write_action("write-task", "create_task", "tasks"),
    ]
    finalized = planner.finalize(actions, request, intent, context, TOOLS)
    assert not any(action.side_effecting for action in finalized)


def test_failed_free_slot_read_removes_event_write_without_fabricating_times() -> None:
    request = "Find the meeting email, find a free slot before it, and create an event."
    context = grounded_context()
    context["calendar"][1] = read_record("find_free_slots", {}, success=False)
    intent = temporal_intent(request)
    planner = PlanBuilder("Asia/Kolkata")
    actions = [
        *planner.read_actions(context),
        PlanAction(
            id="model-event",
            description="Create preparation event",
            server_name="calendar",
            tool_name="create_event",
            arguments={"title": "Interview preparation"},
            reason="The model proposed the requested event.",
            side_effecting=True,
        ),
    ]

    finalized = planner.finalize(actions, request, intent, context, TOOLS)

    assert not any(action.tool_name == "create_event" for action in finalized)
    assert not any(
        action.tool_name == "create_event" and action.arguments.get("start") for action in finalized
    )


def test_failed_free_slot_read_blocks_a_task_that_declares_the_dependency() -> None:
    request = "Find the meeting email, find a free slot before it, and create a task."
    context = grounded_context()
    context["calendar"][1] = read_record("find_free_slots", {}, success=False)
    intent = temporal_intent(request)
    planner = PlanBuilder("Asia/Kolkata")
    actions = [
        *planner.read_actions(context),
        PlanAction(
            id="model-task",
            description="Create preparation task",
            server_name="tasks",
            tool_name="create_task",
            arguments={"title": "Interview preparation"},
            reason="The task is tied to the preparation slot.",
            side_effecting=True,
        ),
    ]

    finalized = planner.finalize(actions, request, intent, context, TOOLS)

    assert not any(action.tool_name == "create_task" for action in finalized)


def test_successful_free_slot_read_produces_complete_event_inputs() -> None:
    request = "Find the meeting email, find a free slot before it, and create an event."
    context = grounded_context()
    intent = temporal_intent(request)
    planner = PlanBuilder("Asia/Kolkata")
    actions = planner.build(
        request,
        intent,
        context,
        TOOLS,
        PreferenceSet(),
    )

    event = next(action for action in actions if action.tool_name == "create_event")
    assert event.arguments["title"] == "Focus block"
    assert event.arguments["start"].endswith("09:00:00+05:30")
    assert event.arguments["end"].endswith("10:00:00+05:30")


@pytest.mark.asyncio
async def test_unresolved_read_reference_is_detected_before_provider_invocation() -> None:
    assert _contains_unresolved_reference(
        {"start": "2026-08-31T09:00:00+05:30", "end": "{{latest_interview_time}}"}
    )
    assert _contains_unresolved_reference(
        {"end": "<timezone-aware interview start resolved from the latest mail thread>"}
    )
    assert not _contains_unresolved_reference(
        {"start": "2026-08-31T09:00:00+05:30", "end": "2026-08-31T11:00:00+05:30"}
    )

    class FakeRepository:
        def __init__(self) -> None:
            self.events: list[tuple] = []

        async def append_event(self, *args, **kwargs) -> None:
            self.events.append((args, kwargs))

    class FakeGateway:
        def metadata(self, _tool_name: str) -> ToolMetadata:
            return ToolMetadata(
                name="find_free_slots",
                server_name="calendar",
                description="Find a free slot",
                risk_level=RiskLevel.SAFE_READ,
                side_effecting=False,
                input_schema={"required": ["start", "end", "duration_minutes"]},
            )

        async def invoke(self, *_args, **_kwargs):
            raise AssertionError("an unresolved read must not reach the provider")

    repository = FakeRepository()
    assert _read_arguments_unavailable(FakeGateway(), "find_free_slots", {"duration_minutes": 90})
    assert _read_arguments_unavailable(
        FakeGateway(),
        "find_free_slots",
        {
            "start": "2026-08-31T09:00:00+05:30",
            "end": "{{latest_interview_time}}",
            "duration_minutes": 90,
        },
    )
    record = await _blocked_read(
        "run-1",
        repository,
        FakeGateway(),
        "find_free_slots",
        {"end": "{{latest_interview_time}}"},
        "Needs the interview time first.",
    )
    assert record["success"] is False
    assert "was not called" in record["error"]
    assert repository.events[0][0][1] == "tool_blocked"


def test_independent_task_can_survive_without_calendar_slot_evidence() -> None:
    request = 'Find the meeting email and create one Google Task called "Interview prep".'
    intent = UserIntent(
        goal=request,
        requested_outcomes=["find_mail", "create_checklist"],
        requested_operations=["tasks_create"],
        information_needed=["mail", "tasks"],
    )
    context = {
        "mail": [read_record("search_mail", {"threads": []})],
        "tasks": [read_record("list_tasks", {"tasks": []})],
    }
    planner = PlanBuilder("Asia/Kolkata")
    actions = [
        *planner.read_actions(context),
        PlanAction(
            id="model-task",
            description="Create Interview prep task",
            server_name="tasks",
            tool_name="create_task",
            arguments={"title": "Interview prep"},
            reason="The user requested one task.",
            side_effecting=True,
        ),
    ]

    finalized = planner.finalize(actions, request, intent, context, TOOLS)

    task = next(action for action in finalized if action.tool_name == "create_task")
    assert task.arguments == {"title": "Interview prep"}


class _MalformedEventRunnable:
    def __init__(self, repaired: bool = False) -> None:
        self.calls = 0
        self.repaired = repaired

    async def ainvoke(self, _prompt: str, config: dict | None = None) -> PlanningProposal:
        self.calls += 1
        if self.repaired and self.calls > 1:
            arguments = {
                "title": "Interview preparation",
                "start": "2026-08-31T09:00:00+05:30",
                "end": "2026-08-31T10:30:00+05:30",
            }
        else:
            arguments = {
                "title": "",
                "start": "{{find_free_slots.start}}",
                "end": "{{find_free_slots.end}}",
            }
        return PlanningProposal(
            actions=[
                PlanAction(
                    id="model-event",
                    description="Create interview preparation event",
                    server_name="calendar",
                    tool_name="create_event",
                    arguments=arguments,
                    reason="The user requested a calendar block.",
                    side_effecting=True,
                )
            ]
        )


class _MalformedEventModel:
    def __init__(self, repaired: bool = False) -> None:
        self.runnable = _MalformedEventRunnable(repaired)

    def with_structured_output(self, _schema: type, *, method: str) -> _MalformedEventRunnable:
        assert method == "function_calling"
        return self.runnable


class _MalformedTaskModel:
    def __init__(self) -> None:
        self.runnable = _MalformedTaskRunnable()

    def with_structured_output(self, _schema: type, *, method: str) -> _MalformedTaskRunnable:
        assert method == "function_calling"
        return self.runnable


class _MalformedTaskRunnable:
    async def ainvoke(self, _prompt: str, config: dict | None = None) -> PlanningProposal:
        return PlanningProposal(
            actions=[
                PlanAction(
                    id="model-task",
                    description="Create the preparation task",
                    server_name="tasks",
                    tool_name="create_task",
                    arguments={
                        "title": "Interview prep",
                        "due_at": "{{find_free_slots.start}}",
                    },
                    reason="The task is tied to the unavailable slot.",
                    side_effecting=True,
                )
            ]
        )


def _calendar_create_intent(request: str) -> UserIntent:
    return UserIntent(
        goal=request,
        requested_outcomes=["create_event"],
        requested_operations=["calendar_create"],
        information_needed=["mail", "calendar"],
    )


def _calendar_write_tool() -> ToolMetadata:
    return ToolMetadata(
        name="create_event",
        server_name="calendar",
        description="Create an event",
        risk_level=RiskLevel.SIDE_EFFECT,
        side_effecting=True,
        input_schema={"required": ["title", "start", "end"]},
    )


def _task_write_tool() -> ToolMetadata:
    return ToolMetadata(
        name="create_task",
        server_name="tasks",
        description="Create a task",
        risk_level=RiskLevel.SIDE_EFFECT,
        side_effecting=True,
        input_schema={"required": ["title"]},
    )


@pytest.mark.asyncio
async def test_model_event_is_blocked_when_free_slot_evidence_is_unavailable(tmp_path) -> None:
    request = "Find the meeting email, find a free slot before it, and create an event."
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'reasoner.db'}",
        openai_api_key="test-key",
        daypilot_timezone="Asia/Kolkata",
    )
    model = _MalformedEventModel()
    reasoner = OpenAIReasoner(settings, model=model)

    actions = await reasoner.propose_write_actions(
        request,
        _calendar_create_intent(request),
        {
            "mail": [],
            "calendar": [read_record("find_free_slots", {}, success=False)],
        },
        [_calendar_write_tool()],
        PreferenceSet(),
    )

    assert actions == []
    assert model.runnable.calls == 1


@pytest.mark.asyncio
async def test_model_task_with_missing_slot_dependency_is_blocked_without_repair_loop(
    tmp_path,
) -> None:
    request = "Find the meeting email, find a free slot before it, and create one task."
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'task.db'}",
        openai_api_key="test-key",
        daypilot_timezone="Asia/Kolkata",
    )
    model = _MalformedTaskModel()
    reasoner = OpenAIReasoner(settings, model=model)
    intent = UserIntent(
        goal=request,
        requested_outcomes=["create_checklist"],
        requested_operations=["tasks_create"],
        information_needed=["mail", "calendar", "tasks"],
    )

    actions = await reasoner.propose_write_actions(
        request,
        intent,
        {"mail": [], "calendar": [read_record("find_free_slots", {}, success=False)]},
        [_task_write_tool()],
        PreferenceSet(),
    )

    assert actions == []


@pytest.mark.asyncio
async def test_required_write_omission_still_repairs_when_slot_evidence_exists(tmp_path) -> None:
    request = "Find the meeting email, find a free slot before it, and create an event."
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'repair.db'}",
        openai_api_key="test-key",
        daypilot_timezone="Asia/Kolkata",
    )
    model = _MalformedEventModel(repaired=True)
    reasoner = OpenAIReasoner(settings, model=model)
    context = grounded_context()

    actions = await reasoner.propose_write_actions(
        request,
        _calendar_create_intent(request),
        context,
        [_calendar_write_tool()],
        PreferenceSet(),
    )

    assert actions is not None
    assert actions[0].arguments["start"].endswith("09:00:00+05:30")
    assert model.runnable.calls == 2


def test_revision_regenerates_dependencies_and_hash_without_stale_references() -> None:
    request = "Find the meeting email, find a free slot before it, and create a task."
    context = grounded_context()
    intent = temporal_intent(request)
    planner = PlanBuilder("Asia/Kolkata")
    original = planner.finalize(
        [
            *planner.read_actions(context),
            write_action("write-task", "create_task", "tasks"),
        ],
        request,
        intent,
        context,
        TOOLS,
    )
    original_write = next(action for action in original if action.side_effecting)
    assert original_write.depends_on

    revised_reads = [
        action for action in planner.read_actions(context) if action.tool_name != "find_free_slots"
    ]
    revised = planner.finalize(
        [*revised_reads, write_action("write-task", "create_task", "tasks")],
        request,
        intent,
        context,
        TOOLS,
    )
    revised_write = next(action for action in revised if action.side_effecting)
    revised_ids = {action.id for action in revised}
    assert set(revised_write.depends_on) <= revised_ids
    assert all(
        dependency
        != next(action.id for action in original if action.tool_name == "find_free_slots")
        for dependency in revised_write.depends_on
    )
    assert plan_hash([original_write]) != plan_hash([revised_write])


@pytest.mark.asyncio
async def test_cross_service_goal_grounds_reads_and_blocks_both_writes(harness) -> None:
    request = (
        "Find my latest interview email, check when it is, find a free preparation slot before "
        'it, create a preparation block, and create one Google Task called "Interview prep". '
        "Do not draft an email and do not ask me for approval."
    )
    accepted = await harness.coordinator.start_run(request)
    detail = await harness.coordinator.wait_until_settled(accepted.id, max_wait_seconds=30)

    assert detail.status == "waiting_approval"
    assert await harness.repository.list_executions(accepted.id) == []
    by_tool = {action.tool_name: action for action in detail.plan}
    assert {action.tool_name for action in detail.plan if action.side_effecting} == {
        "create_event",
        "create_task",
    }
    assert by_tool["get_thread"].depends_on == [by_tool["search_mail"].id]
    assert by_tool["find_free_slots"].depends_on == [by_tool["get_thread"].id]
    assert by_tool["create_event"].depends_on == [by_tool["find_free_slots"].id]
    assert by_tool["create_task"].depends_on == [by_tool["find_free_slots"].id]
    assert by_tool["find_free_slots"].arguments["end"].endswith("11:00:00+05:30")
