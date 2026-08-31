from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from backend.app.domain.errors import UnauthorizedToolCallError
from backend.app.domain.models import PlanAction, RiskLevel


@dataclass(frozen=True)
class ToolPolicy:
    server_name: str
    risk_level: RiskLevel

    @property
    def side_effecting(self) -> bool:
        return self.risk_level == RiskLevel.SIDE_EFFECT


TOOL_POLICIES: dict[str, ToolPolicy] = {
    "search_mail": ToolPolicy("mail", RiskLevel.SAFE_READ),
    "get_thread": ToolPolicy("mail", RiskLevel.SAFE_READ),
    "get_message": ToolPolicy("mail", RiskLevel.SAFE_READ),
    "create_draft": ToolPolicy("mail", RiskLevel.SIDE_EFFECT),
    "list_events": ToolPolicy("calendar", RiskLevel.SAFE_READ),
    "find_free_slots": ToolPolicy("calendar", RiskLevel.SAFE_READ),
    "create_event": ToolPolicy("calendar", RiskLevel.SIDE_EFFECT),
    "list_tasks": ToolPolicy("tasks", RiskLevel.SAFE_READ),
    "create_task": ToolPolicy("tasks", RiskLevel.SIDE_EFFECT),
    "create_task_batch": ToolPolicy("tasks", RiskLevel.SIDE_EFFECT),
    "complete_task": ToolPolicy("tasks", RiskLevel.SIDE_EFFECT),
    "search_files": ToolPolicy("files", RiskLevel.SAFE_READ),
    "list_files": ToolPolicy("files", RiskLevel.SAFE_READ),
    "get_file_metadata": ToolPolicy("files", RiskLevel.SAFE_READ),
    "read_file": ToolPolicy("files", RiskLevel.SAFE_READ),
    "search_posts": ToolPolicy("x", RiskLevel.SAFE_READ),
    "get_post": ToolPolicy("x", RiskLevel.SAFE_READ),
    "get_user_posts": ToolPolicy("x", RiskLevel.SAFE_READ),
    "create_post_draft": ToolPolicy("x", RiskLevel.SIDE_EFFECT),
    "publish_post": ToolPolicy("x", RiskLevel.SIDE_EFFECT),
}


@dataclass(frozen=True)
class WriteAuthorization:
    run_id: str
    action_id: str
    tool_name: str
    arguments: dict[str, Any]
    approved_plan_hash: str
    approved_actions: tuple[PlanAction, ...]


def get_policy(tool_name: str, server_name: str | None = None) -> ToolPolicy:
    policy = TOOL_POLICIES.get(tool_name)
    if policy is not None:
        return policy
    return ToolPolicy(server_name or "unknown", RiskLevel.SIDE_EFFECT)


def plan_hash(actions: list[PlanAction] | tuple[PlanAction, ...]) -> str:
    payload = []
    for action in actions:
        if not action.side_effecting:
            continue
        item = {
            "id": action.id,
            "tool_name": action.tool_name,
            "arguments": action.arguments,
        }
        # Preserve compatibility with pending plans created before dependency
        # metadata existed, while binding every meaningful dependency into new
        # approval hashes.
        if action.depends_on:
            item["depends_on"] = action.depends_on
        payload.append(item)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def enforce_tool_policy(
    tool_name: str,
    arguments: dict[str, Any],
    policy: ToolPolicy,
    authorization: WriteAuthorization | None,
) -> None:
    if not policy.side_effecting:
        return
    if authorization is None:
        raise UnauthorizedToolCallError(
            f"Write tool {tool_name!r} is blocked: no human approval is attached"
        )
    if authorization.tool_name != tool_name or authorization.arguments != arguments:
        raise UnauthorizedToolCallError(
            f"Write tool {tool_name!r} is blocked: call does not match the approved action"
        )
    approved_action = next(
        (
            action
            for action in authorization.approved_actions
            if action.id == authorization.action_id
        ),
        None,
    )
    if approved_action is None:
        raise UnauthorizedToolCallError(
            f"Write tool {tool_name!r} is blocked: action ID was not approved"
        )
    if (
        approved_action.tool_name != tool_name
        or approved_action.arguments != arguments
        or not approved_action.side_effecting
    ):
        raise UnauthorizedToolCallError(
            f"Write tool {tool_name!r} is blocked: approved action payload differs"
        )
    if plan_hash(authorization.approved_actions) != authorization.approved_plan_hash:
        raise UnauthorizedToolCallError(
            f"Write tool {tool_name!r} is blocked: approved plan integrity check failed"
        )
