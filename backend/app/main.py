from __future__ import annotations

import asyncio
import logging
import re
import traceback
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

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.settings = settings
    app.state.runtime_state = RuntimeState.STARTING.value
    app.state.database_state = "initializing"
    app.state.graph_state = "initializing"
    app.state.runtime_services_ready = False
    app.state.readiness = {
        "state": RuntimeState.STARTING.value,
        "mcp_servers_ready": 0,
        "mcp_servers_total": 6,
        "degraded_services": [],
        "message": "DayPilot is waking up and connecting services.",
    }
    shutdown_event = asyncio.Event()
    bootstrap_task = asyncio.create_task(
        _bootstrap_application(app, settings, shutdown_event),
        name="daypilot-runtime-bootstrap",
    )
    app.state.bootstrap_task = bootstrap_task
    try:
        yield
    finally:
        shutdown_event.set()
        try:
            await asyncio.wait_for(asyncio.shield(bootstrap_task), timeout=5)
        except TimeoutError:
            bootstrap_task.cancel()
            await asyncio.gather(bootstrap_task, return_exceptions=True)


async def _bootstrap_application(
    app: FastAPI,
    settings: Settings,
    shutdown_event: asyncio.Event,
) -> None:
    """Initialize persistence and graph resources without delaying HTTP binding."""
    stage = "service database"
    try:
        initializer = (
            initialize_demo_database if settings.daypilot_demo_mode else ensure_demo_database_schema
        )
        if settings.daypilot_demo_mode:
            await asyncio.to_thread(
                initializer,
                settings.database_target,
                settings.daypilot_timezone,
            )
        else:
            await asyncio.to_thread(initializer, settings.database_target)

        stage = "application database"
        repository = DayPilotRepository(settings.database_target)
        await repository.initialize()
        app.state.database_state = "connected"
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

        stage = "provider state"
        connections = await asyncio.to_thread(ConnectionManager, settings, repository)
        gateway = MCPGateway(settings, connections)
        reasoner = create_reasoner(settings)
        planner = PlanBuilder(settings.daypilot_timezone)
        if settings.database_is_postgres:
            if AsyncPostgresSaver is None:
                raise RuntimeError(
                    "PostgreSQL DATABASE_URL requires "
                    "langgraph-checkpoint-postgres to be installed."
                )
            checkpointer_context = AsyncPostgresSaver.from_conn_string(
                settings.database_connection_url
            )
        else:
            checkpointer_context = AsyncSqliteSaver.from_conn_string(str(settings.database_path))

        stage = "LangGraph checkpointer"
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
            app.state.repository = repository
            app.state.gateway = gateway
            app.state.connections = connections
            app.state.graph = graph
            app.state.coordinator = coordinator
            app.state.demo_workspace = DemoWorkspaceService(settings, repository)
            app.state.admin_auth = AdminAuthService(settings, repository)
            app.state.graph_state = "ready"
            app.state.runtime_services_ready = True
            readiness_task = asyncio.create_task(
                _initialize_runtime(app, gateway, settings),
                name="daypilot-capability-readiness",
            )
            try:
                await shutdown_event.wait()
            finally:
                readiness_task.cancel()
                await asyncio.gather(readiness_task, return_exceptions=True)
                await coordinator.shutdown()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(
            "DayPilot bootstrap failed during %s (%s)\n%s",
            stage,
            type(exc).__name__,
            _redacted_bootstrap_traceback(exc, settings),
        )
        if app.state.database_state != "connected":
            app.state.database_state = "unavailable"
        app.state.graph_state = "unavailable"
        app.state.runtime_services_ready = False
        app.state.runtime_state = RuntimeState.DEGRADED.value
        service = "database" if "database" in stage else "checkpointer"
        app.state.readiness = {
            "state": RuntimeState.DEGRADED.value,
            "mcp_servers_ready": 0,
            "mcp_servers_total": 6,
            "degraded_services": [service],
            "message": f"DayPilot persistence is unavailable during {stage} initialization.",
        }
        await shutdown_event.wait()


def _redacted_bootstrap_traceback(exc: Exception, settings: Settings) -> str:
    """Keep startup diagnostics useful without placing credentials in logs."""
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    secrets = (
        settings.database_url,
        settings.database_connection_url,
        settings.openai_api_key,
        settings.tavily_api_key,
        settings.composio_api_key,
        settings.admin_secret,
        settings.google_client_secret,
        settings.x_client_secret,
    )
    for secret in secrets:
        if secret:
            trace = trace.replace(secret, "[redacted]")
    trace = re.sub(
        r"(?i)(postgres(?:ql)?://)[^\s'\"`]+",
        r"\1[redacted]",
        trace,
    )
    trace = re.sub(
        r"(?i)(password|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|secret)"
        r"\s*[=:]\s*[^\s,;]+",
        r"\1=[redacted]",
        trace,
    )
    return trace


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


@app.middleware("http")
async def require_initialized_runtime(request: Request, call_next):
    startup_safe_paths = {"/api/readiness", "/api/admin/status"}
    if (
        request.url.path.startswith("/api/")
        and request.url.path not in startup_safe_paths
        and not getattr(request.app.state, "runtime_services_ready", False)
    ):
        readiness = getattr(request.app.state, "readiness", {})
        return JSONResponse(
            status_code=503,
            content={
                "detail": readiness.get(
                    "message",
                    "DayPilot is still waking up and connecting persistence.",
                )
            },
        )
    return await call_next(request)


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
