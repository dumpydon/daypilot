import type { RunDetail, TimelineEvent, ToolCatalog } from "@/lib/types";

export function makeRun(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    id: "run-test",
    thread_id: "thread-test",
    user_request: "Prepare me for my interview with Rahul tomorrow.",
    status: "waiting_approval",
    approval_status: "pending",
    approval_feedback: null,
    plan: [
      {
        id: "read-1",
        description: "Search interview mail",
        server_name: "mail",
        tool_name: "search_mail",
        arguments: { query: "Rahul interview" },
        reason: "Ground the interview facts.",
        side_effecting: false,
        status: "completed",
        depends_on: [],
      },
      {
        id: "write-1",
        description: "Reserve 7:00 PM–8:30 PM for interview preparation",
        server_name: "calendar",
        tool_name: "create_event",
        arguments: {},
        reason: "The calendar tool returned the slot as free.",
        side_effecting: true,
        status: "pending",
        depends_on: ["read-1"],
      },
    ],
    final_summary: null,
    error: null,
    created_at: "2026-08-25T10:00:00Z",
    updated_at: "2026-08-25T10:01:00Z",
    intent: null,
    available_tools: [],
    context: { mail: [], calendar: [], tasks: [], files: [], x: [] },
    execution_results: [],
    verification_results: [],
    created_outputs: [],
    events: [],
    preferences: {
      preferred_focus_block_minutes: 90,
      avoid_scheduling_after: "22:00",
      preferred_task_due_time: "18:00",
    },
    reasoning_mode: "deterministic_demo",
    interrupt_payload: null,
    plan_revision: 1,
    plan_hash: "plan-hash-1",
    ...overrides,
  };
}

export function makeEvent(overrides: Partial<TimelineEvent> = {}): TimelineEvent {
  return {
    id: 1,
    run_id: "run-test",
    event_type: "approval_required",
    state: "waiting_for_approval",
    title: "Waiting for human approval",
    detail: "3 external changes are blocked",
    payload: {},
    created_at: "2026-08-25T10:01:00Z",
    ...overrides,
  };
}

export const toolCatalog: ToolCatalog = {
  servers: [
    { name: "mail", connected: true, tool_count: 2, tools: ["search_mail", "create_draft"], error: null },
  ],
  tools: [
    { name: "search_mail", server_name: "mail", description: "Search", risk_level: "SAFE_READ", side_effecting: false, input_schema: {} },
    { name: "create_draft", server_name: "mail", description: "Draft", risk_level: "SIDE_EFFECT", side_effecting: true, input_schema: {} },
  ],
};

export const capabilityCatalog: ToolCatalog = {
  servers: [
    { name: "mail", connected: true, tool_count: 4, tools: ["search_mail", "get_thread", "get_message", "create_draft"], error: null },
    { name: "calendar", connected: true, tool_count: 3, tools: ["list_events", "find_free_slots", "create_event"], error: null },
    { name: "tasks", connected: true, tool_count: 4, tools: ["list_tasks", "create_task", "create_task_batch", "complete_task"], error: null },
    { name: "files", connected: true, tool_count: 4, tools: ["search_files", "list_files", "get_file_metadata", "read_file"], error: null },
    { name: "x", connected: true, tool_count: 5, tools: ["search_posts", "get_post", "get_user_posts", "create_post_draft", "publish_post"], error: null },
  ],
  tools: [
    { name: "search_mail", server_name: "mail", description: "Search", risk_level: "SAFE_READ", side_effecting: false, input_schema: {} },
    { name: "get_thread", server_name: "mail", description: "Thread", risk_level: "SAFE_READ", side_effecting: false, input_schema: {} },
    { name: "get_message", server_name: "mail", description: "Message", risk_level: "SAFE_READ", side_effecting: false, input_schema: {} },
    { name: "create_draft", server_name: "mail", description: "Draft", risk_level: "SIDE_EFFECT", side_effecting: true, input_schema: {} },
    { name: "list_events", server_name: "calendar", description: "Events", risk_level: "SAFE_READ", side_effecting: false, input_schema: {} },
    { name: "find_free_slots", server_name: "calendar", description: "Availability", risk_level: "SAFE_READ", side_effecting: false, input_schema: {} },
    { name: "create_event", server_name: "calendar", description: "Schedule", risk_level: "SIDE_EFFECT", side_effecting: true, input_schema: {} },
    { name: "list_tasks", server_name: "tasks", description: "Tasks", risk_level: "SAFE_READ", side_effecting: false, input_schema: {} },
    { name: "create_task", server_name: "tasks", description: "Create", risk_level: "SIDE_EFFECT", side_effecting: true, input_schema: {} },
    { name: "create_task_batch", server_name: "tasks", description: "Batch", risk_level: "SIDE_EFFECT", side_effecting: true, input_schema: {} },
    { name: "complete_task", server_name: "tasks", description: "Complete", risk_level: "SIDE_EFFECT", side_effecting: true, input_schema: {} },
    { name: "search_files", server_name: "files", description: "Search files", risk_level: "SAFE_READ", side_effecting: false, input_schema: {} },
    { name: "list_files", server_name: "files", description: "List files", risk_level: "SAFE_READ", side_effecting: false, input_schema: {} },
    { name: "get_file_metadata", server_name: "files", description: "File metadata", risk_level: "SAFE_READ", side_effecting: false, input_schema: {} },
    { name: "read_file", server_name: "files", description: "Read file", risk_level: "SAFE_READ", side_effecting: false, input_schema: {} },
    { name: "search_posts", server_name: "x", description: "Search posts", risk_level: "SAFE_READ", side_effecting: false, input_schema: {} },
    { name: "get_post", server_name: "x", description: "Get post", risk_level: "SAFE_READ", side_effecting: false, input_schema: {} },
    { name: "get_user_posts", server_name: "x", description: "User posts", risk_level: "SAFE_READ", side_effecting: false, input_schema: {} },
    { name: "create_post_draft", server_name: "x", description: "Draft post", risk_level: "SIDE_EFFECT", side_effecting: true, input_schema: {} },
    { name: "publish_post", server_name: "x", description: "Publish post", risk_level: "SIDE_EFFECT", side_effecting: true, input_schema: {} },
  ],
};
