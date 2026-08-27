export type RunStatus =
  | "queued"
  | "running"
  | "waiting_approval"
  | "resuming"
  | "completed"
  | "rejected"
  | "failed";

export type ApprovalStatus =
  | "not_required"
  | "pending"
  | "approved"
  | "rejected"
  | "edited";

export type EventState =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "waiting_for_approval";

export interface UserIntent {
  goal: string;
  people: string[];
  date_constraints: string[];
  requested_outcomes: string[];
  requested_operations: Array<
    "calendar_create"
    | "tasks_create"
    | "tasks_complete"
    | "mail_draft"
    | "x_draft"
    | "x_publish"
  >;
  information_needed: Array<"mail" | "calendar" | "tasks" | "files" | "x">;
}

export interface ToolMetadata {
  name: string;
  server_name: string;
  description: string;
  risk_level: "SAFE_READ" | "SIDE_EFFECT";
  side_effecting: boolean;
  input_schema: Record<string, unknown>;
}

export interface PlanAction {
  id: string;
  description: string;
  server_name: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  reason: string;
  side_effecting: boolean;
  status: "completed" | "pending" | "approved" | "executed" | "verified" | "failed" | "skipped";
}

export interface TimelineEvent {
  id: number;
  run_id: string;
  event_type: string;
  state: EventState;
  title: string;
  detail: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ContextRecord {
  tool_name: string;
  arguments: Record<string, unknown>;
  description: string;
  reason: string;
  result: Record<string, unknown> | null;
  success: boolean;
  error: string | null;
}

export interface ExecutionResult {
  action_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown> | null;
  success: boolean;
  error: string | null;
  executed_at: string;
  verification: Record<string, unknown> | null;
}

export type ReceiptStatus = "verified" | "created" | "failed" | "partially_completed";

export interface ResourceReceiptItem {
  resource_id: string | null;
  title: string;
  secondary_text: string | null;
}

export interface ResourceReceiptDetail {
  label: string;
  value: string;
}

export interface ResourceReceipt {
  action_id: string;
  resource_type: string;
  provider: string;
  resource_id: string | null;
  title: string;
  secondary_text: string | null;
  status: ReceiptStatus;
  verified: boolean;
  verification_detail: string | null;
  external_url: string | null;
  items: ResourceReceiptItem[];
  details: ResourceReceiptDetail[];
  error: string | null;
}

export interface Preferences {
  preferred_focus_block_minutes: number;
  avoid_scheduling_after: string;
  preferred_task_due_time: string;
}

export interface HealthStatus {
  status: "ok";
  database: "connected";
  graph: "ready";
  demo_mode: boolean;
  reasoning_mode: string;
}

export interface DemoResetResponse {
  status: "reset";
  services: string[];
  preserved_runs: number;
  message: string;
}

export interface RunHistoryClearResponse {
  status: "cleared";
  runs_removed: number;
  events_removed: number;
  executions_removed: number;
  checkpoints_removed: number;
  writes_removed: number;
  message: string;
}

export interface RunRecord {
  id: string;
  thread_id: string;
  user_request: string;
  status: RunStatus;
  approval_status: ApprovalStatus;
  approval_feedback: string | null;
  plan: PlanAction[];
  final_summary: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface RunDetail extends RunRecord {
  intent: UserIntent | null;
  available_tools: ToolMetadata[];
  context: Record<string, ContextRecord[]>;
  execution_results: ExecutionResult[];
  verification_results: Array<Record<string, unknown>>;
  created_outputs: ResourceReceipt[];
  events: TimelineEvent[];
  preferences: Preferences;
  reasoning_mode: string;
  interrupt_payload: Record<string, unknown> | null;
  plan_revision: number;
  plan_hash: string | null;
}

export interface MCPServer {
  name: string;
  connected: boolean;
  tool_count: number;
  tools: string[];
  error: string | null;
}

export interface ToolCatalog {
  servers: MCPServer[];
  tools: ToolMetadata[];
}
