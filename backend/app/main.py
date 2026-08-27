from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.app.api.routes import router
from backend.app.config import get_settings
from backend.app.domain.errors import (
    DayPilotError,
    DemoWorkspaceError,
    PlanRevisionError,
    RunConflictError,
    RunNotFoundError,
)
from backend.app.graph.workflow import WorkflowDependencies, build_daypilot_graph
from backend.app.mcp.gateway import MCPGateway
from backend.app.persistence.repository import DayPilotRepository
from backend.app.services.coordinator import RunCoordinator
from backend.app.services.demo_workspace import DemoWorkspaceService
from backend.app.services.planner import PlanBuilder
from backend.app.services.reasoner import create_reasoner
from mcp_servers.common.database import initialize_demo_database


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    initialize_demo_database(settings.database_path, settings.daypilot_timezone)
    repository = DayPilotRepository(settings.database_path)
    await repository.initialize()
    gateway = MCPGateway(settings)
    reasoner = create_reasoner(settings)
    planner = PlanBuilder(settings.daypilot_timezone)
    async with AsyncSqliteSaver.from_conn_string(str(settings.database_path)) as checkpointer:
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
        app.state.settings = settings
        app.state.repository = repository
        app.state.gateway = gateway
        app.state.graph = graph
        app.state.coordinator = coordinator
        app.state.demo_workspace = demo_workspace
        yield
        await coordinator.shutdown()


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
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
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
