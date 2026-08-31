from __future__ import annotations

from backend.app.services.receipts import build_resource_receipts


def calendar_result(*, external_url: str | None = None) -> dict:
    payload = {
        "id": "event-demo-123",
        "title": "Study block",
        "start_at": "2099-08-30T20:00:00+05:30",
        "end_at": "2099-08-30T21:30:00+05:30",
        "description": "Review systems design notes.",
        "source": "daypilot",
        "status": "created",
    }
    if external_url:
        payload["htmlLink"] = external_url
    return {
        "action_id": "calendar-1",
        "tool_name": "create_event",
        "result": payload,
        "success": True,
        "error": None,
    }


def test_calendar_receipt_uses_grounded_result_and_verification() -> None:
    receipt = build_resource_receipts(
        [calendar_result()],
        [{"action_id": "calendar-1", "verified": True, "detail": "Read-back confirmed."}],
    )[0]

    assert receipt.resource_type == "calendar_event"
    assert receipt.provider == "Calendar · DayPilot demo"
    assert receipt.resource_id == "event-demo-123"
    assert receipt.title == "Study block"
    assert receipt.secondary_text == "Sun, Aug 30 · 8:00 PM–9:30 PM"
    assert receipt.status == "verified"
    assert receipt.verified is True
    assert receipt.external_url is None
    assert {detail.label for detail in receipt.details} == {"Title", "When", "Description"}


def test_provider_url_is_used_only_when_the_result_supplies_one() -> None:
    receipt = build_resource_receipts(
        [calendar_result(external_url="https://calendar.google.com/event/abc")],
        [{"action_id": "calendar-1", "verified": True}],
    )[0]

    assert receipt.external_url == "https://calendar.google.com/event/abc"


def test_failed_verification_is_presented_as_created_but_not_verified() -> None:
    receipt = build_resource_receipts(
        [calendar_result()],
        [{"action_id": "calendar-1", "verified": False, "detail": "Read-back failed."}],
    )[0]

    assert receipt.status == "created"
    assert receipt.verified is False
    assert receipt.verification_detail == "Read-back failed."


def test_failed_and_multiple_writes_remain_distinct() -> None:
    results = [
        calendar_result(),
        {
            "action_id": "tasks-1",
            "tool_name": "create_task_batch",
            "result": None,
            "success": False,
            "error": "Task service unavailable",
        },
        {
            "action_id": "mail-1",
            "tool_name": "create_draft",
            "result": {
                "id": "draft-1",
                "recipient": "person@example.com",
                "subject": "Follow-up after interview",
                "body": "Thanks for the conversation.",
            },
            "success": True,
            "error": None,
        },
    ]
    receipts = build_resource_receipts(
        results,
        [{"action_id": "calendar-1", "verified": True}],
    )

    assert len(receipts) == 3
    assert receipts[0].status == "verified"
    assert receipts[1].status == "failed"
    assert receipts[1].verified is False
    assert receipts[1].error == "Task service unavailable"
    assert receipts[2].title == "Draft created"
    assert receipts[2].secondary_text == "“Follow-up after interview” · To: person@example.com"
    assert receipts[2].status == "created"


def test_task_batch_items_and_receipt_are_grounded_without_summary_prose() -> None:
    receipts = build_resource_receipts(
        [
            {
                "action_id": "tasks-1",
                "tool_name": "create_task_batch",
                "result": {
                    "tasks": [
                        {"id": "task-1", "title": "Review notes", "due_at": None},
                        {"id": "task-2", "title": "Prepare questions", "due_at": None},
                    ],
                    "count": 2,
                    "status": "created",
                },
                "success": True,
                "error": None,
            }
        ],
        [{"action_id": "tasks-1", "verified": True}],
    )

    receipt = receipts[0]
    assert receipt.title == "Created 2 tasks"
    assert [item.title for item in receipt.items] == ["Review notes", "Prepare questions"]
    assert [item.resource_id for item in receipt.items] == ["task-1", "task-2"]


def test_partial_task_batch_is_not_presented_as_fully_verified() -> None:
    receipt = build_resource_receipts(
        [
            {
                "action_id": "tasks-partial",
                "tool_name": "create_task_batch",
                "result": {
                    "tasks": [{"id": "task-1", "title": "Created task", "due_at": None}],
                    "failed": [{"title": "Unavailable task", "error": "quota"}],
                    "count": 1,
                    "status": "partially_created",
                },
                "success": True,
                "error": None,
            }
        ],
        [{"action_id": "tasks-partial", "verified": False}],
    )[0]

    assert receipt.status == "partially_completed"
    assert receipt.verified is False


def test_unavailable_verification_stays_created_not_verified() -> None:
    receipt = build_resource_receipts(
        [calendar_result()],
        [
            {
                "action_id": "calendar-1",
                "verified": False,
                "detail": "Verification read failed: timeout",
            }
        ],
    )[0]

    assert receipt.status == "created"
    assert receipt.verified is False
