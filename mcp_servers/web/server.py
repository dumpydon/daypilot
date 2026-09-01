from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from backend.app.config import Settings
from backend.app.services.web_research import WebResearchService

mcp = FastMCP(
    "DayPilot Web Research",
    instructions=(
        "Read-only public web research for facts that require fresh external information. "
        "Returns normalized source titles, URLs, and snippets."
    ),
    log_level="ERROR",
    json_response=True,
)
research = WebResearchService(Settings())
read_annotations = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


@mcp.tool(annotations=read_annotations)
def search_web(query: str, limit: int = 5) -> dict[str, Any]:
    """Search the public web and return grounded source metadata. This is read-only."""
    return research.search_web(query, limit)


if __name__ == "__main__":
    mcp.run(transport="stdio")
