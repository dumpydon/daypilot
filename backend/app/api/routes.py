from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import RedirectResponse, StreamingResponse

from backend.app.config import Settings
from backend.app.domain.models import (
    ConnectionCatalog,
    CreateRunRequest,
    DecisionRequest,
    DemoResetResponse,
    FeedbackRequest,
    FileRoot,
    FileRootRequest,
    HealthResponse,
    OAuthStartResponse,
    PreferenceSet,
    RunAccepted,
    RunDetail,
    RunHistoryClearResponse,
    RunRecord,
)
from backend.app.mcp.gateway import MCPGateway
from backend.app.persistence.repository import DayPilotRepository
from backend.app.services.coordinator import TERMINAL_STATUSES, RunCoordinator

router = APIRouter()


def _services(request: Request) -> tuple[RunCoordinator, DayPilotRepository, MCPGateway, Settings]:
    return (
        request.app.state.coordinator,
        request.app.state.repository,
        request.app.state.gateway,
        request.app.state.settings,
    )


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    _, _, _, settings = _services(request)
    return HealthResponse(
        demo_mode=settings.daypilot_demo_mode,
        reasoning_mode=settings.reasoning_mode,
    )


@router.post("/api/runs", response_model=RunAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_run(payload: CreateRunRequest, request: Request) -> RunAccepted:
    coordinator, _, _, _ = _services(request)
    return await coordinator.start_run(payload.request)


@router.post("/api/demo-workspace/reset", response_model=DemoResetResponse)
async def reset_demo_workspace(request: Request) -> DemoResetResponse:
    return await request.app.state.demo_workspace.reset_demo_workspace()


@router.get("/api/connections", response_model=ConnectionCatalog)
async def list_connections(request: Request) -> ConnectionCatalog:
    return request.app.state.connections.catalog()


@router.post("/api/connections/google/start", response_model=OAuthStartResponse)
async def start_google_connection(request: Request) -> OAuthStartResponse:
    return request.app.state.connections.start_google()


@router.get("/api/connections/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    settings = request.app.state.settings
    try:
        await request.app.state.connections.complete_google(code, state, error)
        return RedirectResponse(f"{settings.site_url}/?connection=google_connected")
    except Exception as exc:
        return RedirectResponse(
            f"{settings.site_url}/?connection=google_error&message={_quote_error(exc)}"
        )


@router.post("/api/connections/google/disconnect", response_model=ConnectionCatalog)
async def disconnect_google(request: Request) -> ConnectionCatalog:
    await request.app.state.connections.disconnect_google()
    return request.app.state.connections.catalog()


@router.post("/api/connections/x/start", response_model=OAuthStartResponse)
async def start_x_connection(request: Request) -> OAuthStartResponse:
    return request.app.state.connections.start_x()


@router.get("/api/connections/x/callback")
async def x_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    settings = request.app.state.settings
    try:
        await request.app.state.connections.complete_x(code, state, error)
        return RedirectResponse(f"{settings.site_url}/?connection=x_connected")
    except Exception as exc:
        return RedirectResponse(
            f"{settings.site_url}/?connection=x_error&message={_quote_error(exc)}"
        )


@router.get("/api/connections/managed/callback")
async def managed_connection_callback(
    request: Request,
    provider: str | None = None,
    status: str | None = None,
    connected_account_id: str | None = None,
    connectedAccountId: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    settings = request.app.state.settings
    provider_name = provider or ""
    account_id = connected_account_id or connectedAccountId
    try:
        await request.app.state.connections.complete_managed(
            provider_name,
            status,
            account_id,
            error,
        )
        return RedirectResponse(f"{settings.site_url}/?connection={provider_name}_connected")
    except Exception as exc:
        return RedirectResponse(
            f"{settings.site_url}/?connection={provider_name or 'managed'}_error"
            f"&message={_quote_error(exc)}"
        )


@router.post("/api/connections/x/disconnect", response_model=ConnectionCatalog)
async def disconnect_x(request: Request) -> ConnectionCatalog:
    await request.app.state.connections.disconnect_x()
    return request.app.state.connections.catalog()


@router.get("/api/connections/files/roots", response_model=list[FileRoot])
async def list_file_roots(request: Request) -> list[FileRoot]:
    return await request.app.state.connections.list_file_roots()


@router.post("/api/connections/files/roots", response_model=FileRoot)
async def add_file_root(payload: FileRootRequest, request: Request) -> FileRoot:
    return await request.app.state.connections.add_file_root(payload.path)


@router.delete("/api/connections/files/roots/{root_id}", status_code=204)
async def remove_file_root(root_id: str, request: Request) -> None:
    await request.app.state.connections.remove_file_root(root_id)


@router.post("/api/run-history/clear", response_model=RunHistoryClearResponse)
async def clear_run_history(request: Request) -> RunHistoryClearResponse:
    return await request.app.state.demo_workspace.clear_run_history()


@router.get("/api/runs", response_model=list[RunRecord])
async def list_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[RunRecord]:
    _, repository, _, _ = _services(request)
    return await repository.list_runs(limit)


@router.get("/api/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, request: Request) -> RunDetail:
    coordinator, _, _, _ = _services(request)
    return await coordinator.get_detail(run_id)


@router.post("/api/runs/{run_id}/approve", response_model=RunAccepted)
async def approve_run(
    run_id: str,
    payload: DecisionRequest,
    request: Request,
) -> RunAccepted:
    coordinator, _, _, _ = _services(request)
    return await coordinator.resume(run_id, "approve", payload.feedback)


@router.post("/api/runs/{run_id}/reject", response_model=RunAccepted)
async def reject_run(
    run_id: str,
    payload: DecisionRequest,
    request: Request,
) -> RunAccepted:
    coordinator, _, _, _ = _services(request)
    return await coordinator.resume(run_id, "reject", payload.feedback)


@router.post("/api/runs/{run_id}/feedback", response_model=RunDetail)
async def edit_plan(
    run_id: str,
    payload: FeedbackRequest,
    request: Request,
) -> RunDetail:
    coordinator, _, _, _ = _services(request)
    return await coordinator.revise(run_id, payload.feedback, payload.plan_revision)


@router.get("/api/tools")
async def list_tools(request: Request) -> dict[str, Any]:
    _, _, gateway, _ = _services(request)
    tools = await gateway.discover(force=not bool(gateway.catalog()))
    return {
        "servers": gateway.catalog(),
        "tools": [tool.model_dump(mode="json") for tool in tools],
    }


@router.get("/api/preferences", response_model=PreferenceSet)
async def get_preferences(request: Request) -> PreferenceSet:
    _, repository, _, _ = _services(request)
    return await repository.get_preferences()


@router.put("/api/preferences", response_model=PreferenceSet)
async def update_preferences(
    preferences: PreferenceSet,
    request: Request,
) -> PreferenceSet:
    _, repository, _, _ = _services(request)
    return await repository.update_preferences(preferences)


def _quote_error(error: Exception) -> str:
    from urllib.parse import quote

    return quote(str(error)[:240], safe="")


@router.get("/api/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    after: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    _, repository, _, _ = _services(request)
    cursor = max(after, int(last_event_id or 0))

    async def event_stream():
        nonlocal cursor
        idle_ticks = 0
        while True:
            if await request.is_disconnected():
                break
            events = await repository.list_events(run_id, cursor)
            if events:
                idle_ticks = 0
                for event in events:
                    cursor = event.id or cursor
                    data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                    yield f"id: {cursor}\nevent: {event.event_type}\ndata: {data}\n\n"
            else:
                idle_ticks += 1
            run = await repository.get_run(run_id)
            if run.status in TERMINAL_STATUSES and not events:
                yield f"event: end\ndata: {json.dumps({'status': run.status.value})}\n\n"
                break
            if idle_ticks >= 20:
                yield ": keep-alive\n\n"
                idle_ticks = 0
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
