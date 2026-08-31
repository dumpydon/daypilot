from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from backend.app.config import Settings
from backend.app.providers.factory import build_dynamic_service

mcp = FastMCP(
    "DayPilot Calendar",
    instructions=(
        "Calendar capability selected by DayPilot configuration with timezone-aware reads "
        "and guarded event creation."
    ),
    log_level="ERROR",
    json_response=True,
)
store = build_dynamic_service("calendar", Settings())
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
