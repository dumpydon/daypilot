from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_TIMING_QUESTION = re.compile(r"\b(?:when|what\s+time|which\s+date|what\s+date)\b", re.I)
_DATE_TIME = re.compile(
    r"\b(?P<date>(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
    r"(?:(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2}(?:,\s*\d{4})?|today|tomorrow|tonight))"
    r"\s+at\s+(?P<time>\d{1,2}(?::\d{2})?\s*(?:AM|PM)(?:\s+[A-Z]{2,5})?)\b",
    re.I,
)
_TIME = re.compile(
    r"\b(?P<time>\d{1,2}(?::\d{2})?\s*(?:AM|PM)(?:\s+[A-Z]{2,5})?)\b",
    re.I,
)


def summarize_read_only(
    request: str,
    context: dict[str, list[dict[str, Any]]],
    errors: list[str],
) -> str:
    parts: list[str] = []
    mail = _result(context, "mail", "get_thread") or _result(context, "mail", "search_mail")
    if mail is not None:
        if mail.get("messages"):
            subject = mail.get("subject", "matching thread")
            if _TIMING_QUESTION.search(request):
                timing = _grounded_timing(request, mail.get("messages", []))
                if timing:
                    topic = "interview" if "interview" in request.lower() else "requested event"
                    parts.append(
                        f"Your {topic} is {timing}, according to the “{subject}” email thread."
                    )
                elif "interview" in request.lower():
                    parts.append(
                        "I found the interview email, but I couldn't determine the interview "
                        "time from the available messages."
                    )
                else:
                    parts.append(
                        f"I found the “{subject}” email thread, but I couldn't determine the "
                        "requested timing from the available messages."
                    )
            else:
                parts.append(f"Found the mail thread “{subject}”.")
        elif mail.get("threads"):
            parts.append(f"Found {len(mail['threads'])} matching mail thread(s).")
        else:
            parts.append("I couldn't find a matching email.")

    calendar = _result(context, "calendar", "list_events")
    if calendar is not None:
        events = calendar.get("events", [])
        if events:
            descriptions = []
            for event in events:
                event_time = datetime.fromisoformat(event["start_at"]).strftime("%-I:%M %p")
                descriptions.append(f"{event['title']} at {event_time}")
            parts.append("Calendar: " + "; ".join(descriptions) + ".")
        else:
            parts.append("No calendar events were found in the requested range.")

    free_slots = _result(context, "calendar", "find_free_slots")
    if free_slots is not None:
        slots = free_slots.get("slots", [])
        if slots:
            first = slots[0]
            start = datetime.fromisoformat(first["start"]).strftime("%-I:%M %p")
            end = datetime.fromisoformat(first["end"]).strftime("%-I:%M %p")
            parts.append(f"The first suitable free slot is {start}–{end}.")
        else:
            parts.append("No suitable free calendar slot was found.")

    tasks = _result(context, "tasks", "list_tasks")
    if tasks is not None:
        open_count = sum(not task.get("completed") for task in tasks.get("tasks", []))
        parts.append(f"Reviewed {open_count} open task(s).")

    files = _result(context, "files", "read_file") or _result(context, "files", "search_files")
    if files is not None:
        if files.get("content") and files.get("filename"):
            parts.append(
                f"Workspace file “{files['filename']}” says: {_first_file_fact(files['content'])}"
            )
        elif files.get("files"):
            names = ", ".join(file.get("filename", "unnamed file") for file in files["files"][:3])
            parts.append(f"Found these matching workspace files: {names}.")
        else:
            parts.append("I couldn't find a matching workspace file.")

    x_posts = (
        _result(context, "x", "search_posts")
        or _result(context, "x", "get_user_posts")
        or _result(context, "x", "get_post")
    )
    if x_posts is not None:
        posts = x_posts.get("posts") if isinstance(x_posts, dict) else None
        if isinstance(posts, list) and posts:
            excerpts = [str(post.get("text", "")).strip() for post in posts[:3] if post.get("text")]
            if excerpts:
                parts.append("Matching demo X posts: " + " ".join(excerpts))
            else:
                parts.append("Matching demo X posts did not contain readable text.")
        elif x_posts.get("text"):
            parts.append(f"The matching demo X post says: {x_posts['text']}")
        else:
            parts.append("I couldn't find grounded public X posts for that request.")

    if "files" in context and not files and any("file" in error.lower() for error in errors):
        parts.append("I couldn't find a grounded workspace file for that request.")
    if "x" in context and not x_posts and any(
        "post" in error.lower() or "x" in error.lower() for error in errors
    ):
        parts.append("I couldn't find grounded public X information for that request.")

    if errors:
        parts.append(f"{len(errors)} tool call(s) failed; details are preserved in the timeline.")
    return " ".join(parts) or f"No connected-service facts were found for: {request}"


def summarize_execution(
    results: list[dict[str, Any]],
    verifications: list[dict[str, Any]],
) -> str:
    completed: list[str] = []
    failed: list[str] = []
    verification_by_action = {item["action_id"]: item for item in verifications}
    for result in results:
        if not result.get("success"):
            failed.append(f"{result['tool_name']}: {result.get('error', 'failed')}")
            continue
        payload = result.get("result") or {}
        tool_name = result["tool_name"]
        verified = verification_by_action.get(result["action_id"], {}).get("verified", False)
        suffix = " (verified)" if verified else ""
        if tool_name == "create_event":
            start = datetime.fromisoformat(payload["start_at"]).strftime("%-I:%M %p")
            end = datetime.fromisoformat(payload["end_at"]).strftime("%-I:%M %p")
            completed.append(f"Preparation block scheduled for {start}–{end}{suffix}.")
        elif tool_name == "create_task_batch":
            completed.append(f"{payload.get('count', 0)} preparation tasks were created{suffix}.")
        elif tool_name == "create_task":
            completed.append(f"Task “{payload.get('title')}” was created{suffix}.")
        elif tool_name == "complete_task":
            completed.append(f"Task “{payload.get('title')}” was completed{suffix}.")
        elif tool_name == "create_draft":
            completed.append(
                f"A follow-up email draft to {payload.get('recipient')} was saved{suffix}."
            )
        elif tool_name == "create_post_draft":
            completed.append(f"An X post draft was saved for review{suffix}.")
        elif tool_name == "publish_post":
            completed.append(f"An X post was published in the demo workspace{suffix}.")
    if failed:
        completed.append("Failed actions: " + "; ".join(failed) + ".")
    return " ".join(completed) or "No write actions were executed."


def _result(
    context: dict[str, list[dict[str, Any]]],
    server: str,
    tool_name: str,
) -> dict[str, Any] | None:
    for record in reversed(context.get(server, [])):
        if record.get("tool_name") == tool_name and record.get("success"):
            return record.get("result")
    return None


def _grounded_timing(request: str, messages: list[dict[str, Any]]) -> str | None:
    request_terms = {
        word
        for word in re.findall(r"[a-z]{4,}", request.lower())
        if word not in {"find", "from", "most", "recent", "tell", "when", "what", "time"}
    }
    candidates: list[tuple[int, int, str]] = []
    for message_index, message in enumerate(messages):
        body = str(message.get("body", ""))
        for line in (line.strip() for line in body.splitlines() if line.strip()):
            match = _DATE_TIME.search(line) or _TIME.search(line)
            if match is None:
                continue
            overlap = len(request_terms.intersection(re.findall(r"[a-z]{4,}", line.lower())))
            candidates.append((overlap, -message_index, match.group(0)))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def _first_file_fact(content: Any) -> str:
    lines = [
        line.strip()
        for line in str(content).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    text = " ".join(lines)
    return text[:320].rstrip(" .") + ("…" if len(text) > 320 else ".")
