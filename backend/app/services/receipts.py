from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from backend.app.domain.models import (
    ReceiptStatus,
    ResourceReceipt,
    ResourceReceiptDetail,
    ResourceReceiptItem,
)

_TOOL_RECEIPT_META: dict[str, tuple[str, str, str]] = {
    "create_event": ("calendar_event", "Calendar", "Calendar event"),
    "create_task": ("task", "Tasks", "Task"),
    "create_task_batch": ("task_batch", "Tasks", "Task batch"),
    "complete_task": ("task", "Tasks", "Task"),
    "create_draft": ("mail_draft", "Mail", "Mail draft"),
    "create_post_draft": ("x_post_draft", "X", "Post draft"),
    "publish_post": ("x_post", "X", "Post"),
}


def build_resource_receipts(
    results: list[dict[str, Any]],
    verifications: list[dict[str, Any]],
) -> list[ResourceReceipt]:
    """Project persisted write results into small, provider-grounded UI receipts."""

    verification_by_action = {
        str(item.get("action_id")): item for item in verifications if item.get("action_id")
    }
    receipts: list[ResourceReceipt] = []
    for result in results:
        tool_name = str(result.get("tool_name", ""))
        if tool_name not in _TOOL_RECEIPT_META:
            continue
        verification = verification_by_action.get(str(result.get("action_id", "")))
        receipts.append(_receipt_for_result(result, verification))
    return receipts


def _receipt_for_result(
    result: dict[str, Any],
    verification: dict[str, Any] | None,
) -> ResourceReceipt:
    tool_name = str(result.get("tool_name", ""))
    resource_type, base_provider, failure_label = _TOOL_RECEIPT_META[tool_name]
    payload = result.get("result")
    payload = payload if isinstance(payload, dict) else {}
    provider = _provider_name(base_provider, payload)
    success = bool(result.get("success"))
    verified = bool(verification and verification.get("verified") is True)
    status = _receipt_status(success, verification)
    verification_detail = _text(verification.get("detail")) if verification else None

    if not success:
        return ResourceReceipt(
            action_id=str(result.get("action_id", "")),
            resource_type=resource_type,
            provider=provider,
            title=f"{failure_label} not created",
            status=ReceiptStatus.FAILED,
            verification_detail=verification_detail,
            error=_text(result.get("error"), "The write failed before a resource was returned."),
        )

    common = {
        "action_id": str(result.get("action_id", "")),
        "resource_type": resource_type,
        "provider": provider,
        "status": status,
        "verified": verified,
        "verification_detail": verification_detail,
        "external_url": _external_url(payload),
    }
    if tool_name == "create_event":
        return _calendar_receipt(common, payload)
    if tool_name == "create_task_batch":
        return _task_batch_receipt(common, payload)
    if tool_name == "create_task":
        return _task_receipt(common, payload)
    if tool_name == "complete_task":
        return _completed_task_receipt(common, payload)
    if tool_name == "create_draft":
        return _mail_receipt(common, payload)
    return _post_receipt(common, payload, published=tool_name == "publish_post")


def _calendar_receipt(common: dict[str, Any], payload: dict[str, Any]) -> ResourceReceipt:
    title = _text(payload.get("title"), "Calendar event")
    when = _calendar_when(payload)
    details = [ResourceReceiptDetail(label="Title", value=title)]
    if when:
        details.append(ResourceReceiptDetail(label="When", value=when))
    description = _text(payload.get("description"))
    if description:
        details.append(ResourceReceiptDetail(label="Description", value=description))
    return ResourceReceipt(
        **common,
        resource_id=_text(payload.get("id")),
        title=title,
        secondary_text=when,
        details=details,
    )


def _task_batch_receipt(common: dict[str, Any], payload: dict[str, Any]) -> ResourceReceipt:
    raw_tasks = payload.get("tasks")
    tasks = raw_tasks if isinstance(raw_tasks, list) else []
    items = [_task_item(task) for task in tasks if isinstance(task, dict)]
    count = len(items)
    if not count and isinstance(payload.get("count"), int):
        count = max(0, int(payload["count"]))
    noun = "task" if count == 1 else "tasks"
    return ResourceReceipt(
        **common,
        title=f"Created {count} {noun}",
        items=items,
        details=[
            ResourceReceiptDetail(label="Count", value=str(count)),
            *[
                ResourceReceiptDetail(label="Task", value=item.title)
                for item in items
            ],
        ],
    )


def _task_receipt(common: dict[str, Any], payload: dict[str, Any]) -> ResourceReceipt:
    item = _task_item(payload)
    return ResourceReceipt(
        **common,
        resource_id=item.resource_id,
        title=item.title,
        secondary_text=item.secondary_text,
        items=[item],
        details=[ResourceReceiptDetail(label="Task", value=item.title)],
    )


def _completed_task_receipt(
    common: dict[str, Any],
    payload: dict[str, Any],
) -> ResourceReceipt:
    title = _text(payload.get("title"), "Task completed")
    return ResourceReceipt(
        **common,
        resource_id=_text(payload.get("id")),
        title=title,
        secondary_text="Task marked complete",
        details=[ResourceReceiptDetail(label="Task", value=title)],
    )


def _mail_receipt(common: dict[str, Any], payload: dict[str, Any]) -> ResourceReceipt:
    subject = _text(payload.get("subject"), "Untitled draft")
    recipient = _text(payload.get("recipient"))
    secondary = f'“{subject}”'
    if recipient:
        secondary += f" · To: {recipient}"
    details = [ResourceReceiptDetail(label="Subject", value=subject)]
    if recipient:
        details.append(ResourceReceiptDetail(label="Recipient", value=recipient))
    body = _text(payload.get("body"))
    if body:
        details.append(ResourceReceiptDetail(label="Body", value=body))
    return ResourceReceipt(
        **common,
        resource_id=_text(payload.get("id")),
        title="Draft created",
        secondary_text=secondary,
        details=details,
    )


def _post_receipt(
    common: dict[str, Any],
    payload: dict[str, Any],
    *,
    published: bool,
) -> ResourceReceipt:
    text = _text(payload.get("text"), "No post text returned")
    label = "Post published" if published else "Post draft created"
    return ResourceReceipt(
        **common,
        resource_id=_text(payload.get("id")),
        title=label,
        secondary_text=_preview(text),
        details=[ResourceReceiptDetail(label="Content", value=text)],
    )


def _task_item(payload: dict[str, Any]) -> ResourceReceiptItem:
    title = _text(payload.get("title"), "Untitled task")
    due_at = _text(payload.get("due_at"))
    secondary = f"Due {_format_date_only(due_at)}" if due_at else None
    return ResourceReceiptItem(
        resource_id=_text(payload.get("id")),
        title=title,
        secondary_text=secondary,
    )


def _receipt_status(
    success: bool,
    verification: dict[str, Any] | None,
) -> ReceiptStatus:
    if not success:
        return ReceiptStatus.FAILED
    if verification is None:
        return ReceiptStatus.CREATED
    if verification.get("verified") is True:
        return ReceiptStatus.VERIFIED
    return ReceiptStatus.FAILED


def _provider_name(base_provider: str, payload: dict[str, Any]) -> str:
    provider = _text(payload.get("provider"))
    source = _text(payload.get("source"))
    if provider:
        return provider
    if source and source.lower() not in {"daypilot", "demo"}:
        return source
    if source:
        return f"{base_provider} · DayPilot demo"
    return base_provider


def _external_url(payload: dict[str, Any]) -> str | None:
    for key in ("htmlLink", "external_url", "externalUrl", "url", "web_url", "link"):
        value = _text(payload.get(key))
        if value and value.startswith(("https://", "http://")):
            return value
    return None


def _calendar_when(payload: dict[str, Any]) -> str | None:
    start = _parse_datetime(payload.get("start_at") or payload.get("start"))
    end = _parse_datetime(payload.get("end_at") or payload.get("end"))
    if start is None or end is None:
        return None
    today = datetime.now(start.tzinfo).date() if start.tzinfo else datetime.now().date()
    if start.date() == today:
        day = "Today"
    elif start.date() == today + timedelta(days=1):
        day = "Tomorrow"
    else:
        day = f"{start.strftime('%a, %b')} {start.day}"
    return f"{day} · {_format_time(start)}–{_format_time(end)}"


def _format_date_only(value: str) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return value
    return f"{parsed.strftime('%a, %b')} {parsed.day}"


def _format_time(value: datetime) -> str:
    hour = value.hour % 12 or 12
    return f"{hour}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _preview(value: str, limit: int = 160) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _text(value: Any, fallback: str | None = None) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback
