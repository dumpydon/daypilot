from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from backend.app.config import Settings
from backend.app.domain.models import (
    AdminLoginRequest,
    AdminStatusResponse,
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
    ReadinessResponse,
    RunAccepted,
    RunDetail,
    RunHistoryClearResponse,
    RunRecord,
)
from backend.app.mcp.gateway import MCPGateway
from backend.app.persistence.repository import DayPilotRepository
from backend.app.services.access import PUBLIC_PERSONAL_MESSAGE, requires_personal_access
from backend.app.services.admin_auth import ADMIN_COOKIE_NAME, AdminAuthService
from backend.app.services.coordinator import TERMINAL_STATUSES, RunCoordinator

router = APIRouter()


def _services(request: Request) -> tuple[RunCoordinator, DayPilotRepository, MCPGateway, Settings]:
    return (
        request.app.state.coordinator,
        request.app.state.repository,
        request.app.state.gateway,
        request.app.state.settings,
    )


def _admin_service(request: Request) -> AdminAuthService | None:
    return getattr(request.app.state, "admin_auth", None)


def _public_mode(request: Request) -> bool:
    return bool(getattr(request.app.state.settings, "public_demo_mode", False))


async def _is_admin(request: Request) -> bool:
    service = _admin_service(request)
    if service is None:
        return not bool(getattr(request.app.state.settings, "public_demo_mode", False))
    return await service.authenticated(request.cookies.get(ADMIN_COOKIE_NAME))


async def _require_admin(request: Request) -> None:
    if not getattr(request.app.state.settings, "public_demo_mode", False):
        return
    if not await _is_admin(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PUBLIC_PERSONAL_MESSAGE)
    origin = request.headers.get("origin")
    allowed_origins = getattr(request.app.state.settings, "cors_origins", [])
    if origin and origin not in allowed_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Request origin is not allowed."
        )


async def _require_run_access(request: Request, run_id: str) -> bool:
    is_admin = await _is_admin(request)
    if not getattr(request.app.state.settings, "public_demo_mode", False) or is_admin:
        return is_admin
    _, repository, _, _ = _services(request)
    if await repository.is_run_admin_authorized(run_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PUBLIC_PERSONAL_MESSAGE)
    return False


@router.get("/health", response_model=HealthResponse)
async def health(request: Request, response: Response) -> HealthResponse:
    settings = request.app.state.settings
    database_state = getattr(request.app.state, "database_state", "initializing")
    graph_state = getattr(request.app.state, "graph_state", "initializing")
    core_unavailable = "unavailable" in {database_state, graph_state}
    if core_unavailable:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="degraded" if core_unavailable else "ok",
        database=database_state,
        graph=graph_state,
        demo_mode=settings.daypilot_demo_mode,
        reasoning_mode=settings.reasoning_mode,
        runtime_state=getattr(request.app.state, "runtime_state", "starting"),
    )


@router.get("/api/readiness", response_model=ReadinessResponse)
async def readiness(request: Request) -> ReadinessResponse:
    snapshot = getattr(request.app.state, "readiness", None)
    if snapshot is not None:
        return ReadinessResponse.model_validate(snapshot)
    gateway = getattr(request.app.state, "gateway", None)
    total = len(gateway.connections) if gateway is not None else 0
    return ReadinessResponse(
        state="starting",
        mcp_servers_ready=0,
        mcp_servers_total=total,
        message="DayPilot is waking up and connecting services.",
    )


@router.post("/api/admin/login", response_model=AdminStatusResponse)
async def admin_login(payload: AdminLoginRequest, request: Request) -> Response:
    service = _admin_service(request)
    if service is None or not getattr(request.app.state.settings, "admin_secret", None):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin access is not configured.",
        )
    client_key = request.client.host if request.client else "unknown"
    session = await service.authenticate(payload.access_code, client_key)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access code.")
    body = AdminStatusResponse(
        authenticated=True,
        public_demo_mode=_public_mode(request),
        expires_at=session.expires_at,
        message="Admin mode enabled. Personal services are available.",
    )
    response = JSONResponse(body.model_dump(mode="json"))
    response.set_cookie(value=session.token, **service.cookie_options())
    return response


@router.post("/api/admin/logout", response_model=AdminStatusResponse)
async def admin_logout(request: Request) -> Response:
    service = _admin_service(request)
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if service is not None:
        await service.revoke(token)
        body = AdminStatusResponse(
            authenticated=False,
            public_demo_mode=_public_mode(request),
            message="Admin mode locked.",
        )
        response = JSONResponse(body.model_dump(mode="json"))
        response.delete_cookie(ADMIN_COOKIE_NAME, path="/")
        return response
    return JSONResponse(
        AdminStatusResponse(
            authenticated=False,
            public_demo_mode=False,
            message="Admin mode locked.",
        ).model_dump(mode="json")
    )


@router.get("/api/admin/status", response_model=AdminStatusResponse)
async def admin_status(request: Request) -> AdminStatusResponse:
    authenticated = await _is_admin(request)
    service = _admin_service(request)
    return AdminStatusResponse(
        authenticated=authenticated,
        public_demo_mode=_public_mode(request),
        expires_at=(await service.expiry(request.cookies.get(ADMIN_COOKIE_NAME)))
        if service
        else None,
        message=(
            "Admin mode enabled. Personal services are available."
            if authenticated
            else "Public demo mode. Personal services are disabled."
        ),
    )


@router.post("/api/runs", response_model=RunAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_run(payload: CreateRunRequest, request: Request) -> RunAccepted:
    coordinator, _, _, _ = _services(request)
    if getattr(request.app.state, "runtime_state", "starting") == "starting":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DayPilot is still waking up and connecting services. Try again shortly.",
        )
    admin_authorized = await _is_admin(request)
    if (
        _public_mode(request)
        and not request.app.state.settings.daypilot_demo_mode
        and not admin_authorized
        and requires_personal_access(payload.request)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PUBLIC_PERSONAL_MESSAGE)
    if _public_mode(request) and admin_authorized:
        await _require_admin(request)
    return await coordinator.start_run(payload.request, admin_authorized=admin_authorized)


@router.post("/api/demo-workspace/reset", response_model=DemoResetResponse)
async def reset_demo_workspace(request: Request) -> DemoResetResponse:
    await _require_admin(request)
    return await request.app.state.demo_workspace.reset_demo_workspace()


@router.get("/api/connections", response_model=ConnectionCatalog)
async def list_connections(request: Request) -> ConnectionCatalog:
    if _public_mode(request) and not await _is_admin(request):
        return request.app.state.connections.public_catalog()
    return request.app.state.connections.catalog()


@router.post("/api/connections/google/start", response_model=OAuthStartResponse)
async def start_google_connection(request: Request) -> OAuthStartResponse:
    await _require_admin(request)
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
        await _require_admin(request)
        await request.app.state.connections.complete_google(code, state, error)
        return RedirectResponse(f"{settings.site_url}/?connection=google_connected")
    except Exception as exc:
        return RedirectResponse(
            f"{settings.site_url}/?connection=google_error&message={_quote_error(exc)}"
        )


@router.post("/api/connections/google/disconnect", response_model=ConnectionCatalog)
async def disconnect_google(request: Request) -> ConnectionCatalog:
    await _require_admin(request)
    await request.app.state.connections.disconnect_google()
    return request.app.state.connections.catalog()


@router.post("/api/connections/x/start", response_model=OAuthStartResponse)
async def start_x_connection(request: Request) -> OAuthStartResponse:
    await _require_admin(request)
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
        await _require_admin(request)
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
        await _require_admin(request)
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
    await _require_admin(request)
    await request.app.state.connections.disconnect_x()
    return request.app.state.connections.catalog()


@router.get("/api/connections/files/roots", response_model=list[FileRoot])
async def list_file_roots(request: Request) -> list[FileRoot]:
    if _public_mode(request) and not await _is_admin(request):
        return []
    return await request.app.state.connections.list_file_roots()


@router.post("/api/connections/files/roots", response_model=FileRoot)
async def add_file_root(payload: FileRootRequest, request: Request) -> FileRoot:
    await _require_admin(request)
    return await request.app.state.connections.add_file_root(payload.path)


@router.delete("/api/connections/files/roots/{root_id}", status_code=204)
async def remove_file_root(root_id: str, request: Request) -> None:
    await _require_admin(request)
    await request.app.state.connections.remove_file_root(root_id)


@router.post("/api/run-history/clear", response_model=RunHistoryClearResponse)
async def clear_run_history(request: Request) -> RunHistoryClearResponse:
    await _require_admin(request)
    return await request.app.state.demo_workspace.clear_run_history()


@router.get("/api/runs", response_model=list[RunRecord])
async def list_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[RunRecord]:
    _, repository, _, _ = _services(request)
    if _public_mode(request) and not await _is_admin(request):
        return await repository.list_public_runs(limit)
    return await repository.list_runs(limit)


@router.get("/api/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, request: Request) -> RunDetail:
    coordinator, _, _, _ = _services(request)
    await _require_run_access(request, run_id)
    return await coordinator.get_detail(run_id)


@router.post("/api/runs/{run_id}/approve", response_model=RunAccepted)
async def approve_run(
    run_id: str,
    payload: DecisionRequest,
    request: Request,
) -> RunAccepted:
    coordinator, _, _, _ = _services(request)
    await _require_admin(request)
    return await coordinator.resume(run_id, "approve", payload.feedback)


@router.post("/api/runs/{run_id}/reject", response_model=RunAccepted)
async def reject_run(
    run_id: str,
    payload: DecisionRequest,
    request: Request,
) -> RunAccepted:
    coordinator, _, _, _ = _services(request)
    await _require_admin(request)
    return await coordinator.resume(run_id, "reject", payload.feedback)


@router.post("/api/runs/{run_id}/feedback", response_model=RunDetail)
async def edit_plan(
    run_id: str,
    payload: FeedbackRequest,
    request: Request,
) -> RunDetail:
    coordinator, _, _, _ = _services(request)
    await _require_admin(request)
    return await coordinator.revise(run_id, payload.feedback, payload.plan_revision)


@router.get("/api/tools")
async def list_tools(request: Request) -> dict[str, Any]:
    _, _, gateway, _ = _services(request)
    admin_authorized = await _is_admin(request)
    if getattr(request.app.state, "runtime_state", "starting") == "starting":
        return {
            "servers": gateway.catalog(admin_authorized=admin_authorized),
            "tools": [],
        }
    tools = await gateway.discover(
        force=not bool(gateway.catalog(admin_authorized=admin_authorized)),
        admin_authorized=admin_authorized,
    )
    return {
        "servers": gateway.catalog(admin_authorized=admin_authorized),
        "tools": [tool.model_dump(mode="json") for tool in tools],
    }


@router.get("/api/preferences", response_model=PreferenceSet)
async def get_preferences(request: Request) -> PreferenceSet:
    _, repository, _, _ = _services(request)
    if _public_mode(request) and not await _is_admin(request):
        return PreferenceSet()
    return await repository.get_preferences()


@router.put("/api/preferences", response_model=PreferenceSet)
async def update_preferences(
    preferences: PreferenceSet,
    request: Request,
) -> PreferenceSet:
    _, repository, _, _ = _services(request)
    await _require_admin(request)
    return await repository.update_preferences(preferences)


def _quote_error(error: Exception) -> str:
    from urllib.parse import quote

    # OAuth/provider exceptions can contain identifiers or upstream response
    # details. Keep those server-side and return one stable product message.
    return quote("The connection could not be completed. Try again.", safe="")


@router.get("/api/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    after: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    _, repository, _, _ = _services(request)
    await _require_run_access(request, run_id)
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
