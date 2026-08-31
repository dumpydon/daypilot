from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from backend.app.config import Settings
from backend.app.providers.factory import build_dynamic_service

mcp = FastMCP(
    "DayPilot Files",
    instructions=(
        "Read-only Files capability selected by DayPilot configuration. "
        "File IDs are service-owned references; "
        "the server never exposes arbitrary host filesystem paths."
    ),
    log_level="ERROR",
    json_response=True,
)
store = build_dynamic_service("files", Settings())
read_annotations = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


@mcp.tool(annotations=read_annotations)
def search_files(query: str, limit: int = 10) -> dict[str, Any]:
    """Search controlled workspace documents and return grounded file metadata."""
    return store.search_files(query, limit)


@mcp.tool(annotations=read_annotations)
def list_files(query: str | None = None, limit: int = 25) -> dict[str, Any]:
    """List controlled workspace documents, optionally filtered by a query."""
    return store.list_files(query, limit)


@mcp.tool(annotations=read_annotations)
def get_file_metadata(file_id: str) -> dict[str, Any]:
    """Read metadata for one controlled workspace file ID."""
    return store.get_file_metadata(file_id)


@mcp.tool(annotations=read_annotations)
def read_file(file_id: str) -> dict[str, Any]:
    """Read the content of one controlled workspace file ID."""
    return store.read_file(file_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
