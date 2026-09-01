from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from backend.app.domain.errors import InvalidPlanError
from backend.app.domain.models import (
    ActionStatus,
    PlanAction,
    PreferenceSet,
    ProposedToolCall,
    ToolMetadata,
    UserIntent,
)
from backend.app.mcp.policy import get_policy


class PlanBuilder:
    def __init__(self, timezone_name: str) -> None:
        self.timezone = ZoneInfo(timezone_name)

    def build(
        self,
        request: str,
        intent: UserIntent,
        context: dict[str, list[dict[str, Any]]],
        tools: list[ToolMetadata],
        preferences: PreferenceSet,
        feedback: str | None = None,
    ) -> list[PlanAction]:
        actions = self._read_actions(context)
        tool_names = {tool.name for tool in tools}
        lowered_feedback = (feedback or "").lower()

        if (
            "calendar_create" in intent.requested_operations
            or "create_event" in intent.requested_outcomes
        ) and "create_event" not in tool_names:
            raise InvalidPlanError(
                "Structured intent requires unavailable write tool: create_event"
            )

        if (
            any(
                outcome in intent.requested_outcomes
                for outcome in ("schedule_focus_block", "create_event")
            )
            and "create_event" in tool_names
            and not _feedback_removes(lowered_feedback, ("calendar", "event", "block"))
        ):
            event_action = self._event_action(request, intent, context, preferences, feedback)
            if event_action:
                actions.append(event_action)

        if "create_checklist" in intent.requested_outcomes and not _feedback_removes(
            lowered_feedback, ("task", "checklist")
        ):
            if _is_single_task_request(request) and "create_task" in tool_names:
                actions.append(self._task_action(request))
            elif "create_task_batch" in tool_names:
                actions.append(self._checklist_action(request, context, preferences))

        if (
            "create_draft" in intent.requested_outcomes
            and "create_draft" in tool_names
            and not _feedback_removes(lowered_feedback, ("draft", "email", "mail"))
        ):
            draft_action = self._draft_action(context, intent)
            if draft_action:
                actions.append(draft_action)

        if "complete_task" in intent.requested_outcomes and "complete_task" in tool_names:
            complete_action = self._complete_task_action(request, context)
            if complete_action:
                actions.append(complete_action)

        if "create_post_draft" in intent.requested_outcomes and "create_post_draft" in tool_names:
            actions.append(self._post_draft_action(request, context))

        if "publish_post" in intent.requested_outcomes and "publish_post" in tool_names:
            actions.append(self._publish_post_action(request, context))

        return self.finalize(actions, request, intent, context, tools)

    def read_actions(
        self,
        context: dict[str, list[dict[str, Any]]],
    ) -> list[PlanAction]:
        return self._read_actions(context)

    def validate(
        self,
        actions: list[PlanAction],
        tools: list[ToolMetadata],
    ) -> None:
        self._validate(actions, tools)

    def finalize(
        self,
        actions: list[PlanAction],
        request: str,
        intent: UserIntent,
        context: dict[str, list[dict[str, Any]]],
        tools: list[ToolMetadata],
    ) -> list[PlanAction]:
        """Bind grounded information dependencies to the exact plan revision."""
        temporal_request = _requires_grounded_temporal_anchor(request, intent)
        if temporal_request and not _grounded_temporal_anchor(context, self.timezone):
            actions = [
                action
                for action in actions
                if action.tool_name not in {"create_event", "create_task", "create_task_batch"}
            ]
        actions = _derive_dependencies(actions, request, intent, context, self.timezone)
        actions = _bind_grounded_write_arguments(actions, request, context, self.timezone)
        actions = _remove_blocked_writes(actions, request, intent, context, self.timezone)
        self._validate(actions, tools)
        return actions

    def order_read_calls(
        self,
        request: str,
        intent: UserIntent,
        calls: list[ProposedToolCall],
    ) -> list[ProposedToolCall]:
        if not _requires_grounded_temporal_anchor(request, intent):
            return calls
        priority = {
            "search_mail": 0,
            "get_thread": 1,
            "get_message": 1,
            "search_web": 2,
            "list_events": 3,
            "find_free_slots": 3,
        }
        ordered = sorted(
            enumerate(calls),
            key=lambda pair: (priority.get(pair[1].tool_name, 2), pair[0]),
        )
        return [call for _, call in ordered]

    def ground_read_arguments(
        self,
        request: str,
        intent: UserIntent,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, list[dict[str, Any]]],
        preferences: PreferenceSet | None = None,
    ) -> dict[str, Any]:
        grounded = dict(arguments)
        if tool_name == "search_web":
            if intent.request_kind == "hybrid" and "mail" in intent.information_needed:
                company = _grounded_company(context)
                if company:
                    grounded["query"] = f"{company} company background and recent news"
                elif _generic_company_research_request(request):
                    grounded.pop("query", None)
            return grounded
        if tool_name == "get_thread":
            grounded_thread_id = _grounded_thread_id(context)
            if grounded_thread_id:
                grounded["thread_id"] = grounded_thread_id
            elif not _request_thread_id(request):
                # A model-provided identifier is not evidence. Remove it so
                # the workflow records a blocked read instead of querying an
                # arbitrary provider thread.
                grounded.pop("thread_id", None)
            return grounded

        if tool_name not in {"list_events", "find_free_slots"}:
            return grounded

        anchor = _grounded_temporal_anchor(context, self.timezone)
        explicit_slot = _explicit_event_slot(request, self.timezone)
        if anchor is not None:
            if tool_name == "list_events":
                grounded.update(
                    {
                        "start": (anchor - timedelta(hours=3)).isoformat(),
                        "end": (anchor + timedelta(hours=2)).isoformat(),
                    }
                )
            else:
                day_start = datetime.combine(anchor.date(), time(7, 0), self.timezone)
                grounded.update(
                    {
                        "start": max(day_start, anchor - timedelta(hours=4)).isoformat(),
                        "end": anchor.isoformat(),
                    }
                )
        elif explicit_slot is not None and tool_name == "list_events":
            grounded.update(explicit_slot)

        if tool_name == "find_free_slots" and _argument_unavailable(
            grounded.get("duration_minutes")
        ):
            default_duration = preferences.preferred_focus_block_minutes if preferences else 90
            grounded["duration_minutes"] = _requested_duration(request, default_duration)
        return grounded

    def _read_actions(self, context: dict[str, list[dict[str, Any]]]) -> list[PlanAction]:
        actions: list[PlanAction] = []
        index = 1
        for server_name, records in context.items():
            for record in records:
                success = record.get("success", False)
                actions.append(
                    PlanAction(
                        id=f"read-{index:02d}",
                        description=record.get("description", f"Run {record['tool_name']}"),
                        server_name=server_name,
                        tool_name=record["tool_name"],
                        arguments=record.get("arguments", {}),
                        reason=record.get("reason", "Gather grounded service context."),
                        side_effecting=False,
                        status=ActionStatus.COMPLETED if success else ActionStatus.FAILED,
                    )
                )
                index += 1
        return actions

    def _event_action(
        self,
        request: str,
        intent: UserIntent,
        context: dict[str, list[dict[str, Any]]],
        preferences: PreferenceSet,
        feedback: str | None,
    ) -> PlanAction | None:
        free_result = _latest_result(context, "calendar", "find_free_slots")
        slots = free_result.get("slots", []) if free_result else []
        explicit_slot = _explicit_event_slot(request, self.timezone)
        if slots and explicit_slot is None:
            slot = dict(slots[0])
            duration_override = _duration_from_text(feedback or request)
            if duration_override:
                start = datetime.fromisoformat(slot["start"])
                end = start + timedelta(minutes=duration_override)
                available_end = datetime.fromisoformat(slot["end"])
                if end <= available_end:
                    slot["end"] = end.isoformat()
        elif explicit_slot:
            calendar_result = _latest_result(context, "calendar", "list_events") or {}
            if _has_calendar_conflict(calendar_result.get("events", []), explicit_slot):
                return None
            slot = explicit_slot
        else:
            return None
        interview = _interview_message(context)
        explicit_title = _explicit_event_title(request)
        title = explicit_title or (
            "Interview preparation" if interview else _study_title(request, intent)
        )
        description = "Focused preparation block created by DayPilot."
        if interview:
            description = f"Prepare for: {interview['subject']}. Source: Mail MCP."
        elif explicit_title:
            description = (
                "Requested event time was checked against Calendar MCP and had no conflicts."
            )
        return PlanAction(
            id="write-calendar-01",
            description=(
                f"Reserve {self._format_range(slot['start'], slot['end'])} for {title.lower()}"
            ),
            server_name="calendar",
            tool_name="create_event",
            arguments={
                "title": title,
                "start": slot["start"],
                "end": slot["end"],
                "description": description,
            },
            reason=_event_reason(context, explicit_slot, slots),
            side_effecting=True,
            status=ActionStatus.PENDING,
        )

    def _checklist_action(
        self,
        request: str,
        context: dict[str, list[dict[str, Any]]],
        preferences: PreferenceSet,
    ) -> PlanAction:
        interview = _interview_message(context)
        now = datetime.now(self.timezone)
        due_hour, due_minute = map(int, preferences.preferred_task_due_time.split(":"))
        due_at = datetime.combine(now.date(), time(due_hour, due_minute), self.timezone)
        if due_at <= now:
            due_at = now + timedelta(hours=1)
        subject = interview["subject"] if interview else "the requested preparation"
        checklist = [
            "Review the role and interview format",
            "Prepare three concise STAR examples",
            "Rehearse API design and reliability trade-offs",
            "Test the meeting setup and prepare questions",
        ]
        tasks = [
            {
                "title": title,
                "notes": f"Generated for {subject}.",
                "due_at": due_at.isoformat(),
            }
            for title in checklist
        ]
        return PlanAction(
            id="write-tasks-01",
            description="Create a four-item interview preparation checklist",
            server_name="tasks",
            tool_name="create_task_batch",
            arguments={"tasks": tasks},
            reason="The requested preparation outcome benefits from an explicit checklist.",
            side_effecting=True,
            status=ActionStatus.PENDING,
        )

    def _task_action(self, request: str) -> PlanAction:
        now = datetime.now(self.timezone)
        lowered = request.lower()
        has_due_constraint = bool(re.search(r"\b(?:due|today|tomorrow|tonight|on)\b", lowered))
        target_date = _task_date(request, now.date())
        requested_time = _task_time(request)
        due_at = datetime.combine(target_date, requested_time or time.min, self.timezone)
        title = _task_title(request)
        time_note = (
            " Google Tasks preserves the due date but does not preserve an exact due time."
            if requested_time
            else ""
        )
        arguments: dict[str, Any] = {"title": title}
        if has_due_constraint:
            arguments["due_at"] = due_at.isoformat()
        description = f"Create one Google Task titled “{title}”"
        if has_due_constraint:
            description += f" due {due_at.strftime('%b %-d, %Y')}."
        else:
            description += "."
        description += time_note
        if requested_time:
            reason = (
                "The user requested one task. Google Tasks stores the requested due date; "
                "its API does not preserve a specific due time."
            )
        elif has_due_constraint:
            reason = "The user requested one task with this due date."
        else:
            reason = "The user requested one task without a due-date constraint."
        return PlanAction(
            id="write-tasks-01",
            description=description,
            server_name="tasks",
            tool_name="create_task",
            arguments=arguments,
            reason=reason,
            side_effecting=True,
            status=ActionStatus.PENDING,
        )

    def _draft_action(
        self,
        context: dict[str, list[dict[str, Any]]],
        intent: UserIntent,
    ) -> PlanAction | None:
        message = _interview_message(context)
        if message is None:
            return None
        recipient = message.get("sender")
        if not recipient or (recipient.endswith("@example.com") and recipient.startswith("alex.")):
            participants = _mail_participants(context)
            recipient = next((item for item in participants if not item.startswith("alex.")), None)
        if not recipient:
            return None
        person = (
            intent.people[0] if intent.people else recipient.split("@")[0].split(".")[0].title()
        )
        role = message["subject"].replace("Interview confirmed — ", "")
        body = (
            f"Hi {person},\n\nThank you for taking the time to speak with me about the {role} "
            "opportunity. I appreciated learning more about the team and the problems "
            "it is solving.\n\n"
            "Please pass along my thanks to everyone involved. I look forward to hearing "
            "about next steps."
            "\n\nBest,\nAlex"
        )
        return PlanAction(
            id="write-mail-01",
            description=f"Save a follow-up email draft to {recipient}",
            server_name="mail",
            tool_name="create_draft",
            arguments={
                "recipient": recipient,
                "subject": f"Thank you — {role} interview",
                "body": body,
            },
            reason=(
                "Mail MCP provided the recipient and interview subject; the draft will not be sent."
            ),
            side_effecting=True,
            status=ActionStatus.PENDING,
        )

    def _complete_task_action(
        self,
        request: str,
        context: dict[str, list[dict[str, Any]]],
    ) -> PlanAction | None:
        task_result = _latest_result(context, "tasks", "list_tasks") or {}
        request_words = set(re.findall(r"[a-z]{3,}", request.lower()))
        candidates = [
            task
            for task in task_result.get("tasks", [])
            if not task.get("completed")
            and request_words.intersection(re.findall(r"[a-z]{3,}", task["title"].lower()))
        ]
        if not candidates:
            return None
        task = candidates[0]
        return PlanAction(
            id="write-tasks-complete-01",
            description=f"Complete task: {task['title']}",
            server_name="tasks",
            tool_name="complete_task",
            arguments={"task_id": task["id"]},
            reason="Tasks MCP returned this exact task ID as the best grounded match.",
            side_effecting=True,
            status=ActionStatus.PENDING,
        )

    def _post_draft_action(
        self,
        request: str,
        context: dict[str, list[dict[str, Any]]],
    ) -> PlanAction:
        text = _post_text_from_context(request, context)
        return PlanAction(
            id="write-x-draft-01",
            description="Save a grounded X post draft for review",
            server_name="x",
            tool_name="create_post_draft",
            arguments={"text": text},
            reason="The requested public update remains a draft until a person approves it.",
            side_effecting=True,
            status=ActionStatus.PENDING,
        )

    def _publish_post_action(
        self,
        request: str,
        context: dict[str, list[dict[str, Any]]],
    ) -> PlanAction:
        text = _post_text_from_context(request, context)
        return PlanAction(
            id="write-x-publish-01",
            description="Publish the proposed X post",
            server_name="x",
            tool_name="publish_post",
            arguments={"text": text},
            reason="Publishing is an external public change and requires explicit approval.",
            side_effecting=True,
            status=ActionStatus.PENDING,
        )

    def _validate(self, actions: list[PlanAction], tools: list[ToolMetadata]) -> None:
        discovered = {tool.name: tool for tool in tools}
        ids: set[str] = set()
        for action in actions:
            if action.id in ids:
                raise InvalidPlanError(f"Duplicate plan action ID: {action.id}")
            ids.add(action.id)
            tool = discovered.get(action.tool_name)
            if tool is None:
                raise InvalidPlanError(f"Plan references unavailable tool: {action.tool_name}")
            policy = get_policy(action.tool_name, action.server_name)
            if action.side_effecting != policy.side_effecting:
                raise InvalidPlanError(f"Risk mismatch for tool: {action.tool_name}")
            if action.server_name != tool.server_name:
                raise InvalidPlanError(f"Server mismatch for tool: {action.tool_name}")
        _validate_dependencies(actions)

    def _format_range(self, start: str, end: str) -> str:
        start_at = datetime.fromisoformat(start)
        end_at = datetime.fromisoformat(end)
        return f"{start_at.strftime('%-I:%M %p')}–{end_at.strftime('%-I:%M %p')}"


def _latest_result(
    context: dict[str, list[dict[str, Any]]],
    server: str,
    tool_name: str,
) -> dict[str, Any] | None:
    matches = [
        record.get("result")
        for record in context.get(server, [])
        if record.get("tool_name") == tool_name and record.get("success")
    ]
    return matches[-1] if matches else None


def _grounded_thread_id(
    context: dict[str, list[dict[str, Any]]],
) -> str | None:
    """Choose the provider-ranked thread ID returned by the latest mail search."""
    for record in reversed(context.get("mail", [])):
        if record.get("tool_name") != "search_mail" or not record.get("success"):
            continue
        result = record.get("result") or {}
        threads = result.get("threads") if isinstance(result, dict) else None
        for thread in threads if isinstance(threads, list) else []:
            if not isinstance(thread, dict):
                continue
            for key in ("thread_id", "threadId"):
                value = thread.get(key)
                if value is not None and str(value).strip():
                    return str(value)
    return None


def _grounded_company(
    context: dict[str, list[dict[str, Any]]],
) -> str | None:
    suffix_pattern = r"Labs|Inc|LLC|Ltd|Limited|Corp|Corporation|Company|Technologies|Systems|Group"
    pattern = re.compile(
        rf"\b([A-Z][A-Za-z0-9&.-]*(?:\s+[A-Z][A-Za-z0-9&.-]*){{0,3}}\s+"
        rf"(?:{suffix_pattern}))\b"
    )
    for record in reversed(context.get("mail", [])):
        if record.get("tool_name") not in {"get_thread", "get_message"} or not record.get(
            "success"
        ):
            continue
        result = record.get("result") or {}
        messages = result.get("messages") if isinstance(result, dict) else None
        candidates = messages if isinstance(messages, list) else [result]
        for message in candidates:
            if not isinstance(message, dict):
                continue
            text = "\n".join(str(message.get(key) or "") for key in ("subject", "body", "sender"))
            match = pattern.search(text)
            if match:
                return match.group(1)
    return None


def _generic_company_research_request(request: str) -> bool:
    lowered = request.lower()
    return bool(
        re.search(r"\bresearch\s+(?:the\s+)?company\b", lowered)
        and not re.search(r"\bresearch\s+(?:the\s+)?company\s+[A-Z0-9]", request)
    )


def _request_thread_id(request: str) -> str | None:
    match = re.search(
        r"\bthread(?:_id|\s+id)\s*[:=]\s*([A-Za-z0-9_-]+)",
        request,
        re.I,
    )
    return match.group(1) if match else None


def _argument_unavailable(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or _contains_unresolved_reference(value)
    return False


def _requested_duration(request: str, default: int) -> int:
    duration = _duration_from_text(request) or _hour_duration_from_text(request) or default
    return max(15, min(duration, 480))


def _interview_message(context: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    for record in reversed(context.get("mail", [])):
        result = record.get("result") or {}
        for message in result.get("messages", []):
            if "interview" in (message.get("subject", "") + message.get("body", "")).lower():
                return message
    return None


def _derive_dependencies(
    actions: list[PlanAction],
    request: str,
    intent: UserIntent,
    context: dict[str, list[dict[str, Any]]],
    timezone: ZoneInfo,
) -> list[PlanAction]:
    enriched: list[PlanAction] = []
    temporal_dependency = _requires_grounded_temporal_anchor(request, intent)

    def prior(*tool_names: str) -> str | None:
        for candidate in reversed(enriched):
            if candidate.tool_name in tool_names:
                return candidate.id
        return None

    for action in actions:
        dependencies: list[str] = []

        if action.tool_name == "get_thread":
            _add_dependency(dependencies, prior("search_mail"))
        elif action.tool_name == "search_web" and intent.request_kind == "hybrid":
            if "mail" in intent.information_needed:
                _add_dependency(dependencies, prior("get_thread", "get_message"))
        elif action.tool_name == "read_file":
            _add_dependency(dependencies, prior("search_files"))
        elif action.tool_name in {"list_events", "find_free_slots"} and temporal_dependency:
            _add_dependency(dependencies, prior("get_thread", "get_message"))
        elif action.tool_name == "create_event":
            _add_dependency(dependencies, prior("find_free_slots") or prior("list_events"))
        elif action.tool_name in {"create_task", "create_task_batch"} and temporal_dependency:
            _add_dependency(
                dependencies,
                prior("find_free_slots") or prior("get_thread", "get_message"),
            )
        elif action.tool_name == "complete_task":
            _add_dependency(dependencies, prior("list_tasks"))
        elif action.tool_name == "create_draft":
            _add_dependency(dependencies, prior("get_thread", "get_message"))
        elif action.tool_name in {"create_post_draft", "publish_post"}:
            _add_dependency(
                dependencies,
                prior("read_file", "search_posts", "get_post", "get_user_posts"),
            )

        enriched.append(action.model_copy(update={"depends_on": dependencies}))
    return enriched


def _remove_blocked_writes(
    actions: list[PlanAction],
    request: str,
    intent: UserIntent,
    context: dict[str, list[dict[str, Any]]],
    timezone: ZoneInfo,
) -> list[PlanAction]:
    """Remove writes whose grounded prerequisite did not produce usable data.

    A failed read is still retained in the plan context for transparency, but a
    side-effecting action that depends on it must never reach approval with
    empty or model-invented arguments. This is deliberately narrow: unrelated
    writes remain eligible when their own inputs are grounded.
    """
    failed_ids = {action.id for action in actions if action.status == ActionStatus.FAILED}
    blocked_ids: set[str] = set()
    changed = True
    while changed:
        changed = False
        for action in actions:
            if action.id in blocked_ids or not action.side_effecting:
                continue
            if any(
                dependency in failed_ids or dependency in blocked_ids
                for dependency in action.depends_on
            ):
                blocked_ids.add(action.id)
                changed = True

    if _requires_grounded_temporal_anchor(request, intent):
        explicit_slot = _explicit_event_slot(request, timezone)
        if explicit_slot is None:
            free_slot_actions = {
                action.id for action in actions if action.tool_name == "find_free_slots"
            }
            grounded_slots = _grounded_free_slots(context)
            free_slot_read_failed = bool(free_slot_actions) and not grounded_slots
            if free_slot_read_failed:
                for action in actions:
                    if not action.side_effecting:
                        continue
                    if action.tool_name == "create_event":
                        blocked_ids.add(action.id)
                    elif action.tool_name in {"create_task", "create_task_batch"} and (
                        any(dependency in free_slot_actions for dependency in action.depends_on)
                        or _contains_unresolved_reference(action.arguments)
                    ):
                        blocked_ids.add(action.id)

    return [action for action in actions if action.id not in blocked_ids]


def _bind_grounded_write_arguments(
    actions: list[PlanAction],
    request: str,
    context: dict[str, list[dict[str, Any]]],
    timezone: ZoneInfo,
) -> list[PlanAction]:
    """Bind provider-facing write fields to successful semantic read results."""
    slots = _grounded_free_slots(context)
    explicit_slot = _explicit_event_slot(request, timezone)
    explicit_title = _explicit_event_title(request)
    if not slots or explicit_slot is not None:
        return actions
    selected_slot = slots[0]
    bound: list[PlanAction] = []
    for action in actions:
        if action.tool_name != "create_event" or not action.side_effecting:
            bound.append(action)
            continue
        arguments = dict(action.arguments)
        arguments.update(
            {
                "start": selected_slot.get("start"),
                "end": selected_slot.get("end"),
            }
        )
        if explicit_title:
            arguments["title"] = explicit_title
        bound.append(action.model_copy(update={"arguments": arguments}))
    return bound


def _grounded_free_slots(
    context: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    result = _latest_result(context, "calendar", "find_free_slots") or {}
    slots = result.get("slots")
    return (
        [
            slot
            for slot in slots
            if isinstance(slot, dict)
            and isinstance(slot.get("start"), str)
            and isinstance(slot.get("end"), str)
        ]
        if isinstance(slots, list)
        else []
    )


def _contains_unresolved_reference(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_unresolved_reference(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_unresolved_reference(item) for item in value)
    if not isinstance(value, str):
        return False
    return bool(
        re.search(
            r"\{\{[^{}]+\}\}|\$\{[^{}]+\}|<[^>\n]*(?:latest|grounded|resolved|from|interview|slot|thread|timezone)[^>\n]*>",
            value,
        )
    )


def _add_dependency(dependencies: list[str], dependency_id: str | None) -> None:
    if dependency_id and dependency_id not in dependencies:
        dependencies.append(dependency_id)


def _validate_dependencies(actions: list[PlanAction]) -> None:
    action_ids = {action.id for action in actions}
    positions = {action.id: index for index, action in enumerate(actions)}
    graph: dict[str, list[str]] = {}
    for action in actions:
        if len(action.depends_on) != len(set(action.depends_on)):
            raise InvalidPlanError(f"Duplicate dependencies for action: {action.id}")
        if action.id in action.depends_on:
            raise InvalidPlanError(f"Plan action cannot depend on itself: {action.id}")
        missing = [dependency for dependency in action.depends_on if dependency not in action_ids]
        if missing:
            raise InvalidPlanError(
                f"Plan action {action.id} references missing dependencies: {', '.join(missing)}"
            )
        graph[action.id] = action.depends_on

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(action_id: str) -> None:
        if action_id in visiting:
            raise InvalidPlanError("Plan dependency graph contains a cycle")
        if action_id in visited:
            return
        visiting.add(action_id)
        for dependency_id in graph[action_id]:
            visit(dependency_id)
        visiting.remove(action_id)
        visited.add(action_id)

    for action_id in graph:
        visit(action_id)

    for action in actions:
        later = [
            dependency
            for dependency in action.depends_on
            if positions[dependency] >= positions[action.id]
        ]
        if later:
            raise InvalidPlanError(
                f"Plan action {action.id} depends on a later action: {', '.join(later)}"
            )


def _requires_grounded_temporal_anchor(request: str, intent: UserIntent) -> bool:
    domains = set(intent.information_needed)
    if not {"mail", "calendar"}.issubset(domains):
        return False
    lowered = request.lower()
    return bool(
        re.search(r"\b(?:before|after|around|when)\b", lowered)
        or re.search(r"\b(?:free|available)\b[^.!?]*\b(?:it|that|this)\b", lowered)
    )


def _grounded_temporal_anchor(
    context: dict[str, list[dict[str, Any]]],
    timezone: ZoneInfo,
) -> datetime | None:
    month_pattern = (
        r"January|February|March|April|May|June|July|August|September|October|"
        r"November|December"
    )
    named_pattern = re.compile(
        rf"\b(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
        rf"(?P<month>{month_pattern})\s+(?P<day>\d{{1,2}})(?:,\s*(?P<year>20\d{{2}}))?"
        rf"\s+at\s+(?P<hour>\d{{1,2}})(?::(?P<minute>\d{{2}}))?\s*(?P<period>AM|PM)\b",
        re.I,
    )
    iso_pattern = re.compile(
        r"\b(?P<year>20\d{2})-(?P<month>\d{2})-(?P<day>\d{2})"
        r"(?:T|\s+)(?P<hour>\d{1,2}):(?P<minute>\d{2})\b"
    )
    now = datetime.now(timezone)
    for record in context.get("mail", []):
        if not record.get("success"):
            continue
        result = record.get("result") or {}
        messages = result.get("messages") if isinstance(result, dict) else None
        for message in messages if isinstance(messages, list) else []:
            text = f"{message.get('subject', '')}\n{message.get('body', '')}"
            named = named_pattern.search(text)
            if named:
                hour = int(named.group("hour")) % 12
                if named.group("period").lower() == "pm":
                    hour += 12
                try:
                    parsed = datetime.strptime(
                        f"{named.group('month')} {named.group('day')} "
                        f"{named.group('year') or now.year} {hour}:{named.group('minute') or '00'}",
                        "%B %d %Y %H:%M",
                    )
                    return parsed.replace(tzinfo=timezone)
                except ValueError:
                    continue
            iso = iso_pattern.search(text)
            if iso:
                try:
                    return datetime(
                        int(iso.group("year")),
                        int(iso.group("month")),
                        int(iso.group("day")),
                        int(iso.group("hour")),
                        int(iso.group("minute")),
                        tzinfo=timezone,
                    )
                except ValueError:
                    continue
    return None


def _mail_participants(context: dict[str, list[dict[str, Any]]]) -> list[str]:
    for record in context.get("mail", []):
        result = record.get("result") or {}
        participants = result.get("participants")
        if participants:
            return [participant.strip() for participant in participants.split(",")]
    return []


def _post_text_from_context(
    request: str,
    context: dict[str, list[dict[str, Any]]],
) -> str:
    for record in reversed(context.get("files", [])):
        if record.get("tool_name") != "read_file" or not record.get("success"):
            continue
        result = record.get("result") or {}
        content = str(result.get("content", ""))
        content = "\n".join(
            line
            for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        sentences = [
            sentence.strip().replace("\n", " ")
            for sentence in re.split(r"(?<=[.!?])\s+", content)
            if sentence.strip() and not sentence.lstrip().startswith("#")
        ]
        if sentences:
            text = f"From the workspace: {sentences[0]}"
            return text[:280].rstrip()
    normalized_request = " ".join(request.split())
    return f"DayPilot update: {normalized_request}"[:280].rstrip()


def _feedback_removes(feedback: str, nouns: tuple[str, ...]) -> bool:
    return any(
        re.search(rf"\b(?:no|remove|don't|do not)\b[^.!?]*\b{re.escape(noun)}\b", feedback)
        for noun in nouns
    )


def _is_single_task_request(request: str) -> bool:
    lowered = request.lower()
    # A title can legitimately contain words such as “Tasks”; only inspect the
    # request structure when deciding whether the user asked for one or many.
    structure = re.sub(r"[\"“'][^\"”']*[\"”']", "", lowered)
    if "checklist" in structure or re.search(r"\b(?:tasks|to-dos|items|steps)\b", structure):
        return False
    return bool(
        re.search(r"\b(?:a|one|1)\s+(?:google\s+)?task\b", lowered)
        or re.search(r"\bcreate\s+(?:a\s+)?(?:google\s+)?task\b", lowered)
    )


def _task_title(request: str) -> str:
    quoted_matches = list(re.finditer(r"(?:called|named|titled)\s+[\"“']([^\"”']+)[\"”']", request))
    if quoted_matches:
        return quoted_matches[-1].group(1).strip()
    plain_matches = list(
        re.finditer(
            r"(?:called|named|titled)\s+(.+?)(?=\s+(?:due|for|on|tomorrow|today)\b|[.!?]|$)",
            request,
            re.I,
        )
    )
    return plain_matches[-1].group(1).strip() if plain_matches else "Requested task"


def _task_time(request: str) -> time | None:
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\b", request, re.I)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == "pm":
        hour += 12
    return time(hour, int(match.group(2) or 0))


def _task_date(request: str, today: date) -> date:
    lowered = request.lower()
    iso_match = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", lowered)
    if iso_match:
        try:
            return date(
                int(iso_match.group(1)),
                int(iso_match.group(2)),
                int(iso_match.group(3)),
            )
        except ValueError:
            pass
    month_match = re.search(
        r"\b(?:january|february|march|april|may|june|july|august|september|"
        r"october|november|december)\s+(\d{1,2})(?:,\s*(20\d{2}))?\b",
        lowered,
    )
    if month_match:
        month_name = re.search(
            r"\b(january|february|march|april|may|june|july|august|"
            r"september|october|november|december)\b",
            lowered,
        )
        if month_name:
            try:
                parsed = datetime.strptime(
                    f"{month_name.group(1)} {month_match.group(1)} "
                    f"{month_match.group(2) or today.year}",
                    "%B %d %Y",
                )
                return parsed.date()
            except ValueError:
                pass
    return _relative_date(lowered, today) or today


def _duration_from_text(text: str) -> int | None:
    match = re.search(r"\b(\d{2,3})\s*(?:-| )?(?:minute|min)\b", text.lower())
    return max(15, min(int(match.group(1)), 240)) if match else None


def _hour_duration_from_text(text: str) -> int | None:
    match = re.search(r"\b(?:an?|one|1)\s+hour\b", text.lower())
    return 60 if match else None


def _explicit_event_title(request: str) -> str | None:
    match = re.search(r"\b(?:called|named)\s+[\"']([^\"']+)[\"']", request, re.I)
    return match.group(1).strip() if match else None


def _study_title(request: str, intent: UserIntent) -> str:
    text = f"{request} {intent.goal}".lower()
    if "study" in text or "studying" in text:
        return "Study block"
    return "Focus block"


def _event_reason(
    context: dict[str, list[dict[str, Any]]],
    explicit_slot: dict[str, str] | None,
    slots: list[dict[str, Any]],
) -> str:
    if explicit_slot:
        events = (_latest_result(context, "calendar", "list_events") or {}).get("events", [])
        if _has_calendar_conflict(events, explicit_slot):
            return (
                "The requested interval was checked by Calendar MCP; any overlap will be "
                "reported by the guarded write after approval."
            )
        return "The requested interval was reviewed and no overlapping events were found."
    if slots:
        return "Calendar MCP returned this interval as free."
    return "Calendar MCP returned no conflicting events for the requested interval."


def _explicit_event_slot(request: str, timezone: ZoneInfo) -> dict[str, str] | None:
    lowered = request.lower()
    range_match = re.search(
        r"\b(?P<start>\d{1,2}(?::\d{2})?)\s*(?:-|–|to)\s*"
        r"(?P<end>\d{1,2}(?::\d{2})?)\s*(?P<meridiem>am|pm)\b",
        lowered,
        re.I,
    )
    if range_match:
        meridiem = range_match.group("meridiem")
        time_matches = [
            f"{range_match.group('start')} {meridiem}",
            f"{range_match.group('end')} {meridiem}",
        ]
    else:
        time_matches = re.findall(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", lowered, re.I)
    if not time_matches:
        return None
    now = datetime.now(timezone)
    date = _relative_date(lowered, now.date())
    calculation_date = date or now.date()
    try:
        start_time = _parse_clock(time_matches[0])
        if len(time_matches) > 1:
            end_time = _parse_clock(time_matches[1])
        else:
            duration = _duration_from_text(request) or _hour_duration_from_text(request)
            if duration is None:
                return None
            end_time = (
                datetime.combine(calculation_date, start_time, timezone)
                + timedelta(minutes=duration)
            ).timetz()
    except ValueError:
        return None
    if date is None:
        date = calculation_date
        if "tonight" in lowered and start_time <= now.time():
            date += timedelta(days=1)
    start = datetime.combine(date, start_time, timezone)
    end = datetime.combine(date, end_time, timezone)
    if end <= start:
        return None
    return {"start": start.isoformat(), "end": end.isoformat()}


def _has_calendar_conflict(events: list[dict[str, Any]], slot: dict[str, str]) -> bool:
    start = datetime.fromisoformat(slot["start"])
    end = datetime.fromisoformat(slot["end"])
    return any(
        datetime.fromisoformat(event["start_at"]) < end
        and datetime.fromisoformat(event["end_at"]) > start
        for event in events
    )


def _relative_date(text: str, today) -> Any:
    if "tomorrow" in text:
        return today + timedelta(days=1)
    if "today" in text:
        return today
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for name, weekday in weekdays.items():
        if name in text:
            offset = (weekday - today.weekday()) % 7 or 7
            return today + timedelta(days=offset)
    return None


def _parse_clock(value: str) -> time:
    normalized = re.sub(r"\s+", " ", value.strip().upper())
    normalized = re.sub(r"(?<=\d)(AM|PM)$", r" \1", normalized)
    for format_string in ("%I:%M %p", "%I %p"):
        try:
            return datetime.strptime(normalized, format_string).time()
        except ValueError:
            continue
    raise ValueError(f"Unsupported clock value: {value}")
