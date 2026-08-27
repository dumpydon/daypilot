from __future__ import annotations

import pytest

from backend.app.domain.errors import UnauthorizedToolCallError
from backend.app.domain.models import PlanAction
from backend.app.mcp.policy import (
    WriteAuthorization,
    enforce_tool_policy,
    get_policy,
    plan_hash,
)


def write_action() -> PlanAction:
    return PlanAction(
        id="write-1",
        description="Create event",
        server_name="calendar",
        tool_name="create_event",
        arguments={"title": "Prep", "start": "a", "end": "b"},
        reason="Approved slot",
        side_effecting=True,
    )


def test_read_tool_needs_no_authorization() -> None:
    enforce_tool_policy("list_events", {"start": "a", "end": "b"}, get_policy("list_events"), None)


def test_write_tool_is_blocked_without_human_authorization() -> None:
    with pytest.raises(UnauthorizedToolCallError, match="no human approval"):
        enforce_tool_policy("create_event", {}, get_policy("create_event"), None)


def test_authorization_is_bound_to_exact_action_arguments() -> None:
    action = write_action()
    authorization = WriteAuthorization(
        run_id="run-1",
        action_id=action.id,
        tool_name=action.tool_name,
        arguments=action.arguments,
        approved_plan_hash=plan_hash([action]),
        approved_actions=(action,),
    )
    enforce_tool_policy(
        action.tool_name,
        action.arguments,
        get_policy(action.tool_name),
        authorization,
    )
    with pytest.raises(UnauthorizedToolCallError, match="does not match"):
        enforce_tool_policy(
            action.tool_name,
            {**action.arguments, "title": "Unapproved title"},
            get_policy(action.tool_name),
            authorization,
        )


def test_unknown_discovered_tools_fail_closed_as_side_effects() -> None:
    assert get_policy("new_unclassified_tool").side_effecting is True


def test_new_read_capabilities_are_safe_and_x_writes_are_gated() -> None:
    read_tools = (
        "search_files",
        "list_files",
        "get_file_metadata",
        "read_file",
        "search_posts",
        "get_post",
        "get_user_posts",
    )
    for tool_name in read_tools:
        assert get_policy(tool_name).side_effecting is False
    for tool_name in ("create_post_draft", "publish_post"):
        assert get_policy(tool_name).side_effecting is True
        with pytest.raises(UnauthorizedToolCallError):
            enforce_tool_policy(tool_name, {}, get_policy(tool_name), None)
