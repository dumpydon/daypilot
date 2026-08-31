from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from backend.app.config import Settings
from backend.app.providers.factory import build_dynamic_service


class TaskInput(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    notes: str | None = Field(default=None, max_length=2_000)
    due_at: str | None = None


mcp = FastMCP(
    "DayPilot Tasks",
    instructions=(
        "Tasks capability selected by DayPilot configuration. Every mutation is designed "
        "for approval-gated clients."
    ),
    log_level="ERROR",
    json_response=True,
)
store = build_dynamic_service("tasks", Settings())
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
def list_tasks() -> dict[str, Any]:
    """List current tasks and grounded task IDs. This is read-only."""
    return store.list_tasks()


@mcp.tool(annotations=write_annotations)
def create_task(
    title: str,
    notes: str | None = None,
    due_at: str | None = None,
) -> dict[str, Any]:
    """Create one task. This modifies task state."""
    return store.create_task(title, notes, due_at)


@mcp.tool(annotations=write_annotations)
def create_task_batch(tasks: list[TaskInput]) -> dict[str, Any]:
    """Create a bounded batch of tasks. This modifies task state."""
    return store.create_task_batch([task.model_dump() for task in tasks])


@mcp.tool(annotations=write_annotations)
def complete_task(task_id: str) -> dict[str, Any]:
    """Mark a grounded task ID complete. This modifies task state."""
    return store.complete_task(task_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
