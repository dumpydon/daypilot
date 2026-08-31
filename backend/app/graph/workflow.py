from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from backend.app.domain.errors import UnauthorizedToolCallError
from backend.app.domain.models import (
    ActionStatus,
    ApprovalStatus,
    EventState,
    ExecutionResult,
    PlanAction,
    PreferenceSet,
    ToolMetadata,
    UserIntent,
)
from backend.app.graph.state import DayPilotState
from backend.app.mcp.gateway import MCPGateway
from backend.app.mcp.policy import WriteAuthorization, plan_hash
from backend.app.persistence.repository import DayPilotRepository
from backend.app.services.planner import PlanBuilder
from backend.app.services.reasoner import Reasoner
from backend.app.services.summarizer import summarize_execution


@dataclass(frozen=True)
class WorkflowDependencies:
    repository: DayPilotRepository
    gateway: MCPGateway
    reasoner: Reasoner
    planner: PlanBuilder


def build_daypilot_graph(dependencies: WorkflowDependencies, checkpointer: Any):
    repository = dependencies.repository
    gateway = dependencies.gateway
    reasoner = dependencies.reasoner
    planner = dependencies.planner

    async def understand_request(state: DayPilotState) -> dict[str, Any]:
        intent = await reasoner.understand(state["user_request"])
        await repository.append_event(
            state["run_id"],
            "request_understood",
            EventState.COMPLETED,
            "Request understood",
            _intent_detail(intent),
            {"intent": intent.model_dump(mode="json"), "reasoning_mode": reasoner.mode},
        )
        return {
            "intent": intent.model_dump(mode="json"),
            "reasoning_mode": reasoner.mode,
            "updated_at": _now(),
        }

    async def discover_tools(state: DayPilotState) -> dict[str, Any]:
        tools = await gateway.discover(force=True)
        connected = sum(server["connected"] for server in gateway.catalog())
        await repository.append_event(
            state["run_id"],
            "tools_discovered",
            EventState.COMPLETED if tools else EventState.FAILED,
            f"{len(tools)} MCP tools discovered",
            f"{connected} of {len(gateway.catalog())} MCP servers connected",
            {"servers": gateway.catalog()},
        )
        errors = list(state.get("errors", []))
        for server in gateway.catalog():
            if not server["connected"]:
                errors.append(f"{server['name']} MCP unavailable: {server['error']}")
        return {
            "available_tools": [tool.model_dump(mode="json") for tool in tools],
            "errors": errors,
            "updated_at": _now(),
        }

    async def gather_context(state: DayPilotState) -> dict[str, Any]:
        run_id = state["run_id"]
        tools = [ToolMetadata.model_validate(tool) for tool in state.get("available_tools", [])]
        intent = UserIntent.model_validate(state["intent"])
        preferences = PreferenceSet.model_validate(state["preferences"])
        await repository.append_event(
            run_id,
            "context_gathering_started",
            EventState.RUNNING,
            "Gathering connected-service context",
            "Only read tools may run in this phase",
        )
        read_plan = await reasoner.select_read_calls(
            state["user_request"], intent, tools, preferences
        )
        read_calls = planner.order_read_calls(state["user_request"], intent, read_plan.calls)
        context: dict[str, list[dict[str, Any]]] = {
            server_name: [] for server_name in gateway.connections
        }
        errors = list(state.get("errors", []))
        calls_made = 0

        for read_index, proposed in enumerate(read_calls[:8]):
            grounded_arguments = planner.ground_read_arguments(
                state["user_request"],
                intent,
                proposed.tool_name,
                proposed.arguments,
                context,
                preferences,
            )
            if _read_arguments_unavailable(gateway, proposed.tool_name, grounded_arguments):
                record = await _blocked_read(
                    run_id,
                    repository,
                    gateway,
                    proposed.tool_name,
                    grounded_arguments,
                    proposed.reason,
                )
            else:
                record = await _invoke_read(
                    run_id,
                    gateway,
                    repository,
                    proposed.tool_name,
                    grounded_arguments,
                    proposed.reason,
                )
            calls_made += 1
            server = record.pop("server_name")
            context.setdefault(server, []).append(record)
            if not record["success"]:
                errors.append(record["error"])

            follow_up = _read_follow_up(proposed.tool_name, record.get("result"))
            later_read_tools = {call.tool_name for call in read_calls[read_index + 1 : 8]}
            if (
                follow_up
                and record["success"]
                and calls_made < 8
                and gateway.metadata(follow_up[0]) is not None
                and follow_up[0] not in later_read_tools
            ):
                follow_up_tool, follow_up_arguments, follow_up_reason = follow_up
                if _read_arguments_unavailable(gateway, follow_up_tool, follow_up_arguments):
                    follow_up_record = await _blocked_read(
                        run_id,
                        repository,
                        gateway,
                        follow_up_tool,
                        follow_up_arguments,
                        follow_up_reason,
                    )
                else:
                    follow_up_record = await _invoke_read(
                        run_id,
                        gateway,
                        repository,
                        follow_up_tool,
                        follow_up_arguments,
                        follow_up_reason,
                    )
                calls_made += 1
                follow_up_server = follow_up_record.pop("server_name")
                context.setdefault(follow_up_server, []).append(follow_up_record)
                if not follow_up_record["success"]:
                    errors.append(follow_up_record["error"])

        facts = sum(record["success"] for records in context.values() for record in records)
        await repository.append_event(
            run_id,
            "context_gathered",
            EventState.COMPLETED,
            "Grounded context collected",
            f"{facts} successful read calls · {calls_made} total",
        )
        return {"context": context, "errors": errors, "updated_at": _now()}

    async def build_plan(state: DayPilotState) -> dict[str, Any]:
        intent = UserIntent.model_validate(state["intent"])
        tools = [ToolMetadata.model_validate(tool) for tool in state.get("available_tools", [])]
        preferences = PreferenceSet.model_validate(state["preferences"])
        revision = state.get("plan_revision", 0) + 1
        feedback = state.get("approval_feedback")
        previous_plan = [PlanAction.model_validate(item) for item in state.get("plan", [])]
        if feedback:
            await repository.append_event(
                state["run_id"],
                "replanning_started",
                EventState.RUNNING,
                "Replanning with user feedback",
                feedback,
                {"previous_revision": revision - 1},
            )
            revised_writes = await reasoner.revise_write_actions(
                state["user_request"],
                intent,
                state.get("context", {}),
                tools,
                preferences,
                previous_plan,
                feedback,
            )
            if revised_writes is not None:
                actions = [*planner.read_actions(state.get("context", {})), *revised_writes]
                actions = planner.finalize(
                    actions,
                    state["user_request"],
                    intent,
                    state.get("context", {}),
                    tools,
                )
            else:
                actions = planner.build(
                    state["user_request"],
                    intent,
                    state.get("context", {}),
                    tools,
                    preferences,
                    feedback,
                )
        else:
            proposed_writes = await reasoner.propose_write_actions(
                state["user_request"],
                intent,
                state.get("context", {}),
                tools,
                preferences,
            )
            if proposed_writes is not None:
                actions = [*planner.read_actions(state.get("context", {})), *proposed_writes]
                actions = planner.finalize(
                    actions,
                    state["user_request"],
                    intent,
                    state.get("context", {}),
                    tools,
                )
            else:
                actions = planner.build(
                    state["user_request"],
                    intent,
                    state.get("context", {}),
                    tools,
                    preferences,
                )
        write_actions = [action for action in actions if action.side_effecting]
        read_actions = [action for action in actions if not action.side_effecting]
        plan_payload = [action.model_dump(mode="json") for action in actions]
        proposed_hash = plan_hash(write_actions)
        await repository.set_plan(
            state["run_id"],
            plan_payload,
            approval_required=bool(write_actions),
        )
        event_type = "plan_revised" if feedback else "plan_generated"
        event_title = "Revised plan generated" if feedback else "Action plan generated"
        await repository.append_event(
            state["run_id"],
            event_type,
            EventState.COMPLETED,
            event_title,
            (
                f"{len(read_actions)} reads · {len(write_actions)} proposed writes "
                f"· revision {revision}"
            ),
            {"plan": plan_payload, "revision": revision, "plan_hash": proposed_hash},
        )
        approval_status = ApprovalStatus.PENDING if write_actions else ApprovalStatus.NOT_REQUIRED
        if write_actions:
            await repository.append_event(
                state["run_id"],
                "approval_required",
                EventState.WAITING_APPROVAL,
                "Waiting for human approval",
                f"{len(write_actions)} external changes are blocked",
                {
                    "write_actions": [action.model_dump(mode="json") for action in write_actions],
                    "revision": revision,
                    "plan_hash": proposed_hash,
                },
            )
        return {
            "plan": plan_payload,
            "read_actions": [action.model_dump(mode="json") for action in read_actions],
            "write_actions": [action.model_dump(mode="json") for action in write_actions],
            "approval_status": approval_status.value,
            "approval_feedback": feedback,
            "approved_plan_hash": None,
            "plan_revision": revision,
            "plan_hash": proposed_hash,
            "updated_at": _now(),
        }

    async def approval_gate(state: DayPilotState) -> dict[str, Any]:
        decision = interrupt(
            {
                "type": "approval_required",
                "run_id": state["run_id"],
                "plan_revision": state.get("plan_revision", 1),
                "plan_hash": state.get("plan_hash"),
                "write_actions": state.get("write_actions", []),
                "message": "External state changes remain blocked until you approve them.",
            }
        )
        if not isinstance(decision, dict):
            decision = {"decision": "reject", "feedback": "Invalid approval response"}
        choice = str(decision.get("decision", "reject")).lower()
        feedback = decision.get("feedback")
        if choice == "approve":
            actions = [PlanAction.model_validate(item) for item in state.get("write_actions", [])]
            approved_hash = plan_hash(actions)
            await repository.set_approval(state["run_id"], ApprovalStatus.APPROVED)
            await repository.append_event(
                state["run_id"],
                "approval_received",
                EventState.COMPLETED,
                "Plan approved",
                f"Authorization bound to {len(actions)} exact write payloads",
                {"approved_plan_hash": approved_hash},
            )
            return {
                "approval_status": ApprovalStatus.APPROVED.value,
                "approved_plan_hash": approved_hash,
                "updated_at": _now(),
            }
        if choice == "edit":
            feedback_text = str(feedback or "Revise the plan")
            await repository.set_approval(state["run_id"], ApprovalStatus.EDITED, feedback_text)
            await repository.append_event(
                state["run_id"],
                "plan_feedback_received",
                EventState.COMPLETED,
                "Plan feedback received",
                feedback_text,
            )
            return {
                "approval_status": ApprovalStatus.EDITED.value,
                "approval_feedback": feedback_text,
                "updated_at": _now(),
            }
        await repository.set_approval(state["run_id"], ApprovalStatus.REJECTED, feedback)
        await repository.append_event(
            state["run_id"],
            "run_rejected",
            EventState.COMPLETED,
            "Plan rejected",
            "No write tools were called",
        )
        return {
            "approval_status": ApprovalStatus.REJECTED.value,
            "approval_feedback": feedback,
            "updated_at": _now(),
        }

    async def execute_actions(state: DayPilotState) -> dict[str, Any]:
        if state.get("approval_status") != ApprovalStatus.APPROVED:
            raise UnauthorizedToolCallError("Execution node reached without persisted approval")
        write_actions = [PlanAction.model_validate(item) for item in state.get("write_actions", [])]
        approved_hash = state.get("approved_plan_hash")
        if not approved_hash or approved_hash != plan_hash(write_actions):
            raise UnauthorizedToolCallError("Approved plan integrity check failed before execution")

        await repository.append_event(
            state["run_id"],
            "execution_started",
            EventState.RUNNING,
            "Executing approved actions",
            f"{len(write_actions)} single-attempt writes",
        )
        results: list[dict[str, Any]] = []
        plan = [PlanAction.model_validate(item) for item in state.get("plan", [])]
        by_id = {action.id: action for action in plan}
        authorization_actions = tuple(write_actions)

        for action in write_actions:
            attempt = await repository.begin_execution(
                state["run_id"], action.id, action.tool_name, action.arguments
            )
            if not attempt["is_new"]:
                existing = _existing_execution_result(action, attempt)
                results.append(existing.model_dump(mode="json"))
                by_id[action.id].status = (
                    ActionStatus.EXECUTED if existing.success else ActionStatus.FAILED
                )
                continue

            await repository.append_event(
                state["run_id"],
                "action_started",
                EventState.RUNNING,
                action.description,
                f"{action.server_name.title()} MCP · {action.tool_name}",
                {"action_id": action.id, "arguments": action.arguments},
            )
            authorization = WriteAuthorization(
                run_id=state["run_id"],
                action_id=action.id,
                tool_name=action.tool_name,
                arguments=action.arguments,
                approved_plan_hash=approved_hash,
                approved_actions=authorization_actions,
            )
            try:
                result = await gateway.invoke(
                    action.tool_name,
                    action.arguments,
                    authorization=authorization,
                )
                execution = ExecutionResult(
                    action_id=action.id,
                    tool_name=action.tool_name,
                    arguments=action.arguments,
                    result=result,
                    success=True,
                    executed_at=datetime.now(UTC),
                )
                await repository.complete_execution(
                    state["run_id"], action.id, success=True, result=result
                )
                by_id[action.id].status = ActionStatus.EXECUTED
                await repository.append_event(
                    state["run_id"],
                    "action_completed",
                    EventState.COMPLETED,
                    action.description,
                    f"{action.tool_name} completed",
                    {"action_id": action.id, "result": result},
                )
            except Exception as exc:
                execution = ExecutionResult(
                    action_id=action.id,
                    tool_name=action.tool_name,
                    arguments=action.arguments,
                    success=False,
                    error=str(exc),
                    executed_at=datetime.now(UTC),
                )
                await repository.complete_execution(
                    state["run_id"], action.id, success=False, error=str(exc)
                )
                by_id[action.id].status = ActionStatus.FAILED
                await repository.append_event(
                    state["run_id"],
                    "action_failed",
                    EventState.FAILED,
                    action.description,
                    str(exc),
                    {"action_id": action.id},
                )
            results.append(execution.model_dump(mode="json"))

        return {
            "plan": [action.model_dump(mode="json") for action in plan],
            "execution_results": results,
            "updated_at": _now(),
        }

    async def verify_execution(state: DayPilotState) -> dict[str, Any]:
        verifications: list[dict[str, Any]] = []
        results = state.get("execution_results", [])
        for result in results:
            if not result.get("success"):
                continue
            verification = await _verify_result(state["run_id"], gateway, repository, result)
            verifications.append(verification)
            # Read-back recovery may enrich an otherwise successful provider
            # result with the real resource ID and URL. Persist that enrichment
            # alongside the verification receipt.
            await repository.complete_execution(
                state["run_id"],
                result["action_id"],
                success=True,
                result=result.get("result"),
            )
            await repository.set_verification(state["run_id"], result["action_id"], verification)
            await repository.append_event(
                state["run_id"],
                "execution_verified",
                EventState.COMPLETED if verification["verified"] else EventState.FAILED,
                f"Verified {result['tool_name']}",
                verification["detail"],
                verification,
            )
        return {"verification_results": verifications, "updated_at": _now()}

    async def summarize(state: DayPilotState) -> dict[str, Any]:
        results = state.get("execution_results", [])
        if results:
            summary = summarize_execution(results, state.get("verification_results", []))
        else:
            summary = await reasoner.summarize_read_only(
                state["user_request"],
                UserIntent.model_validate(state["intent"]),
                state.get("context", {}),
                state.get("errors", []),
            )
        await repository.finish_run(state["run_id"], summary)
        await repository.append_event(
            state["run_id"],
            "run_completed",
            EventState.COMPLETED,
            "Run completed",
            summary,
        )
        return {"final_summary": summary, "updated_at": _now()}

    async def summarize_cancelled(state: DayPilotState) -> dict[str, Any]:
        summary = "Run rejected. No external state was changed and no write tool was called."
        await repository.finish_run(state["run_id"], summary, rejected=True)
        await repository.append_event(
            state["run_id"],
            "run_completed",
            EventState.COMPLETED,
            "Run closed safely",
            summary,
        )
        return {"final_summary": summary, "execution_results": [], "updated_at": _now()}

    graph = StateGraph(DayPilotState)
    graph.add_node("understand_request", understand_request)
    graph.add_node("discover_tools", discover_tools)
    graph.add_node("gather_context", gather_context)
    graph.add_node("build_plan", build_plan)
    graph.add_node("approval_gate", approval_gate)
    graph.add_node("execute_actions", execute_actions)
    graph.add_node("verify_execution", verify_execution)
    graph.add_node("summarize", summarize)
    graph.add_node("summarize_cancelled", summarize_cancelled)
    graph.add_edge(START, "understand_request")
    graph.add_edge("understand_request", "discover_tools")
    graph.add_edge("discover_tools", "gather_context")
    graph.add_edge("gather_context", "build_plan")
    graph.add_conditional_edges(
        "build_plan",
        _route_after_plan,
        {"approval": "approval_gate", "summarize": "summarize"},
    )
    graph.add_conditional_edges(
        "approval_gate",
        _route_after_approval,
        {
            "approved": "execute_actions",
            "edited": "build_plan",
            "rejected": "summarize_cancelled",
        },
    )
    graph.add_edge("execute_actions", "verify_execution")
    graph.add_edge("verify_execution", "summarize")
    graph.add_edge("summarize", END)
    graph.add_edge("summarize_cancelled", END)
    return graph.compile(checkpointer=checkpointer, name="daypilot")


def _route_after_plan(state: DayPilotState) -> Literal["approval", "summarize"]:
    return "approval" if state.get("write_actions") else "summarize"


def _route_after_approval(state: DayPilotState) -> Literal["approved", "edited", "rejected"]:
    status = state.get("approval_status")
    if status == ApprovalStatus.APPROVED:
        return "approved"
    if status == ApprovalStatus.EDITED:
        return "edited"
    return "rejected"


async def _invoke_read(
    run_id: str,
    gateway: MCPGateway,
    repository: DayPilotRepository,
    tool_name: str,
    arguments: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    metadata = gateway.metadata(tool_name)
    server_name = metadata.server_name if metadata else "unknown"
    description = _read_description(tool_name, arguments)
    await repository.append_event(
        run_id,
        "tool_called",
        EventState.RUNNING,
        description,
        f"{server_name.title()} MCP · {tool_name}",
        {"tool_name": tool_name, "arguments": arguments, "risk": "SAFE_READ"},
    )
    try:
        result = await gateway.invoke(tool_name, arguments)
        await repository.append_event(
            run_id,
            "tool_completed",
            EventState.COMPLETED,
            description,
            f"{tool_name} returned grounded data",
            {"tool_name": tool_name},
        )
        return {
            "server_name": server_name,
            "tool_name": tool_name,
            "arguments": arguments,
            "description": description,
            "reason": reason,
            "result": result,
            "success": True,
            "error": None,
        }
    except Exception as exc:
        await repository.append_event(
            run_id,
            "tool_failed",
            EventState.FAILED,
            description,
            str(exc),
            {"tool_name": tool_name},
        )
        return {
            "server_name": server_name,
            "tool_name": tool_name,
            "arguments": arguments,
            "description": description,
            "reason": reason,
            "result": None,
            "success": False,
            "error": f"{tool_name}: {exc}",
        }


async def _blocked_read(
    run_id: str,
    repository: DayPilotRepository,
    gateway: MCPGateway,
    tool_name: str,
    arguments: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """Record a read that cannot safely run because its inputs are unresolved.

    Model-selected read calls can refer to facts that only an earlier read can
    provide (for example ``{{latest_interview_time_from_search_mail}}``). Never
    send those placeholders to an MCP/provider adapter: keeping the failed
    record in context lets the planner block dependent writes deterministically.
    """
    metadata = gateway.metadata(tool_name)
    server_name = metadata.server_name if metadata else "unknown"
    description = _read_description(tool_name, arguments)
    detail = f"{tool_name} was not called because a required input was not grounded "
    detail += "by an earlier read."
    missing = _missing_required_arguments(metadata, arguments)
    if missing:
        detail = f"{tool_name} was not called because required input(s) are missing: "
        detail += ", ".join(missing) + "."
    await repository.append_event(
        run_id,
        "tool_blocked",
        EventState.FAILED,
        description,
        detail,
        {"tool_name": tool_name, "arguments": arguments, "reason": reason},
    )
    return {
        "server_name": server_name,
        "tool_name": tool_name,
        "arguments": arguments,
        "description": description,
        "reason": reason,
        "result": None,
        "success": False,
        "error": f"{tool_name}: {detail}",
    }


def _contains_unresolved_reference(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_unresolved_reference(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_unresolved_reference(item) for item in value)
    if not isinstance(value, str):
        return False
    return bool(
        re.search(
            r"\{\{[^{}]+\}\}|\$\{[^{}]+\}|<[^>\n]*(?:latest|grounded|resolved|from|interview|slot|thread|timezone)[^>\n]*>",
            value,
        )
    )


def _read_arguments_unavailable(
    gateway: MCPGateway,
    tool_name: str,
    arguments: dict[str, Any],
) -> bool:
    metadata = gateway.metadata(tool_name)
    return _contains_unresolved_reference(arguments) or bool(
        _missing_required_arguments(metadata, arguments)
    )


def _missing_required_arguments(
    metadata: Any,
    arguments: dict[str, Any],
) -> list[str]:
    if metadata is None:
        return []
    required = metadata.input_schema.get("required", [])
    if not isinstance(required, list):
        return []
    missing: list[str] = []
    for name in required:
        value = arguments.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(str(name))
        elif _contains_unresolved_reference(value):
            missing.append(str(name))
    return missing


async def _verify_result(
    run_id: str,
    gateway: MCPGateway,
    repository: DayPilotRepository,
    result: dict[str, Any],
) -> dict[str, Any]:
    payload = result.get("result") or {}
    tool_name = result["tool_name"]
    try:
        if tool_name == "create_event":
            read_back = await gateway.invoke(
                "list_events",
                {"start": payload["start_at"], "end": payload["end_at"]},
            )
            events = [event for event in read_back.get("events", []) if isinstance(event, dict)]
            exact = [event for event in events if _event_matches(event, payload)]
            provider_id = payload.get("id")
            identified = next((event for event in exact if event.get("id") == provider_id), None)
            if identified is None and not provider_id and len(exact) == 1:
                identified = exact[0]
                payload["id"] = identified.get("id")
                payload["provider_resource_id"] = identified.get("id")
                payload["external_url"] = identified.get("htmlLink")
            verified = identified is not None
            if verified:
                detail = "Calendar event matched the approved title, time, and timezone."
            elif len(exact) > 1:
                detail = (
                    "Verification read failed: multiple matching calendar events were found; "
                    "no ID was guessed."
                )
            elif exact:
                detail = (
                    "Verification mismatch: the provider event did not match the approved "
                    "resource ID."
                )
            else:
                detail = (
                    "Verification read failed: no calendar event matched the approved title "
                    "and time."
                )
            return {
                "action_id": result["action_id"],
                "verified": verified,
                "detail": detail,
                **({"provider_resource_id": identified.get("id")} if identified else {}),
            }
        elif tool_name in {"create_task", "create_task_batch", "complete_task"}:
            read_back = await gateway.invoke("list_tasks", {})
            expected = payload.get("tasks", []) if tool_name == "create_task_batch" else [payload]
            expected = [task for task in expected if isinstance(task, dict)]
            if not expected or any(not task.get("id") for task in expected):
                return {
                    "action_id": result["action_id"],
                    "verified": False,
                    "detail": (
                        "Verification read failed: Google Tasks did not return every task ID; "
                        "no retry was attempted."
                    ),
                }
            tasks = {
                task["id"]: task
                for task in read_back.get("tasks", [])
                if isinstance(task, dict) and task.get("id")
            }
            verified = all(
                task["id"] in tasks and _task_matches(tasks[task["id"]], task) for task in expected
            )
            if tool_name == "create_task_batch" and payload.get("status") == "partially_created":
                verified = False
            if tool_name == "complete_task" and expected:
                verified = verified and tasks[expected[0]["id"]]["completed"]
        elif tool_name == "create_draft":
            message_id = payload.get("message_id") or payload.get("id")
            if not message_id:
                return {
                    "action_id": result["action_id"],
                    "verified": False,
                    "detail": (
                        "Verification read failed: Gmail did not return a draft message ID; "
                        "no retry was attempted."
                    ),
                }
            read_back = await gateway.invoke("get_message", {"message_id": message_id})
            verified = read_back.get("id") == message_id
            if payload.get("subject"):
                verified = verified and read_back.get("subject") == payload["subject"]
            if payload.get("body"):
                verified = verified and payload["body"][:200] in str(read_back.get("body", ""))
        elif tool_name == "create_post_draft":
            read_back = await gateway.invoke("get_post", {"post_id": payload["id"]})
            verified = read_back.get("id") == payload["id"] and read_back.get("status") == "draft"
        elif tool_name == "publish_post":
            read_back = await gateway.invoke("get_post", {"post_id": payload["id"]})
            verified = (
                read_back.get("id") == payload["id"] and read_back.get("status") == "published"
            )
        else:
            return {
                "action_id": result["action_id"],
                "verified": False,
                "detail": "No verification strategy is registered for this tool.",
            }
        return {
            "action_id": result["action_id"],
            "verified": verified,
            "detail": "Persisted state confirmed by an MCP read tool."
            if verified
            else "Read-back did not confirm the expected persisted state.",
        }
    except Exception:
        return {
            "action_id": result["action_id"],
            "verified": False,
            "detail": "Verification read failed: the provider could not be read back safely.",
        }


def _event_matches(event: dict[str, Any], payload: dict[str, Any]) -> bool:
    if str(event.get("title") or "") != str(payload.get("title") or ""):
        return False
    try:
        provider_start = _instant(event.get("start_at"))
        provider_end = _instant(event.get("end_at"))
        approved_start = _instant(payload.get("start_at"))
        approved_end = _instant(payload.get("end_at"))
    except (TypeError, ValueError):
        return False
    return provider_start == approved_start and provider_end == approved_end


def _task_matches(task: dict[str, Any], payload: dict[str, Any]) -> bool:
    if str(task.get("title") or "") != str(payload.get("title") or ""):
        return False
    expected_due = payload.get("due_at")
    actual_due = task.get("due_at")
    if not expected_due:
        return True
    if not actual_due:
        return False
    try:
        expected_date_value = str(payload.get("due_date") or expected_due).replace("Z", "+00:00")
        expected_date = datetime.fromisoformat(expected_date_value).date()
        actual_date = datetime.fromisoformat(str(actual_due).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return False
    # Google Tasks has date-only due semantics; do not compare the requested
    # wall-clock time as if the provider supported reminders at that time.
    return expected_date == actual_date


def _instant(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing provider datetime")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("naive provider datetime")
    return parsed.astimezone(UTC)


def _existing_execution_result(action: PlanAction, attempt: dict[str, Any]) -> ExecutionResult:
    if attempt["success"] is None:
        return ExecutionResult(
            action_id=action.id,
            tool_name=action.tool_name,
            arguments=action.arguments,
            success=False,
            error=(
                "A previous write attempt has an unknown outcome; it was not retried automatically."
            ),
            executed_at=datetime.fromisoformat(attempt["started_at"]),
        )
    return ExecutionResult(
        action_id=action.id,
        tool_name=action.tool_name,
        arguments=action.arguments,
        result=attempt.get("result"),
        success=bool(attempt["success"]),
        error=attempt.get("error"),
        executed_at=datetime.fromisoformat(attempt["completed_at"] or attempt["started_at"]),
        verification=attempt.get("verification"),
    )


def _intent_detail(intent: UserIntent) -> str:
    pieces = [intent.goal]
    if intent.people:
        pieces.append("People: " + ", ".join(intent.people))
    if intent.date_constraints:
        pieces.append("Timing: " + ", ".join(intent.date_constraints))
    return " · ".join(pieces)


def _read_description(tool_name: str, arguments: dict[str, Any]) -> str:
    descriptions = {
        "search_mail": f"Search mail for “{arguments.get('query', '')}”",
        "get_thread": "Read matching mail thread",
        "get_message": "Read mail message",
        "list_events": "Read calendar events",
        "find_free_slots": (
            f"Find a {arguments['duration_minutes']} minute free slot"
            if arguments.get("duration_minutes")
            else "Find a free preparation slot"
        ),
        "list_tasks": "Review current tasks",
        "search_files": f"Search workspace files for “{arguments.get('query', '')}”",
        "list_files": "List controlled workspace files",
        "get_file_metadata": "Read workspace file metadata",
        "read_file": "Read workspace file content",
        "search_posts": f"Search public X posts for “{arguments.get('query', '')}”",
        "get_post": "Read a public X post",
        "get_user_posts": f"Read public X posts from @{arguments.get('username', '')}",
    }
    return descriptions.get(tool_name, f"Run {tool_name}")


def _read_follow_up(
    tool_name: str,
    result: Any,
) -> tuple[str, dict[str, Any], str] | None:
    """Follow grounded search references with a service read, without inventing IDs."""
    payload = result if isinstance(result, dict) else {}
    if tool_name == "search_mail":
        threads = payload.get("threads", [])
        thread_id = threads[0].get("thread_id") if threads else None
        if thread_id:
            return (
                "get_thread",
                {"thread_id": thread_id},
                "Read the best grounded thread returned by search_mail.",
            )
    if tool_name == "search_files":
        files = payload.get("files", [])
        file_id = files[0].get("id") if files else None
        if file_id:
            return (
                "read_file",
                {"file_id": file_id},
                "Read the best grounded workspace file returned by search_files.",
            )
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat()
