import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getReadiness: vi.fn(),
  getHealth: vi.fn(),
  getAdminStatus: vi.fn(),
  getTools: vi.fn(),
  getPreferences: vi.fn(),
  listRuns: vi.fn(),
  getConnections: vi.fn(),
  listFileRoots: vi.fn(),
  createRun: vi.fn(),
  getRun: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ API_URL: "http://daypilot.test", ...api }));

import { DayPilotWorkspace } from "@/components/DayPilotWorkspace";

import { capabilityCatalog, makeEvent, makeRun } from "./factories";

class FakeEventSource {
  addEventListener = vi.fn();
  close = vi.fn();
}

const ready = {
  state: "ready" as const,
  mcp_servers_ready: 6,
  mcp_servers_total: 6,
  degraded_services: [],
  message: "DayPilot is ready.",
};

describe("optimistic run submission", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      configurable: true,
      value: () => null,
    });
    vi.stubGlobal("EventSource", FakeEventSource);
    api.getReadiness.mockResolvedValue(ready);
    api.getHealth.mockResolvedValue({
      status: "ok",
      database: "connected",
      graph: "ready",
      demo_mode: true,
      reasoning_mode: "deterministic_demo",
      runtime_state: "ready",
    });
    api.getAdminStatus.mockResolvedValue({
      authenticated: false,
      public_demo_mode: false,
      expires_at: null,
      message: "",
    });
    api.getTools.mockResolvedValue(capabilityCatalog);
    api.getPreferences.mockResolvedValue({
      preferred_focus_block_minutes: 90,
      avoid_scheduling_after: "22:00",
      preferred_task_due_time: "18:00",
    });
    api.listRuns.mockResolvedValue([]);
    api.getConnections.mockResolvedValue({ demo_mode: true, connections: [] });
    api.listFileRoots.mockResolvedValue([]);
    api.getRun.mockResolvedValue(makeRun({ status: "running", plan: [], intent: {
      goal: "General request",
      request_kind: "general",
      people: [],
      date_constraints: [],
      requested_outcomes: [],
      requested_operations: [],
      information_needed: [],
    }, events: [makeEvent({
      event_type: "request_received",
      state: "completed",
      title: "Request received",
    })] }));
  });

  it("moves into a pending run immediately while create-run is slow", async () => {
    let resolveCreate: ((value: { id: string }) => void) | undefined;
    api.createRun.mockImplementation(() => new Promise((resolve) => {
      resolveCreate = resolve;
    }));
    render(<DayPilotWorkspace />);
    const user = userEvent.setup();
    const input = await screen.findByRole("textbox", { name: "Goal" });
    const prompt = "What is the capital of Romania?";

    await user.type(input, prompt);
    await user.click(screen.getByRole("button", { name: "Start DayPilot run" }));

    expect(screen.getByRole("heading", { name: prompt })).toBeInTheDocument();
    expect(screen.getByText("Starting run")).toBeInTheDocument();
    expect(screen.getAllByText("Starting run…")).toHaveLength(2);
    expect(api.createRun).toHaveBeenCalledTimes(1);
    expect(api.getRun).not.toHaveBeenCalled();

    resolveCreate?.({ id: "run-real" });
    await waitFor(() => expect(api.getRun).toHaveBeenCalledWith("run-real"));
    await waitFor(() => expect(screen.getAllByText("Request received").length).toBeGreaterThan(0));
    expect(screen.queryByText("Starting run…")).not.toBeInTheDocument();
  });

  it("returns to the composer with the prompt intact when creation fails", async () => {
    let rejectCreate: ((reason?: unknown) => void) | undefined;
    api.createRun.mockImplementation(() => new Promise((_, reject) => {
      rejectCreate = reject;
    }));
    render(<DayPilotWorkspace />);
    const user = userEvent.setup();
    const input = await screen.findByRole("textbox", { name: "Goal" });
    const prompt = "Prepare my notes for tomorrow.";

    await user.type(input, prompt);
    await user.click(screen.getByRole("button", { name: "Start DayPilot run" }));
    expect(screen.getByRole("heading", { name: prompt })).toBeInTheDocument();

    rejectCreate?.(new Error("The backend is unavailable."));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Goal" })).toHaveValue(prompt));
    expect(screen.getByText("The backend is unavailable.")).toBeInTheDocument();
  });

  it("creates exactly one run for rapid duplicate submissions", async () => {
    let resolveCreate: ((value: { id: string }) => void) | undefined;
    api.createRun.mockImplementation(() => new Promise((resolve) => {
      resolveCreate = resolve;
    }));
    render(<DayPilotWorkspace />);
    const user = userEvent.setup();
    const input = await screen.findByRole("textbox", { name: "Goal" });
    await user.type(input, "Check my workspace");
    const submit = screen.getByRole("button", { name: "Start DayPilot run" });

    fireEvent.click(submit);
    fireEvent.click(submit);

    expect(api.createRun).toHaveBeenCalledTimes(1);
    resolveCreate?.({ id: "run-real" });
  });
});
