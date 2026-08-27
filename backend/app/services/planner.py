from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from backend.app.domain.errors import InvalidPlanError
from backend.app.domain.models import (
    ActionStatus,
    PlanAction,
    PreferenceSet,
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
            (
                "calendar_create" in intent.requested_operations
                or "create_event" in intent.requested_outcomes
            )
            and "create_event" not in tool_names
        ):
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

        if (
            "create_checklist" in intent.requested_outcomes
            and "create_task_batch" in tool_names
            and not _feedback_removes(lowered_feedback, ("task", "checklist"))
        ):
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

        self._validate(actions, tools)
        return actions

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


def _interview_message(context: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    for record in reversed(context.get("mail", [])):
        result = record.get("result") or {}
        for message in result.get("messages", []):
            if "interview" in (message.get("subject", "") + message.get("body", "")).lower():
                return message
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
