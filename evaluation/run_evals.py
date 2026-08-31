from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.app.config import Settings
from backend.app.graph.workflow import WorkflowDependencies, build_daypilot_graph
from backend.app.mcp.gateway import MCPGateway
from backend.app.mcp.policy import get_policy
from backend.app.persistence.repository import DayPilotRepository
from backend.app.services.coordinator import RunCoordinator
from backend.app.services.planner import PlanBuilder
from backend.app.services.reasoner import DeterministicReasoner
from evaluation.scenarios import SCENARIOS, EvaluationScenario
from mcp_servers.common.database import initialize_demo_database


@dataclass
class ScenarioResult:
    name: str
    tool_selection_correct: bool
    plan_valid: bool
    approval_correct: bool
    dependency_correct: bool
    unauthorized_writes: int
    planned_writes: int
    executed_writes: int
    successful_writes: int
    passed: bool
    details: dict[str, Any]


async def evaluate_scenario(scenario: EvaluationScenario) -> ScenarioResult:
    with tempfile.TemporaryDirectory(prefix="daypilot-eval-") as directory:
        database_path = Path(directory) / "eval.db"
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
        async with AsyncSqliteSaver.from_conn_string(str(database_path)) as checkpointer:
            await checkpointer.setup()
            graph = build_daypilot_graph(
                WorkflowDependencies(
                    repository,
                    gateway,
                    DeterministicReasoner(settings.daypilot_timezone),
                    PlanBuilder(settings.daypilot_timezone),
                ),
                checkpointer,
            )
            coordinator = RunCoordinator(graph, repository, gateway)
            accepted = await coordinator.start_run(scenario.request)
            detail = await coordinator.wait_until_settled(accepted.id, max_wait_seconds=60)
            actual_reads = {action.tool_name for action in detail.plan if not action.side_effecting}
            actual_writes = {action.tool_name for action in detail.plan if action.side_effecting}
            actions_by_id = {action.id: action for action in detail.plan}
            actual_dependencies = {
                (actions_by_id[dependency].tool_name, action.tool_name)
                for action in detail.plan
                for dependency in action.depends_on
                if dependency in actions_by_id
            }
            before_approval = await repository.list_executions(accepted.id)
            unauthorized_writes = len(before_approval)
            tool_selection_correct = actual_reads == set(
                scenario.expected_read_tools
            ) and actual_writes == set(scenario.expected_write_tools)
            plan_valid = all(
                gateway.metadata(action.tool_name) is not None
                and get_policy(action.tool_name, action.server_name).side_effecting
                == action.side_effecting
                for action in detail.plan
            )
            approval_correct = (detail.status == "waiting_approval") == scenario.approval_required
            dependency_correct = scenario.expected_dependency_tools is None or (
                actual_dependencies == set(scenario.expected_dependency_tools)
            )
            executed_writes = 0
            successful_writes = 0
            if scenario.execute and scenario.approval_required:
                await coordinator.resume(accepted.id, "approve")
                detail = await coordinator.wait_until_settled(accepted.id, max_wait_seconds=60)
                executed_writes = len(detail.execution_results)
                successful_writes = sum(result.success for result in detail.execution_results)
            await coordinator.shutdown()
            execution_correct = not scenario.execute or (
                executed_writes == len(scenario.expected_write_tools)
                and successful_writes == executed_writes
            )
            passed = all(
                (
                    tool_selection_correct,
                    plan_valid,
                    approval_correct,
                    dependency_correct,
                    unauthorized_writes == 0,
                    execution_correct,
                )
            )
            return ScenarioResult(
                name=scenario.name,
                tool_selection_correct=tool_selection_correct,
                plan_valid=plan_valid,
                approval_correct=approval_correct,
                dependency_correct=dependency_correct,
                unauthorized_writes=unauthorized_writes,
                planned_writes=len(actual_writes),
                executed_writes=executed_writes,
                successful_writes=successful_writes,
                passed=passed,
                details={
                    "expected_reads": sorted(scenario.expected_read_tools),
                    "actual_reads": sorted(actual_reads),
                    "expected_writes": sorted(scenario.expected_write_tools),
                    "actual_writes": sorted(actual_writes),
                    "expected_dependencies": sorted(scenario.expected_dependency_tools or []),
                    "actual_dependencies": sorted(actual_dependencies),
                },
            )


async def run_evaluations() -> dict[str, Any]:
    results = []
    for scenario in SCENARIOS:
        result = await evaluate_scenario(scenario)
        results.append(result)
        marker = "PASS" if result.passed else "FAIL"
        print(f"[{marker}] {result.name}")
        if not result.passed:
            print("       " + json.dumps(result.details, sort_keys=True))
    total = len(results)
    planned_writes = sum(result.planned_writes for result in results)
    executed_writes = sum(result.executed_writes for result in results)
    successful_writes = sum(result.successful_writes for result in results)
    summary = {
        "scenarios": total,
        "scenario_pass_rate": sum(result.passed for result in results) / total,
        "tool_selection_accuracy": (
            sum(result.tool_selection_correct for result in results) / total
        ),
        "plan_validity": sum(result.plan_valid for result in results) / total,
        "approval_required_correctness": (
            sum(result.approval_correct for result in results) / total
        ),
        "dependency_accuracy": sum(result.dependency_correct for result in results) / total,
        "unauthorized_write_rate": (
            sum(result.unauthorized_writes for result in results) / max(planned_writes, 1)
        ),
        "execution_success": successful_writes / max(executed_writes, 1),
        "results": [asdict(result) for result in results],
    }
    print("\nMetrics")
    for key, value in summary.items():
        if key != "results":
            rendered = f"{value:.1%}" if isinstance(value, float) else str(value)
            print(f"  {key}: {rendered}")
    return summary


def main() -> None:
    summary = asyncio.run(run_evaluations())
    if summary["unauthorized_write_rate"] != 0 or summary["scenario_pass_rate"] != 1:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
