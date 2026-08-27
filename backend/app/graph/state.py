from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class DayPilotState(TypedDict, total=False):
    run_id: str
    thread_id: str
    user_request: str
    intent: dict[str, Any]
    available_tools: list[dict[str, Any]]
    context: dict[str, list[dict[str, Any]]]
    plan: list[dict[str, Any]]
    read_actions: list[dict[str, Any]]
    write_actions: list[dict[str, Any]]
    approval_status: str
    approval_feedback: str | None
    approved_plan_hash: str | None
    plan_revision: int
    plan_hash: str | None
    execution_results: list[dict[str, Any]]
    verification_results: list[dict[str, Any]]
    errors: list[str]
    final_summary: str | None
    preferences: dict[str, Any]
    reasoning_mode: str
    created_at: str
    updated_at: str
