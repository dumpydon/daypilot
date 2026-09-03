from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
except ImportError:  # pragma: no cover - local installs may omit the Postgres extra.
    AsyncPostgresSaver = None  # type: ignore[assignment,misc]

from backend.app.api.routes import router
from backend.app.config import Settings, get_settings
from backend.app.domain.errors import (
    DayPilotError,
    DemoWorkspaceError,
    PlanRevisionError,
    RunConflictError,
    RunNotFoundError,
)
from backend.app.domain.models import RuntimeState
from backend.app.graph.workflow import WorkflowDependencies, build_daypilot_graph
from backend.app.mcp.gateway import MCPGateway
from backend.app.persistence.repository import DayPilotRepository
from backend.app.providers.manager import ConnectionManager
from backend.app.services.admin_auth import AdminAuthService
from backend.app.services.coordinator import RunCoordinator
from backend.app.services.demo_workspace import DemoWorkspaceService
from backend.app.services.planner import PlanBuilder
from backend.app.services.reasoner import create_reasoner
from mcp_servers.common.database import ensure_demo_database_schema, initialize_demo_database


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.runtime_state = RuntimeState.STARTING.value
    app.state.readiness = {
        "state": RuntimeState.STARTING.value,
        "mcp_servers_ready": 0,
        "mcp_servers_total": 6,
        "degraded_services": [],
        "message": "DayPilot is waking up and connecting services.",
    }
    if settings.daypilot_demo_mode:
        initialize_demo_database(settings.database_target, settings.daypilot_timezone)
    else:
        ensure_demo_database_schema(settings.database_target)
    repository = DayPilotRepository(settings.database_target)
    await repository.initialize()
    provider_defaults = {
        service: settings.configured_provider(service)
        for service in ("mail", "calendar", "tasks", "files", "x")
    }
    stored_modes = await repository.list_provider_modes()
    if not stored_modes or (
        not settings.daypilot_demo_mode
        and set(stored_modes.values()) <= {"demo"}
        and any(mode != "demo" for mode in provider_defaults.values())
    ):
        await repository.ensure_provider_modes(provider_defaults)
        if stored_modes:
            for service, mode in provider_defaults.items():
                await repository.set_provider_mode(service, mode)
    connections = ConnectionManager(settings, repository)
    gateway = MCPGateway(settings, connections)
    reasoner = create_reasoner(settings)
    planner = PlanBuilder(settings.daypilot_timezone)
    if settings.database_is_postgres:
        if AsyncPostgresSaver is None:
            raise RuntimeError(
                "PostgreSQL DATABASE_URL requires langgraph-checkpoint-postgres to be installed."
            )
        checkpointer_context = AsyncPostgresSaver.from_conn_string(settings.database_url)
    else:
        checkpointer_context = AsyncSqliteSaver.from_conn_string(str(settings.database_path))
    async with checkpointer_context as checkpointer:
        await checkpointer.setup()
        graph = build_daypilot_graph(
            WorkflowDependencies(
                repository=repository,
                gateway=gateway,
                reasoner=reasoner,
                planner=planner,
            ),
            checkpointer,
        )
        coordinator = RunCoordinator(graph, repository, gateway)
        demo_workspace = DemoWorkspaceService(settings, repository)
        admin_auth = AdminAuthService(settings, repository)
        app.state.settings = settings
        app.state.repository = repository
        app.state.gateway = gateway
        app.state.connections = connections
        app.state.graph = graph
        app.state.coordinator = coordinator
        app.state.demo_workspace = demo_workspace
        app.state.admin_auth = admin_auth
        readiness_task = asyncio.create_task(_initialize_runtime(app, gateway, settings))
        yield
        readiness_task.cancel()
        await asyncio.gather(readiness_task, return_exceptions=True)
        await coordinator.shutdown()


async def _initialize_runtime(app: FastAPI, gateway: MCPGateway, settings: Settings) -> None:
    """Warm MCP transports after the lightweight server startup has completed."""
    try:
        await gateway.discover(force=True, admin_authorized=True)
        catalog = gateway.catalog(admin_authorized=True)
        degraded: list[str] = []
        for server in catalog:
            if settings.public_demo_mode and server["name"] != "web":
                # Private providers are intentionally outside anonymous runtime
                # readiness. Their exact state is shown only after admin unlock.
                continue
            if not server["connected"]:
                degraded.append(server["name"])
                continue
            if server.get("provider_state") not in {None, "connected"}:
                degraded.append(server["name"])
        state = RuntimeState.DEGRADED if degraded else RuntimeState.READY
        if state == RuntimeState.READY:
            message = (
                "Public demo ready. Personal services require admin access."
                if settings.public_demo_mode
                else "DayPilot is ready."
            )
        else:
            message = "DayPilot is ready with limited capabilities: " + ", ".join(degraded) + "."
        app.state.runtime_state = state.value
        app.state.readiness = {
            "state": state.value,
            "mcp_servers_ready": sum(server["connected"] for server in catalog),
            "mcp_servers_total": len(catalog),
            "degraded_services": degraded,
            "message": message,
        }
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        app.state.runtime_state = RuntimeState.DEGRADED.value
        app.state.readiness = {
            "state": RuntimeState.DEGRADED.value,
            "mcp_servers_ready": 0,
            "mcp_servers_total": len(gateway.connections),
            "degraded_services": ["mcp"],
            "message": "DayPilot is ready with limited MCP capabilities.",
        }
        app.state.readiness_error = str(exc)


app = FastAPI(
    title="DayPilot API",
    description="MCP-powered personal operations with persisted LangGraph approval gates.",
    version="0.1.0",
    lifespan=lifespan,
)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(router)


@app.exception_handler(RunNotFoundError)
async def run_not_found(_: Request, exc: RunNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(RunConflictError)
async def run_conflict(_: Request, exc: RunConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(PlanRevisionError)
async def plan_revision_failed(_: Request, exc: PlanRevisionError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.exception_handler(DayPilotError)
async def daypilot_error(_: Request, exc: DayPilotError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(DemoWorkspaceError)
async def demo_workspace_error(_: Request, exc: DemoWorkspaceError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})
