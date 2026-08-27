from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from mcp_servers.common.database import database_path_from_env
from mcp_servers.common.store import DemoServiceStore

mcp = FastMCP(
    "DayPilot Mail",
    instructions=(
        "Fictional local mailbox. Read tools expose grounded mail facts; "
        "create_draft saves but never sends."
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
def search_mail(query: str, limit: int = 10) -> dict[str, Any]:
    """Search the demo mailbox by person, subject, or body text. This is read-only."""
    return store.search_mail(query, limit)


@mcp.tool(annotations=read_annotations)
def get_thread(thread_id: str) -> dict[str, Any]:
    """Read a complete mail thread by its grounded thread ID. This is read-only."""
    return store.get_thread(thread_id)


@mcp.tool(annotations=read_annotations)
def get_message(message_id: str) -> dict[str, Any]:
    """Read a message or saved draft by its grounded ID. This is read-only."""
    return store.get_message(message_id)


@mcp.tool(annotations=write_annotations)
def create_draft(recipient: str, subject: str, body: str) -> dict[str, Any]:
    """Save an email draft without sending it. This modifies mailbox state."""
    return store.create_draft(recipient, subject, body)


if __name__ == "__main__":
    mcp.run(transport="stdio")
