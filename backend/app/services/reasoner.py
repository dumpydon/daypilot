from __future__ import annotations

import json
import logging
import re
from datetime import datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from langchain_openai import ChatOpenAI

from backend.app.config import Settings
from backend.app.domain.errors import InvalidPlanError
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
from backend.app.mcp.policy import get_policy
from backend.app.services.planner import _explicit_event_slot
from backend.app.services.summarizer import summarize_read_only

logger = logging.getLogger(__name__)


class Reasoner(Protocol):
    mode: str

    async def understand(self, request: str) -> UserIntent: ...

    async def select_read_calls(
        self,
        request: str,
        intent: UserIntent,
        tools: list[ToolMetadata],
        preferences: PreferenceSet,
    ) -> ReadCallPlan: ...

    async def summarize_read_only(
        self,
        request: str,
        intent: UserIntent,
        context: dict[str, list[dict[str, Any]]],
        errors: list[str],
    ) -> str: ...

    async def revise_write_actions(
        self,
        request: str,
        intent: UserIntent,
        context: dict[str, list[dict[str, Any]]],
        tools: list[ToolMetadata],
        preferences: PreferenceSet,
        previous_plan: list[PlanAction],
        feedback: str,
    ) -> list[PlanAction] | None: ...

    async def propose_write_actions(
        self,
        request: str,
        intent: UserIntent,
        context: dict[str, list[dict[str, Any]]],
        tools: list[ToolMetadata],
        preferences: PreferenceSet,
    ) -> list[PlanAction] | None: ...


class DeterministicReasoner:
    """Credential-free, reproducible reasoning for the local demo and evals."""

    mode = "deterministic_demo"

    def __init__(self, timezone_name: str) -> None:
        self.timezone = ZoneInfo(timezone_name)

    async def understand(self, request: str) -> UserIntent:
        lowered = request.lower()
        people = _extract_people(request)
        constraints = [
            word for word in ("today", "tonight", "tomorrow", "this week") if word in lowered
        ]
        outcomes: list[str] = []
        information: list[str] = []
        interview_prep = "interview" in lowered and any(
            word in lowered for word in ("prepare", "prep", "ready")
        )

        mail_requested = any(
            word in lowered for word in ("mail", "email", "conversation", "thread", "interview")
        )
        if mail_requested:
            outcomes.append("find_mail")
            information.append("mail")
        if interview_prep or any(
            word in lowered for word in ("calendar", "schedule", "block", "free", "availability")
        ):
            outcomes.append("review_calendar")
            information.append("calendar")
        if any(word in lowered for word in ("task", "checklist", "to-do")) or interview_prep:
            outcomes.append("review_tasks")
            information.append("tasks")

        explicit_schedule = any(
            word in lowered for word in ("schedule", "reserve", "book")
        ) or lowered.strip().startswith("block ")
        if interview_prep or explicit_schedule:
            outcomes.append("schedule_focus_block")
            if "calendar" not in information:
                information.append("calendar")
        if "create" in lowered and "event" in lowered:
            outcomes.append("create_event")
            if "calendar" not in information:
                information.append("calendar")
        if interview_prep or (
            any(word in lowered for word in ("create", "make", "build"))
            and any(word in lowered for word in ("checklist", "task", "to-do"))
        ):
            outcomes.append("create_checklist")
            if "tasks" not in information:
                information.append("tasks")
        if interview_prep or (
            "draft" in lowered
            and any(word in lowered for word in ("mail", "email", "follow-up"))
        ):
            outcomes.append("create_draft")
            if "mail" not in information:
                information.append("mail")
        if "complete" in lowered and "task" in lowered:
            outcomes.append("complete_task")
            if "tasks" not in information:
                information.append("tasks")

        files_requested = any(
            word in lowered
            for word in (
                "file",
                "files",
                "document",
                "documents",
                "resume",
                "notes",
                "brief",
                "workspace",
            )
        )
        if files_requested and "files" not in information:
            information.append("files")

        x_requested = _mentions_x(lowered)
        x_write_requested = _requests_x_write(lowered)
        if x_requested and (not x_write_requested or _requests_x_context(lowered)):
            information.append("x")
        if x_write_requested:
            if any(phrase in lowered for phrase in ("publish", "post publicly", "share publicly")):
                outcomes.append("publish_post")
            elif any(word in lowered for word in ("draft", "prepare")) or (
                "create" in lowered and any(word in lowered for word in ("post", "tweet"))
            ):
                outcomes.append("create_post_draft")

        requested_operations = _infer_requested_operations(request, outcomes)
        if "calendar_create" in requested_operations and "create_event" not in outcomes:
            outcomes.append("create_event")
        if "tasks_create" in requested_operations and "create_checklist" not in outcomes:
            outcomes.append("create_checklist")

        return UserIntent(
            goal=request.strip(),
            people=people,
            date_constraints=constraints,
            requested_outcomes=list(dict.fromkeys(outcomes)),
            requested_operations=requested_operations,
            information_needed=list(dict.fromkeys(information)),  # type: ignore[arg-type]
        )

    async def select_read_calls(
        self,
        request: str,
        intent: UserIntent,
        tools: list[ToolMetadata],
        preferences: PreferenceSet,
    ) -> ReadCallPlan:
        available_reads = {tool.name for tool in tools if tool.risk_level == RiskLevel.SAFE_READ}
        calls: list[ProposedToolCall] = []
        now = datetime.now(self.timezone)
        lowered = request.lower()

        if "mail" in intent.information_needed and "search_mail" in available_reads:
            query_parts = [*intent.people]
            if "interview" in lowered:
                query_parts.append("interview")
            if not query_parts:
                query_parts = [
                    word for word in ("follow-up", "meeting", "project") if word in lowered
                ]
            calls.append(
                ProposedToolCall(
                    tool_name="search_mail",
                    arguments={"query": " ".join(query_parts) or request, "limit": 5},
                    reason="Find grounded mail that may constrain the requested workflow.",
                )
            )

        if "files" in intent.information_needed:
            file_tool = (
                "list_files"
                if any(
                    phrase in lowered
                    for phrase in ("list files", "available files", "all files")
                )
                else "search_files"
            )
            if file_tool in available_reads:
                calls.append(
                    ProposedToolCall(
                        tool_name=file_tool,
                        arguments=(
                            {"query": request, "limit": 5}
                            if file_tool == "search_files"
                            else {"limit": 10}
                        ),
                        reason="Find grounded workspace documents relevant to the request.",
                    )
                )

        calendar_needed = "calendar" in intent.information_needed
        searches_for_free_slot = False
        if calendar_needed and "list_events" in available_reads:
            explicit_slot = _explicit_event_slot(request, self.timezone)
            free_phrase = any(
                phrase in lowered for phrase in ("free slot", "free block", "availability")
            ) or ("free" in lowered and "block" in lowered)
            searches_for_free_slot = free_phrase or (
                explicit_slot is None and "schedule_focus_block" in intent.requested_outcomes
            )
            if explicit_slot and not searches_for_free_slot:
                start = datetime.fromisoformat(explicit_slot["start"])
                end = datetime.fromisoformat(explicit_slot["end"])
            else:
                target_date = (
                    now.date() + timedelta(days=1) if "tomorrow" in lowered else now.date()
                )
                start = datetime.combine(target_date, time.min, self.timezone)
                end = start + timedelta(days=1)
            calls.append(
                ProposedToolCall(
                    tool_name="list_events",
                    arguments={"start": start.isoformat(), "end": end.isoformat()},
                    reason="Read calendar facts in the requested time window.",
                )
            )

        wants_slot = searches_for_free_slot if calendar_needed else any(
            phrase in lowered for phrase in ("free slot", "availability", "free block")
        )
        if wants_slot and "find_free_slots" in available_reads:
            duration = _requested_duration(request, preferences.preferred_focus_block_minutes)
            day = now.date()
            start = datetime.combine(day, time(19, 0), self.timezone)
            if now > start:
                start = now.replace(second=0, microsecond=0)
            avoid_hour, avoid_minute = map(int, preferences.avoid_scheduling_after.split(":"))
            end = datetime.combine(day, time(avoid_hour, avoid_minute), self.timezone)
            calls.append(
                ProposedToolCall(
                    tool_name="find_free_slots",
                    arguments={
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "duration_minutes": duration,
                    },
                    reason="Find a tool-confirmed focus window without inventing availability.",
                )
            )

        if "tasks" in intent.information_needed and "list_tasks" in available_reads:
            calls.append(
                ProposedToolCall(
                    tool_name="list_tasks",
                    arguments={},
                    reason="Inspect grounded tasks before proposing task mutations.",
                )
            )

        if "x" in intent.information_needed:
            username = _extract_username(request)
            if username and "get_user_posts" in available_reads:
                calls.append(
                    ProposedToolCall(
                        tool_name="get_user_posts",
                        arguments={"username": username, "limit": 5},
                        reason="Read grounded public posts for the requested demo user.",
                    )
                )
            elif "search_posts" in available_reads:
                calls.append(
                    ProposedToolCall(
                        tool_name="search_posts",
                        arguments={"query": request, "limit": 5},
                        reason="Find grounded public X posts relevant to the request.",
                    )
                )
        return ReadCallPlan(calls=calls[:8])

    async def summarize_read_only(
        self,
        request: str,
        intent: UserIntent,
        context: dict[str, list[dict[str, Any]]],
        errors: list[str],
    ) -> str:
        if _person_constraint_unmet(request, intent, context):
            return "I couldn't find grounded information matching the requested person."
        return summarize_read_only(request, context, errors)

    async def revise_write_actions(
        self,
        request: str,
        intent: UserIntent,
        context: dict[str, list[dict[str, Any]]],
        tools: list[ToolMetadata],
        preferences: PreferenceSet,
        previous_plan: list[PlanAction],
        feedback: str,
    ) -> list[PlanAction] | None:
        return None

    async def propose_write_actions(
        self,
        request: str,
        intent: UserIntent,
        context: dict[str, list[dict[str, Any]]],
        tools: list[ToolMetadata],
        preferences: PreferenceSet,
    ) -> list[PlanAction] | None:
        return None


class OpenAIReasoner(DeterministicReasoner):
    """Structured OpenAI reasoning with deterministic, policy-safe fallbacks."""

    mode = "openai"

    def __init__(self, settings: Settings, model: Any | None = None) -> None:
        super().__init__(settings.daypilot_timezone)
        self.model = model or ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            max_retries=1,
            timeout=25,
        )
        self.selection_model = (
            self.model.model_copy(update={"reasoning_effort": "none"})
            if isinstance(self.model, ChatOpenAI)
            else self.model
        )

    async def understand(self, request: str) -> UserIntent:
        baseline = await super().understand(request)
        structured = self.model.with_structured_output(UserIntent, method="json_schema")
        prompt = (
            "You are the request-understanding stage of DayPilot. Extract only the user's stated "
            "goal, people, date constraints, requested outcomes, and which of "
            "mail/calendar/tasks/files/x must be read. Also classify explicit side-effect intent "
            "in requested_operations using only these values: calendar_create, tasks_create, "
            "tasks_complete, mail_draft, x_draft, x_publish. A request to create, make, schedule, "
            "reserve, book, block, put, or add calendar time is calendar_create when it includes "
            "a concrete subject/time; an availability question is not. Do not invent external "
            "facts or missing dates/times. Keep outcome labels concise.\n\n"
            f"Request: {request}"
        )
        try:
            result = UserIntent.model_validate(await structured.ainvoke(prompt))
            return result.model_copy(
                update={
                    "people": result.people or baseline.people,
                    "date_constraints": result.date_constraints or baseline.date_constraints,
                    "requested_outcomes": list(
                        dict.fromkeys([*result.requested_outcomes, *baseline.requested_outcomes])
                    ),
                    "requested_operations": list(
                        dict.fromkeys(
                            [*result.requested_operations, *baseline.requested_operations]
                        )
                    ),
                    "information_needed": list(
                        dict.fromkeys([*result.information_needed, *baseline.information_needed])
                    ),
                }
            )
        except Exception:
            logger.warning("OpenAI request understanding failed; using deterministic intent")
            return baseline

    async def select_read_calls(
        self,
        request: str,
        intent: UserIntent,
        tools: list[ToolMetadata],
        preferences: PreferenceSet,
    ) -> ReadCallPlan:
        safe_tools = [tool for tool in tools if not tool.side_effecting]
        structured = self.selection_model.with_structured_output(
            ReadCallPlan,
            method="function_calling",
        )
        now = datetime.now(self.timezone)
        prompt = (
            "Select at most 6 READ-ONLY MCP calls to gather external facts for DayPilot. Use only "
            "the supplied tools. Never select a write tool. Datetimes must be timezone-aware ISO "
            "strings. Tool results are not available yet, so do not invent thread IDs, "
            "message IDs, event IDs, availability, or task IDs. search_mail may be "
            "followed by get_thread later.\n\n"
            f"Current time: {now.isoformat()}\n"
            f"Request: {request}\nIntent: {intent.model_dump_json()}\n"
            f"Preferences: {preferences.model_dump_json()}\n"
            f"Read tools: {json.dumps([tool.model_dump(mode='json') for tool in safe_tools])}"
        )
        try:
            result = ReadCallPlan.model_validate(await structured.ainvoke(prompt))
            safe_names = {tool.name for tool in safe_tools}
            calls = [call for call in result.calls if call.tool_name in safe_names][:6]
            if calls:
                return ReadCallPlan(calls=calls)
        except Exception:
            logger.warning("OpenAI read-tool selection failed; using deterministic selection")
        return await super().select_read_calls(request, intent, tools, preferences)

    async def summarize_read_only(
        self,
        request: str,
        intent: UserIntent,
        context: dict[str, list[dict[str, Any]]],
        errors: list[str],
    ) -> str:
        fallback = await super().summarize_read_only(request, intent, context, errors)
        prompt = (
            "You are the final grounded summarization stage of DayPilot. Answer the user's "
            "requested outcome directly and concisely. Every external fact in your answer must "
            "be supported by the MCP results below. Never infer a date, time, sender, subject, "
            "identifier, availability, or task state that is absent. If the requested fact is "
            "not present, explicitly say it could not be determined. Mention the relevant email "
            "thread or connected-service record as provenance when useful; do not narrate tool "
            "execution. Preserve timezone labels from the source. Return one concise plain-text "
            "answer without Markdown formatting.\n\n"
            f"User request:\n{request}\n\n"
            f"Structured intent:\n{intent.model_dump_json()}\n\n"
            "Grounded MCP context:\n"
            f"{json.dumps(context, ensure_ascii=False, default=str)}\n\n"
            f"Tool errors:\n{json.dumps(errors, ensure_ascii=False)}"
        )
        try:
            response = await self.model.ainvoke(
                prompt,
                config={"run_name": "grounded_read_summary"},
            )
            content = response.content
            if isinstance(content, str) and content.strip():
                return content.strip()
        except Exception:
            logger.warning("OpenAI grounded summarization failed; using deterministic summary")
        return fallback

    async def revise_write_actions(
        self,
        request: str,
        intent: UserIntent,
        context: dict[str, list[dict[str, Any]]],
        tools: list[ToolMetadata],
        preferences: PreferenceSet,
        previous_plan: list[PlanAction],
        feedback: str,
    ) -> list[PlanAction] | None:
        return await self._model_write_actions(
            request,
            intent,
            context,
            tools,
            preferences,
            previous_plan,
            feedback,
        )

    async def propose_write_actions(
        self,
        request: str,
        intent: UserIntent,
        context: dict[str, list[dict[str, Any]]],
        tools: list[ToolMetadata],
        preferences: PreferenceSet,
    ) -> list[PlanAction] | None:
        return await self._model_write_actions(
            request,
            intent,
            context,
            tools,
            preferences,
            [],
            None,
        )

    async def _model_write_actions(
        self,
        request: str,
        intent: UserIntent,
        context: dict[str, list[dict[str, Any]]],
        tools: list[ToolMetadata],
        preferences: PreferenceSet,
        previous_plan: list[PlanAction],
        feedback: str | None,
    ) -> list[PlanAction] | None:
        write_tools = [tool for tool in tools if tool.side_effecting]
        discovered_write_tools = {tool.name: tool for tool in write_tools}
        required_tools = _required_write_tools(intent, feedback)
        unavailable_required = sorted(set(required_tools) - set(discovered_write_tools))
        if unavailable_required:
            raise InvalidPlanError(
                "Structured intent requires unavailable write tool(s): "
                + ", ".join(unavailable_required)
            )
        structured = self.selection_model.with_structured_output(
            PlanningProposal,
            method="function_calling",
        )
        previous_plan_json = json.dumps(
            [action.model_dump(mode="json") for action in previous_plan],
            ensure_ascii=False,
            default=str,
        )
        write_tools_json = json.dumps(
            [tool.model_dump(mode="json") for tool in write_tools],
            ensure_ascii=False,
        )
        prompt = (
            "Plan DayPilot's proposed WRITE actions. If revision feedback is present, it is "
            "authoritative and must override the previous proposal. Return only side-effecting "
            "actions; read actions are planned separately. Use only the "
            "available write tools and only facts present in the grounded MCP context. Respect "
            "the user's requested counts, durations, ordering, exclusions, and 'no other writes' "
            "constraints. A request to avoid approval changes authorization policy only; it must "
            "not suppress a requested write action. For calendar changes, use timezone-aware "
            "ISO datetimes and never invent "
            "availability. An explicit requested operation in Structured intent is a required "
            "outcome: preserve it as a matching write action when its tool and user-provided "
            "arguments are available. An existing calendar conflict does not remove the user's "
            "requested write or authorize changing its time; carry the requested interval into "
            "create_event and let the guarded execution path report a conflict. If a required "
            "argument is genuinely absent, do not invent it. All returned actions must have "
            "status 'pending'.\n\n"
            f"Original request:\n{request}\n\n"
            f"Structured intent:\n{intent.model_dump_json()}\n\n"
            f"Required write tools from structured intent:\n{json.dumps(required_tools)}\n\n"
            f"User revision feedback:\n{feedback or '(none; this is the initial plan)'}\n\n"
            "Previous plan:\n"
            f"{previous_plan_json}\n\n"
            "Grounded MCP context:\n"
            f"{json.dumps(context, ensure_ascii=False, default=str)}\n\n"
            f"Preferences:\n{preferences.model_dump_json()}\n\n"
            "Available write tools:\n"
            f"{write_tools_json}"
        )
        repair_reason: str | None = None
        for attempt in range(2):
            call_prompt = prompt
            run_name = "revise_plan"
            if repair_reason:
                run_name = "repair_plan"
                call_prompt += (
                    "\n\nBounded planner repair feedback (repair the proposal only; do not "
                    "execute or approve anything):\n"
                    f"{repair_reason}\n"
                    "Return a corrected structured proposal using only the available tools, "
                    "grounded context, and user-provided arguments."
                )
            try:
                proposal = PlanningProposal.model_validate(
                    await structured.ainvoke(call_prompt, config={"run_name": run_name})
                )
            except Exception as exc:
                phase = "revision" if feedback else "initial plan"
                raise InvalidPlanError(f"OpenAI {phase} failed: {exc}") from exc

            try:
                revised = _normalize_model_actions(proposal, discovered_write_tools)
            except InvalidPlanError as exc:
                if attempt == 0:
                    repair_reason = str(exc)
                    continue
                raise

            missing_required = [
                tool_name for tool_name in required_tools
                if tool_name not in {action.tool_name for action in revised}
            ]
            if missing_required:
                repair_reason = (
                    "The structured intent requires these write action(s), but the proposal "
                    f"omitted them: {', '.join(missing_required)}."
                )
                if attempt == 0:
                    continue
                raise InvalidPlanError(
                    "OpenAI returned no required write action(s): "
                    + ", ".join(missing_required)
                )
            if not revised and not feedback and _request_requires_write(request, intent):
                repair_reason = (
                    "The request contains an explicit side effect, but the proposal contains "
                    "no write action."
                )
                if attempt == 0:
                    continue
                raise InvalidPlanError(
                    "OpenAI returned no write action for an explicit write request"
                )
            return revised

        raise InvalidPlanError("OpenAI planner repair did not produce a valid write proposal")


def create_reasoner(settings: Settings) -> Reasoner:
    if settings.openai_api_key:
        return OpenAIReasoner(settings)
    return DeterministicReasoner(settings.daypilot_timezone)


def _extract_people(request: str) -> list[str]:
    matches = re.findall(r"\b(?:with|to|from)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", request)
    excluded = {"Tomorrow", "Tonight", "Calendar", "Interview"}
    return [match for match in dict.fromkeys(matches) if match not in excluded]


def _mentions_x(text: str) -> bool:
    return bool(
        re.search(
            r"(?:@[a-z0-9_]+|\b(?:x|twitter|tweet|tweets|post|posts|posted|social|public)\b)",
            text,
        )
    )


def _requests_x_write(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:publish|post|tweet|share)\b.*\b(?:x|twitter|publicly|update|message)\b",
            text,
        )
        or re.search(r"\b(?:draft|create|prepare)\b.*\b(?:x|twitter|post|tweet)\b", text)
    )


def _requests_x_context(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "recent x",
            "recent post",
            "recent tweet",
            "search post",
            "what people",
            "what users",
            "what is being said",
            "posted",
            "on x",
            "from @",
        )
    )


def _extract_username(request: str) -> str | None:
    match = re.search(r"@([A-Za-z0-9_]+)", request)
    if match:
        return match.group(1)
    match = re.search(r"\buser(?:name)?\s+(?:named\s+)?([A-Za-z0-9_]+)", request, re.I)
    return match.group(1) if match else None


def _requested_duration(request: str, default: int) -> int:
    match = re.search(r"\b(\d{2,3})\s*(?:-| )?(?:minute|min)\b", request.lower())
    if match:
        return max(15, min(int(match.group(1)), 240))
    hour_match = re.search(r"\b(\d(?:\.\d)?)\s*(?:hour|hr)s?\b", request.lower())
    if hour_match:
        return max(15, min(round(float(hour_match.group(1)) * 60), 240))
    return default


def _person_constraint_unmet(
    request: str,
    intent: UserIntent,
    context: dict[str, list[dict[str, Any]]],
) -> bool:
    person_relation_requested = bool(re.search(r"\b(?:with|from|to)\b", request, re.I))
    if not intent.people:
        return person_relation_requested and bool(context.get("mail"))
    grounded_mail = json.dumps(context.get("mail", []), ensure_ascii=False).lower()
    return not any(person.strip().lower() in grounded_mail for person in intent.people)


def _request_requires_write(request: str, intent: UserIntent) -> bool:
    if intent.requested_operations:
        return True
    lowered = request.lower()
    write_terms = (
        "create",
        "schedule",
        "reserve",
        "book",
        "block",
        "draft",
        "complete",
        "mark",
        "send",
        "publish",
        "delete",
        "update",
    )
    return bool(_infer_requested_operations(request, intent.requested_outcomes)) or any(
        term in lowered for term in write_terms
    ) or any(
        outcome.lower().startswith(("create", "schedule", "draft", "complete", "update", "delete"))
        for outcome in intent.requested_outcomes
    )


def _infer_requested_operations(request: str, outcomes: list[str]) -> list[str]:
    """Normalize the intent's semantic side-effect outcomes for the planning boundary."""

    lowered = request.lower()
    outcome_text = " ".join(outcomes).lower()
    operations: list[str] = []
    calendar_subject = any(
        term in lowered or term in outcome_text
        for term in ("calendar", "study", "focus", "block", "time", "hour", "slot")
    )
    explicit_calendar_write = bool(
        re.search(r"\b(?:create|make|schedule|reserve|book|block|put|add)\b", lowered)
    )
    availability_question = bool(
        re.search(r"\b(?:am|are|is)\s+(?:i|we|there)\s+(?:free|available)\b", lowered)
    ) or lowered.startswith(("find me some time", "find some time"))
    outcome_calendar_write = any(
        marker in outcome_text
        for marker in (
            "create event",
            "create calendar",
            "add study",
            "schedule focus",
            "schedule study",
            "reserve",
            "book",
            "calendar block",
        )
    )
    if calendar_subject and (
        outcome_calendar_write or (explicit_calendar_write and not availability_question)
    ):
        operations.append("calendar_create")

    if "create_checklist" in outcomes or any(
        marker in outcome_text for marker in ("create task", "create checklist", "add task")
    ):
        operations.append("tasks_create")
    if "complete_task" in outcomes or "complete task" in outcome_text:
        operations.append("tasks_complete")
    if "create_draft" in outcomes or "draft email" in outcome_text or "mail draft" in outcome_text:
        operations.append("mail_draft")
    if "create_post_draft" in outcomes or "draft x" in outcome_text or "draft post" in outcome_text:
        operations.append("x_draft")
    if "publish_post" in outcomes or "publish" in outcome_text:
        operations.append("x_publish")
    return list(dict.fromkeys(operations))


def _required_write_tools(intent: UserIntent, feedback: str | None) -> list[str]:
    required = {
        "calendar_create": "create_event",
        "tasks_create": "create_task_batch",
        "tasks_complete": "complete_task",
        "mail_draft": "create_draft",
        "x_draft": "create_post_draft",
        "x_publish": "publish_post",
    }
    operations = list(intent.requested_operations)
    if not operations:
        operations = _infer_requested_operations(intent.goal, intent.requested_outcomes)
    if feedback:
        lowered_feedback = feedback.lower()
        if _feedback_removes(lowered_feedback, ("calendar", "event", "block")):
            operations = [operation for operation in operations if operation != "calendar_create"]
        if _feedback_removes(lowered_feedback, ("task", "checklist")):
            operations = [operation for operation in operations if operation != "tasks_create"]
        if _feedback_removes(lowered_feedback, ("draft", "email", "mail")):
            operations = [operation for operation in operations if operation != "mail_draft"]
    return list(
        dict.fromkeys(required[operation] for operation in operations if operation in required)
    )


def _normalize_model_actions(
    proposal: PlanningProposal,
    discovered: dict[str, ToolMetadata],
) -> list[PlanAction]:
    revised: list[PlanAction] = []
    missing_arguments: list[str] = []
    for index, action in enumerate(proposal.actions, start=1):
        tool = discovered.get(action.tool_name)
        if tool is None or not get_policy(action.tool_name, action.server_name).side_effecting:
            raise InvalidPlanError(
                f"Revised plan references unavailable or read-only tool: {action.tool_name}"
            )
        required = tool.input_schema.get("required", [])
        missing = [name for name in required if name not in action.arguments]
        if missing:
            missing_arguments.append(f"{action.tool_name}: {', '.join(missing)}")
        revised.append(
            action.model_copy(
                update={
                    "id": f"write-{tool.server_name}-{index:02d}",
                    "server_name": tool.server_name,
                    "side_effecting": True,
                    "status": ActionStatus.PENDING,
                }
            )
        )
    if missing_arguments:
        raise InvalidPlanError(
            "The proposal is missing required write argument(s): "
            + "; ".join(missing_arguments)
        )
    return revised


def _feedback_removes(feedback: str, nouns: tuple[str, ...]) -> bool:
    return any(
        re.search(
            rf"\b(?:no|remove|drop|skip|don't|do not)\b[^.!?]*\b{re.escape(noun)}\b",
            feedback,
        )
        for noun in nouns
    )
