from __future__ import annotations

import httpx
import pytest
from langchain_core.messages import AIMessage

from backend.app.config import Settings
from backend.app.domain.models import PreferenceSet, UserIntent
from backend.app.graph.workflow import WorkflowDependencies, build_daypilot_graph
from backend.app.services.coordinator import RunCoordinator
from backend.app.services.planner import PlanBuilder
from backend.app.services.reasoner import OpenAIReasoner, create_reasoner
from backend.app.services.summarizer import summarize_read_only
from backend.app.services.web_research import WebResearchService


class _NoUnderstandingModel:
    def with_structured_output(self, *_args, **_kwargs):
        raise AssertionError("general requests should not call the understanding model")


class _AnswerModel(_NoUnderstandingModel):
    def __init__(self, content):
        self.content = content
        self.calls = []

    async def ainvoke(self, prompt, *, config):
        self.calls.append(config["run_name"])
        return AIMessage(content=self.content)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "A model-generated answer.",
        [
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "Private reasoning"}],
            },
            {"type": "text", "text": "A model-generated ", "annotations": []},
            {"type": "text", "text": "answer.", "annotations": []},
        ],
    ],
)
async def test_general_model_answer_supports_string_and_content_blocks(content) -> None:
    model = _AnswerModel(content)
    reasoner = OpenAIReasoner(Settings(_env_file=None, openai_api_key="configured"), model=model)
    intent = await reasoner.understand("What is the capital of Bolivia?")

    answer = await reasoner.answer_general(intent.goal, intent)

    assert answer == "A model-generated answer."
    assert model.calls == ["general_answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    ["", "   ", [], [{"type": "reasoning", "summary": []}]],
)
async def test_configured_openai_empty_answer_does_not_claim_missing_key(content) -> None:
    reasoner = OpenAIReasoner(
        Settings(_env_file=None, openai_api_key="configured"), model=_AnswerModel(content)
    )
    intent = UserIntent(goal="Explain gravity", request_kind="general")

    answer = await reasoner.answer_general(intent.goal, intent)

    assert "configured" in answer
    assert "Configure OPENAI_API_KEY" not in answer


@pytest.mark.asyncio
async def test_grounded_model_summary_supports_content_blocks() -> None:
    model = _AnswerModel(
        [
            {"type": "reasoning", "summary": []},
            {"type": "text", "text": "Grounded model summary."},
        ]
    )
    reasoner = OpenAIReasoner(Settings(_env_file=None, openai_api_key="configured"), model=model)
    intent = UserIntent(
        goal="Summarize my mail", request_kind="personal", information_needed=["mail"]
    )

    answer = await reasoner.summarize_read_only(intent.goal, intent, {}, [])

    assert answer == "Grounded model summary."
    assert model.calls == ["grounded_read_summary"]


def test_reasoner_and_runtime_status_share_settings_configuration() -> None:
    configured = Settings(_env_file=None, openai_api_key="configured")
    unconfigured = Settings(_env_file=None, openai_api_key=None)

    assert create_reasoner(configured).mode == configured.reasoning_mode == "openai"
    assert create_reasoner(unconfigured).mode == unconfigured.reasoning_mode == "deterministic_demo"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "What is the capital of Bolivia?",
        "Why does ice float on water?",
        "Explain the difference between a stack and a queue.",
    ],
)
async def test_openai_general_graph_returns_block_text_without_tools(harness, monkeypatch, prompt):
    async def no_tools(*_args, **_kwargs):
        raise AssertionError("A general answer must not discover or invoke MCP/Web tools")

    monkeypatch.setattr(harness.gateway, "discover", no_tools)
    monkeypatch.setattr(harness.gateway, "invoke", no_tools)
    model = _AnswerModel([{"type": "text", "text": "Direct model answer."}])
    reasoner = OpenAIReasoner(Settings(_env_file=None, openai_api_key="configured"), model=model)
    graph = build_daypilot_graph(
        WorkflowDependencies(
            harness.repository, harness.gateway, reasoner, PlanBuilder("Asia/Kolkata")
        ),
        harness.graph.checkpointer,
    )
    coordinator = RunCoordinator(graph, harness.repository, harness.gateway)
    try:
        accepted = await coordinator.start_run(prompt)
        detail = await coordinator.wait_until_settled(accepted.id)
        assert detail.status == "completed"
        assert detail.reasoning_mode == "openai"
        assert detail.intent.request_kind == "general"
        assert detail.final_summary == "Direct model answer."
        assert detail.available_tools == detail.plan == []
        assert not any(detail.context.values())
        assert await harness.repository.list_executions(accepted.id) == []
        assert [event.event_type for event in detail.events] == [
            "request_received", "request_understood", "general_answer_generated", "run_completed"
        ]
        assert model.calls == ["general_answer"]
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("What is the capital of Lithuania?", "Vilnius"),
        ("Give me the LeetCode House Robber solution in Python.", "def rob"),
    ],
)
async def test_general_answers_skip_mcp_and_planning(harness, prompt: str, expected: str) -> None:
    accepted = await harness.coordinator.start_run(prompt)
    detail = await harness.coordinator.wait_until_settled(accepted.id)

    assert detail.status == "completed"
    assert detail.intent and detail.intent.request_kind == "general"
    assert expected in (detail.final_summary or "")
    assert detail.plan == []
    assert detail.available_tools == []
    assert not any(detail.context.values())
    assert not any(event.event_type == "tools_discovered" for event in detail.events)
    assert await harness.repository.list_executions(accepted.id) == []


@pytest.mark.asyncio
async def test_openai_reasoner_skips_understanding_round_trip_for_clear_general_request() -> None:
    reasoner = OpenAIReasoner(
        Settings(_env_file=None, openai_api_key="configured"),
        model=_NoUnderstandingModel(),
    )

    intent = await reasoner.understand("What is the capital of Romania?")

    assert intent.request_kind == "general"
    assert intent.information_needed == []


def test_tavily_search_normalizes_sources_without_extra_dependency() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.tavily.com/search"
        assert request.headers["Authorization"] == "Bearer tvly-test"
        return httpx.Response(
            200,
            json={
                "answer": "A current public answer.",
                "results": [
                    {
                        "title": "Primary source",
                        "url": "https://example.com/news",
                        "content": "Current public details.",
                        "score": 0.9,
                    }
                ],
            },
        )

    service = WebResearchService(
        Settings(_env_file=None, tavily_api_key="tvly-test"),
        transport=httpx.MockTransport(handler),
    )
    result = service.search_web("What happened today?", 3)

    assert result["answer"] == "A current public answer."
    assert result["count"] == 1
    assert result["sources"] == [
        {
            "title": "Primary source",
            "url": "https://example.com/news",
            "snippet": "Current public details.",
            "score": 0.9,
        }
    ]


@pytest.mark.asyncio
async def test_read_only_research_does_not_invoke_write_planning_model() -> None:
    reasoner = OpenAIReasoner(Settings(_env_file=None), model=object())
    intent = UserIntent(
        goal="Answer a fresh public question",
        request_kind="research",
        information_needed=["web"],
        requested_outcomes=["fresh public answer"],
    )

    actions = await reasoner.propose_write_actions(
        "What happened today?",
        intent,
        {},
        [],
        PreferenceSet(),
    )

    assert actions == []


@pytest.mark.asyncio
async def test_configured_openai_failure_does_not_claim_the_key_is_missing() -> None:
    reasoner = OpenAIReasoner(Settings(_env_file=None, openai_api_key="configured"), model=object())
    intent = UserIntent(goal="Answer an uncommon general question", request_kind="general")

    answer = await reasoner.answer_general("Explain an uncommon topic", intent)

    assert "configured but unavailable" in answer
    assert "Configure OPENAI_API_KEY" not in answer


def test_unavailable_web_research_is_reported_without_unrelated_service_claims() -> None:
    error = "Fresh web research is unavailable because TAVILY_API_KEY is not configured."
    context = {
        "mail": [],
        "calendar": [],
        "tasks": [],
        "files": [],
        "x": [],
        "web": [{"tool_name": "search_web", "success": False, "error": error}],
    }

    summary = summarize_read_only("What happened today?", context, [error])

    assert error in summary
    assert "X information" not in summary


@pytest.mark.asyncio
async def test_fresh_research_uses_web_only_and_retains_sources(harness, monkeypatch) -> None:
    original_invoke = harness.gateway.invoke

    async def invoke(tool_name, arguments, *, authorization=None):
        if tool_name == "search_web":
            return {
                "query": arguments["query"],
                "answer": "The public update was confirmed by the cited source.",
                "sources": [
                    {
                        "title": "Public update",
                        "url": "https://example.com/update",
                        "snippet": "Confirmed public update.",
                    }
                ],
                "count": 1,
                "provider": "Tavily",
            }
        return await original_invoke(tool_name, arguments, authorization=authorization)

    monkeypatch.setattr(harness.gateway, "invoke", invoke)
    accepted = await harness.coordinator.start_run("What happened with OpenAI today?")
    detail = await harness.coordinator.wait_until_settled(accepted.id)

    assert detail.status == "completed"
    assert detail.intent and detail.intent.request_kind == "research"
    assert [action.tool_name for action in detail.plan] == ["search_web"]
    assert detail.context["web"][0]["result"]["sources"][0]["url"] == ("https://example.com/update")
    assert not any(
        detail.context[service] for service in ("mail", "calendar", "tasks", "files", "x")
    )
    assert "confirmed by the cited source" in (detail.final_summary or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("approval_bypass", [False, True])
async def test_hybrid_research_preserves_grounding_dependencies_and_hitl(
    harness,
    monkeypatch,
    approval_bypass: bool,
) -> None:
    original_invoke = harness.gateway.invoke

    async def invoke(tool_name, arguments, *, authorization=None):
        if tool_name == "search_web":
            assert "Northstar Labs" in arguments["query"]
            return {
                "query": arguments["query"],
                "answer": "Northstar Labs builds reliability-focused platform systems.",
                "sources": [
                    {
                        "title": "Northstar overview",
                        "url": "https://example.com/northstar",
                        "snippet": "Reliability-focused platform systems.",
                    }
                ],
                "count": 1,
                "provider": "Tavily",
            }
        return await original_invoke(tool_name, arguments, authorization=authorization)

    monkeypatch.setattr(harness.gateway, "invoke", invoke)
    request = (
        "Find my interview email, research the company, then create a 90-minute preparation "
        "block before the interview."
    )
    if approval_bypass:
        request += " Do it without asking for approval."
    accepted = await harness.coordinator.start_run(request)
    detail = await harness.coordinator.wait_until_settled(accepted.id, max_wait_seconds=45)

    assert detail.status == "waiting_approval"
    assert detail.intent and detail.intent.request_kind == "hybrid"
    assert await harness.repository.list_executions(accepted.id) == []
    by_tool = {action.tool_name: action for action in detail.plan}
    assert set(by_tool) == {
        "search_mail",
        "get_thread",
        "search_web",
        "list_events",
        "find_free_slots",
        "create_event",
    }
    assert by_tool["search_web"].depends_on == [by_tool["get_thread"].id]
    assert by_tool["find_free_slots"].depends_on == [by_tool["get_thread"].id]
    assert by_tool["create_event"].depends_on == [by_tool["find_free_slots"].id]
    assert by_tool["find_free_slots"].arguments["duration_minutes"] == 90
