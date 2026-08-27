from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from mcp_servers.common.database import database_path_from_env
from mcp_servers.common.store import DemoServiceStore

mcp = FastMCP(
    "DayPilot Calendar",
    instructions=(
        "Fictional local calendar with timezone-aware event reads and guarded event creation."
    ),
    log_level="ERROR",
    json_response=True,
)
store = DemoServiceStore(
    database_path_from_env(),
    os.getenv("DAYPILOT_TIMEZONE", "Asia/Kolkata"),
)
read_annotations = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
write_annotations = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


@mcp.tool(annotations=read_annotations)
def list_events(start: str, end: str) -> dict[str, Any]:
    """List calendar events overlapping an ISO datetime range. This is read-only."""
    return store.list_events(start, end)


@mcp.tool(annotations=read_annotations)
def find_free_slots(start: str, end: str, duration_minutes: int) -> dict[str, Any]:
    """Find free slots of a requested duration inside an ISO datetime range. This is read-only."""
    return store.find_free_slots(start, end, duration_minutes)


@mcp.tool(annotations=write_annotations)
def create_event(
    title: str,
    start: str,
    end: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a non-conflicting calendar event. This modifies calendar state."""
    return store.create_event(title, start, end, description)


if __name__ == "__main__":
    mcp.run(transport="stdio")
