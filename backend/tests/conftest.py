from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest_asyncio
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.app.config import Settings
from backend.app.graph.workflow import WorkflowDependencies, build_daypilot_graph
from backend.app.mcp.gateway import MCPGateway
from backend.app.persistence.repository import DayPilotRepository
from backend.app.services.coordinator import RunCoordinator
from backend.app.services.planner import PlanBuilder
from backend.app.services.reasoner import DeterministicReasoner
from mcp_servers.common.database import initialize_demo_database


@dataclass
class Harness:
    database_path: Path
    repository: DayPilotRepository
    gateway: MCPGateway
    graph: Any
    coordinator: RunCoordinator
    reasoner: DeterministicReasoner


@pytest_asyncio.fixture
async def harness(tmp_path: Path):
    database_path = tmp_path / "daypilot-test.db"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{database_path}",
        openai_api_key=None,
        daypilot_demo_mode=True,
        provider_mode="demo",
        daypilot_timezone="Asia/Kolkata",
    )
    initialize_demo_database(database_path, settings.daypilot_timezone)
    repository = DayPilotRepository(database_path)
    await repository.initialize()
    gateway = MCPGateway(settings)
    reasoner = DeterministicReasoner(settings.daypilot_timezone)
    planner = PlanBuilder(settings.daypilot_timezone)
    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as checkpointer:
        await checkpointer.setup()
        graph = build_daypilot_graph(
            WorkflowDependencies(repository, gateway, reasoner, planner),
            checkpointer,
        )
        coordinator = RunCoordinator(graph, repository, gateway)
        yield Harness(database_path, repository, gateway, graph, coordinator, reasoner)
        await coordinator.shutdown()
