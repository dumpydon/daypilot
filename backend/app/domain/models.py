from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JsonDict = dict[str, Any]


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    RESUMING = "resuming"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"


class RiskLevel(StrEnum):
    SAFE_READ = "SAFE_READ"
    SIDE_EFFECT = "SIDE_EFFECT"


class ActionStatus(StrEnum):
    COMPLETED = "completed"
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTED = "executed"
    VERIFIED = "verified"
    FAILED = "failed"
    SKIPPED = "skipped"


class EventState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_APPROVAL = "waiting_for_approval"


class UserIntent(BaseModel):
    goal: str
    request_kind: Literal["general", "research", "personal", "hybrid"] = "personal"
    people: list[str] = Field(default_factory=list)
    date_constraints: list[str] = Field(default_factory=list)
    requested_outcomes: list[str] = Field(default_factory=list)
    requested_operations: list[
        Literal[
            "calendar_create",
            "tasks_create",
            "tasks_complete",
            "mail_draft",
            "x_draft",
            "x_publish",
        ]
    ] = Field(default_factory=list)
    information_needed: list[Literal["web", "mail", "calendar", "tasks", "files", "x"]] = Field(
        default_factory=list
    )


class ToolMetadata(BaseModel):
    name: str
    server_name: str
    description: str
    risk_level: RiskLevel
    side_effecting: bool
    input_schema: JsonDict = Field(default_factory=dict)


class ProposedToolCall(BaseModel):
    tool_name: str
    arguments: JsonDict = Field(default_factory=dict)
    reason: str


class ReadCallPlan(BaseModel):
    calls: list[ProposedToolCall] = Field(default_factory=list, max_length=8)


class PlanAction(BaseModel):
    id: str
    description: str
    server_name: str
    tool_name: str
    arguments: JsonDict = Field(default_factory=dict)
    reason: str
    side_effecting: bool
    status: ActionStatus = ActionStatus.PENDING
    depends_on: list[str] = Field(default_factory=list)


class PlanningProposal(BaseModel):
    actions: list[PlanAction] = Field(default_factory=list, max_length=12)


class ExecutionResult(BaseModel):
    action_id: str
    tool_name: str
    arguments: JsonDict
    result: Any = None
    success: bool
    error: str | None = None
    executed_at: datetime
    verification: JsonDict | None = None


class ReceiptStatus(StrEnum):
    VERIFIED = "verified"
    CREATED = "created"
    FAILED = "failed"
    PARTIALLY_COMPLETED = "partially_completed"


class ResourceReceiptItem(BaseModel):
    resource_id: str | None = None
    title: str
    secondary_text: str | None = None


class ResourceReceiptDetail(BaseModel):
    label: str
    value: str


class ResourceReceipt(BaseModel):
    action_id: str
    resource_type: str
    provider: str
    resource_id: str | None = None
    title: str
    secondary_text: str | None = None
    status: ReceiptStatus
    verified: bool = False
    verification_detail: str | None = None
    external_url: str | None = None
    items: list[ResourceReceiptItem] = Field(default_factory=list)
    details: list[ResourceReceiptDetail] = Field(default_factory=list)
    error: str | None = None


class TimelineEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    run_id: str
    event_type: str
    state: EventState
    title: str
    detail: str | None = None
    payload: JsonDict = Field(default_factory=dict)
    created_at: datetime


class PreferenceSet(BaseModel):
    preferred_focus_block_minutes: int = Field(default=90, ge=15, le=240)
    avoid_scheduling_after: str = Field(default="22:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    preferred_task_due_time: str = Field(default="18:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class RunRecord(BaseModel):
    id: str
    thread_id: str
    user_request: str
    status: RunStatus
    approval_status: ApprovalStatus
    approval_feedback: str | None = None
    plan: list[PlanAction] = Field(default_factory=list)
    final_summary: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class RunDetail(RunRecord):
    intent: UserIntent | None = None
    available_tools: list[ToolMetadata] = Field(default_factory=list)
    context: JsonDict = Field(default_factory=dict)
    execution_results: list[ExecutionResult] = Field(default_factory=list)
    verification_results: list[JsonDict] = Field(default_factory=list)
    created_outputs: list[ResourceReceipt] = Field(default_factory=list)
    events: list[TimelineEvent] = Field(default_factory=list)
    preferences: PreferenceSet = Field(default_factory=PreferenceSet)
    reasoning_mode: str = "deterministic_demo"
    interrupt_payload: JsonDict | None = None
    plan_revision: int = 0
    plan_hash: str | None = None


class CreateRunRequest(BaseModel):
    request: str = Field(min_length=3, max_length=2_000)


class DecisionRequest(BaseModel):
    feedback: str | None = Field(default=None, max_length=2_000)


class FeedbackRequest(BaseModel):
    feedback: str = Field(min_length=2, max_length=2_000)
    plan_revision: int = Field(ge=1)


class RunAccepted(BaseModel):
    id: str
    status: RunStatus


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    database: Literal["connected"] = "connected"
    graph: Literal["ready"] = "ready"
    demo_mode: bool
    reasoning_mode: str


class ProviderConnectionState(StrEnum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    RECONNECT_REQUIRED = "reconnect_required"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


class ProviderConnection(BaseModel):
    service: Literal["mail", "calendar", "tasks", "files", "x"]
    provider: str
    state: ProviderConnectionState
    account_label: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    last_error: str | None = None
    requires_reauth: bool = False
    metadata: JsonDict = Field(default_factory=dict)
    connection_mode: Literal["demo", "managed", "direct", "local"] = "demo"


class ConnectionCatalog(BaseModel):
    demo_mode: bool
    connections: list[ProviderConnection]


class OAuthStartResponse(BaseModel):
    provider: Literal["google", "x"]
    authorization_url: str
    scopes: list[str]
    mode: Literal["managed", "direct"] = "direct"


class FileRoot(BaseModel):
    id: str
    path: str
    label: str
    exists: bool
    added_at: datetime


class FileRootRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2_000)


class DemoResetResponse(BaseModel):
    status: Literal["reset"] = "reset"
    services: list[str]
    preserved_runs: int
    message: str


class RunHistoryClearResponse(BaseModel):
    status: Literal["cleared"] = "cleared"
    runs_removed: int
    events_removed: int
    executions_removed: int
    checkpoints_removed: int
    writes_removed: int
    message: str
