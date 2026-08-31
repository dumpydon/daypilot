from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from backend.app.config import Settings
from backend.app.providers.factory import build_dynamic_service

mcp = FastMCP(
    "DayPilot X",
    instructions=(
        "X capability selected by DayPilot configuration. Reads expose grounded public posts. "
        "Draft creation and publishing require human approval."
    ),
    log_level="ERROR",
    json_response=True,
)
store = build_dynamic_service("x", Settings())
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
def search_posts(query: str, limit: int = 10) -> dict[str, Any]:
    """Search public X posts by text or author."""
    return store.search_posts(query, limit)


@mcp.tool(annotations=read_annotations)
def get_post(post_id: str) -> dict[str, Any]:
    """Read one grounded public post or DayPilot draft by ID."""
    return store.get_post(post_id)


@mcp.tool(annotations=read_annotations)
def get_user_posts(username: str, limit: int = 10) -> dict[str, Any]:
    """Read public posts for one X username."""
    return store.get_user_posts(username, limit)


@mcp.tool(annotations=write_annotations)
def create_post_draft(text: str) -> dict[str, Any]:
    """Save a local X draft; this is a side effect and never publishes it."""
    return store.create_post_draft(text)


@mcp.tool(annotations=write_annotations)
def publish_post(text: str, draft_id: str | None = None) -> dict[str, Any]:
    """Publish a post to X after approval."""
    return store.publish_post(text, draft_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
