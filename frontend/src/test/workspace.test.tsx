import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CapabilityStrip } from "@/components/CapabilityStrip";
import { ConfirmationDialog } from "@/components/ConfirmationDialog";
import { ConnectionSettings } from "@/components/ConnectionSettings";
import { ContextPanel } from "@/components/ContextPanel";
import { CreatedOutputs } from "@/components/CreatedOutputs";
import { DayPilotLogo } from "@/components/DayPilotLogo";
import { Header } from "@/components/Header";
import { PlanPanel } from "@/components/PlanPanel";
import { PlanDependencyGraph } from "@/components/PlanDependencyGraph";
import { PreferencesDialog } from "@/components/PreferencesDialog";
import { RequestComposer } from "@/components/RequestComposer";
import { Sidebar } from "@/components/Sidebar";
import { HERO_MOTION, HERO_WORDS } from "@/components/RotatingHeroWord";
import { TimelinePanel } from "@/components/TimelinePanel";
import { ToolInspector } from "@/components/ToolInspector";
import { editRun, listRuns } from "@/lib/api";
import { runDisplayTitle } from "@/lib/runPresentation";

import { capabilityCatalog, makeEvent, makeRun, toolCatalog } from "./factories";

afterEach(() => vi.useRealTimers());

function CapabilityHarness() {
  const [open, setOpen] = useState(false);
  const [selectedServer, setSelectedServer] = useState<string | null>(null);
  const inspectorRef = useRef<HTMLElement>(null);

  return (
    <>
      <CapabilityStrip
        catalog={capabilityCatalog}
        selectedServer={selectedServer}
        onSelect={(serverName) => {
          setSelectedServer(serverName);
          setOpen(true);
        }}
      />
      <ToolInspector
        catalog={capabilityCatalog}
        collapsible
        open={open}
        onOpenChange={(nextOpen) => {
          setOpen(nextOpen);
          if (!nextOpen) setSelectedServer(null);
        }}
        expandedServer={selectedServer}
        onExpandedServerChange={setSelectedServer}
        sectionRef={inspectorRef}
      />
    </>
  );
}

function SidebarHarness({
  run,
  onSelect,
  onPreferences = vi.fn(),
}: {
  run: ReturnType<typeof makeRun>;
  onSelect: (runId: string) => void;
  onPreferences?: () => void;
}) {
  return (
    <Sidebar
      runs={[run]}
      activeRunId={null}
      preferences={{ preferred_focus_block_minutes: 90, avoid_scheduling_after: "22:00", preferred_task_due_time: "18:00" }}
      collapsed={false}
      mobileOpen={false}
      onSelect={onSelect}
      onNew={vi.fn()}
      onPreferences={onPreferences}
      onToggle={vi.fn()}
      onCloseMobile={vi.fn()}
      onWidthChange={vi.fn()}
    />
  );
}

function timelineEvents(count: number) {
  return Array.from({ length: count }, (_, index) => makeEvent({
    id: index + 1,
    event_type: `step_${index + 1}`,
    state: index === count - 1 ? "running" : "completed",
    title: `Workflow step ${index + 1}`,
    detail: `Detail ${index + 1}`,
    created_at: `2026-08-25T10:${String(index).padStart(2, "0")}:00Z`,
  }));
}

function dependencyActions() {
  const run = makeRun();
  const readMail = { ...run.plan[0], id: "mail", depends_on: [] };
  const readThread = {
    ...run.plan[0],
    id: "thread",
    tool_name: "get_thread",
    description: "Read interview thread",
    depends_on: ["mail"],
  };
  const createEvent = {
    ...run.plan[1],
    id: "event",
    description: "Create interview preparation block",
    depends_on: ["thread"],
  };
  const createTask = {
    ...run.plan[1],
    id: "task",
    server_name: "tasks",
    tool_name: "create_task",
    description: "Create interview preparation task",
    depends_on: ["thread"],
  };
  return [readMail, readThread, createEvent, createTask];
}

function completedDependencyRun(overrides: Partial<ReturnType<typeof makeRun>> = {}) {
  return makeRun({
    id: "completed-dependency-run",
    status: "completed",
    approval_status: "approved",
    final_summary: "The requested actions were completed and verified.",
    plan: dependencyActions(),
    ...overrides,
  });
}

function mockTimelineScroll() {
  const scrollTo = vi.fn(function scrollTo(this: HTMLElement, options: ScrollToOptions) {
    this.scrollTop = typeof options.top === "number" ? options.top : this.scrollTop;
  });
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    writable: true,
    value: scrollTo,
  });
  return scrollTo;
}

function setTimelineMetrics(
  element: HTMLElement,
  values: { scrollTop: number; scrollHeight: number; clientHeight: number },
) {
  Object.defineProperties(element, {
    scrollTop: { configurable: true, writable: true, value: values.scrollTop },
    scrollHeight: { configurable: true, value: values.scrollHeight },
    clientHeight: { configurable: true, value: values.clientHeight },
  });
}

describe("DayPilot operations workspace", () => {
  it("renders one scalable canonical logo with a restrained active state", () => {
    const { rerender } = render(<DayPilotLogo size={16} />);
    const logo = screen.getByRole("img", { name: "DayPilot logo" });
    expect(logo).toHaveAttribute("width", "16");
    expect(logo).toHaveAttribute("height", "16");
    expect(logo.querySelectorAll("path")).toHaveLength(2);
    const geometry = [...logo.querySelectorAll("path")].map((path) => path.getAttribute("d"));
    expect(geometry.every(Boolean)).toBe(true);

    rerender(<DayPilotLogo size={64} active />);
    expect(screen.getByRole("img", { name: "DayPilot logo" }).getAttribute("class")).toContain("logoActive");
    expect([...logo.querySelectorAll("path")].map((path) => path.getAttribute("d"))).toEqual(geometry);

    rerender(<DayPilotLogo size={24} monochrome active aria-label="DayPilot monochrome logo" />);
    expect(screen.getByRole("img", { name: "DayPilot monochrome logo" }).querySelector("g")).toHaveAttribute("fill", "currentColor");
  });

  it("links both the header logo and wordmark to home with keyboard access", async () => {
    const onHome = vi.fn();
    render(<Header servers={capabilityCatalog.servers} reasoningMode="openai" onMenu={vi.fn()} onHome={onHome} />);
    const home = screen.getByRole("link", { name: "DayPilot home" });
    expect(home).toHaveAttribute("href", "/");
    expect(home).toContainElement(screen.getByRole("img", { name: "DayPilot logo" }));
    expect(home).toContainElement(screen.getByText("DayPilot", { exact: true }));
    await userEvent.tab();
    expect(home).toHaveFocus();
    fireEvent.click(home);
    expect(onHome).toHaveBeenCalledOnce();
    expect(window.location.pathname).toBe("/");
  });

  it("keeps OpenAI and local runtime status in one compact group", () => {
    const view = render(<Header servers={capabilityCatalog.servers} reasoningMode="openai" onMenu={vi.fn()} />);
    const group = screen.getByRole("group", { name: "Runtime status" });
    expect(group).toHaveTextContent("OpenAI runtime");
    expect(group).toHaveTextContent("Local runtime");
    expect(screen.getByTestId("openai-runtime-indicator")).toHaveAttribute("data-runtime-state", "ready");
    expect(screen.getByLabelText("6/6 MCP servers")).toBeInTheDocument();

    view.rerender(<Header servers={capabilityCatalog.servers} reasoningMode="deterministic_demo" onMenu={vi.fn()} />);
    expect(screen.getByRole("group", { name: "Runtime status" })).toHaveTextContent("OpenAI unavailable");
    expect(screen.getByTestId("openai-runtime-indicator")).toHaveAttribute("data-runtime-state", "unavailable");
    view.rerender(<Header servers={capabilityCatalog.servers} reasoningMode="openai" onMenu={vi.fn()} />);
    expect(screen.getByTestId("openai-runtime-indicator")).toHaveAttribute("data-runtime-state", "ready");
  });

  it("shows a stable waking state without flashing fake demo or zero-server status", () => {
    render(
      <Header
        servers={[]}
        reasoningMode="unknown"
        readinessState="starting"
        publicDemoMode
        onMenu={vi.fn()}
      />,
    );

    expect(screen.getByText("Waking DayPilot…")).toBeInTheDocument();
    expect(screen.getByText("Connecting services…")).toBeInTheDocument();
    expect(screen.queryByText("Demo workspace")).not.toBeInTheDocument();
    expect(screen.queryByText(/0\/6 MCP servers/)).not.toBeInTheDocument();
  });

  it("marks Mail as used when search_mail succeeds without a thread read", () => {
    render(
      <ContextPanel
        context={{
          mail: [{
            tool_name: "search_mail",
            arguments: { query: "DayPilot interview test" },
            description: "Search mail",
            reason: "Find the matching email.",
            result: { threads: [{ thread_id: "thread-1" }], count: 1 },
            success: true,
            error: null,
          }],
          calendar: [{
            tool_name: "list_events",
            arguments: {},
            description: "Read calendar events",
            reason: "Check availability.",
            result: null,
            success: false,
            error: "Blocked",
          }],
          tasks: [],
          files: [],
          x: [],
        }}
      />,
    );

    expect(screen.getByText("1 thread")).toBeInTheDocument();
    expect(screen.getAllByText("Not queried").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /view details/i }));
    expect(screen.getByText("1 matching mail thread returned by search.")).toBeInTheDocument();
  });

  it("renders compact stable run titles with the original request preview", () => {
    const run = makeRun({
      id: "run-history",
      status: "completed",
      user_request: "Read my launch notes and tell me what recent X posts in the demo workspace say about MCP.",
    });
    const onSelect = vi.fn();
    const view = render(
      <SidebarHarness run={run} onSelect={onSelect} />,
    );
    expect(runDisplayTitle(run)).toBe("MCP launch research");
    expect(screen.getByTestId("history-title-run-history")).toHaveTextContent("MCP launch research");
    expect(screen.getByTestId("history-preview-run-history")).toHaveTextContent(run.user_request);
    expect(screen.getByTestId("history-preview-run-history").className).toContain("historyPreview");
    expect(screen.getByTestId("history-run-run-history")).toHaveTextContent(/Completed ·/);
    fireEvent.click(screen.getByTestId("history-run-run-history"));
    expect(onSelect).toHaveBeenCalledWith("run-history");

    view.rerender(<SidebarHarness run={run} onSelect={onSelect} />);
    expect(screen.getByTestId("history-title-run-history")).toHaveTextContent("MCP launch research");
    expect(runDisplayTitle({ user_request: "Find my latest resume." })).toBe("Resume lookup");
  });

  it("uses the larger wand-and-settings trigger without changing its click behavior", () => {
    const onPreferences = vi.fn();
    render(<SidebarHarness run={makeRun()} onSelect={vi.fn()} onPreferences={onPreferences} />);

    const trigger = screen.getByRole("button", { name: "Open preferences" });
    expect(trigger).toHaveAttribute("title", "Preferences");
    expect(trigger.className).toContain("preferencesTrigger");
    expect(trigger.querySelectorAll("svg")).toHaveLength(2);
    expect(screen.getByText("Preferences and settings and more")).toBeInTheDocument();
    fireEvent.click(trigger);
    expect(onPreferences).toHaveBeenCalledOnce();
  });

  it("renders read and write actions with approval controls", () => {
    const approve = vi.fn();
    render(
      <PlanPanel
        run={makeRun()}
        busy={false}
        onApprove={approve}
        onReject={vi.fn()}
        onEdit={vi.fn()}
      />,
    );
    expect(screen.getByText("Search interview mail")).toBeInTheDocument();
    expect(screen.getByText("Write")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /approve & execute/i }));
    expect(approve).toHaveBeenCalledOnce();
  });

  it("renders a direct general answer without an awkward empty-plan message", () => {
    render(
      <PlanPanel
        run={makeRun({
          status: "completed",
          approval_status: "not_required",
          plan: [],
          final_summary: "The capital of Lithuania is Vilnius.",
          intent: {
            goal: "What is the capital of Lithuania?",
            request_kind: "general",
            people: [],
            date_constraints: [],
            requested_outcomes: [],
            requested_operations: [],
            information_needed: [],
          },
        })}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={vi.fn()}
      />,
    );
    expect(screen.getByText("The capital of Lithuania is Vilnius.")).toBeInTheDocument();
    expect(screen.queryByText("No executable plan was produced for this run.")).not.toBeInTheDocument();
  });

  it("shows compact retained sources for a web-researched answer", () => {
    const webAction = {
      ...makeRun().plan[0],
      id: "web-read",
      server_name: "web",
      tool_name: "search_web",
      description: "Research a current public update",
    };
    render(
      <PlanPanel
        run={makeRun({
          status: "completed",
          approval_status: "not_required",
          plan: [webAction],
          final_summary: "A current public update was confirmed.",
          context: {
            web: [{
              tool_name: "search_web",
              arguments: { query: "current update" },
              description: "Research the web",
              reason: "Fresh public information is required.",
              result: {
                sources: [{ title: "Primary source", url: "https://example.com/update" }],
              },
              success: true,
              error: null,
            }],
            mail: [], calendar: [], tasks: [], files: [], x: [],
          },
        })}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Web research sources")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Primary source" })).toHaveAttribute(
      "href",
      "https://example.com/update",
    );
  });

  it("keeps demo reset and run-history clearing separate and guarded", () => {
    const reset = vi.fn();
    const clear = vi.fn();
    const preferences = {
      preferred_focus_block_minutes: 90,
      avoid_scheduling_after: "22:00",
      preferred_task_due_time: "18:00",
    };
    const view = render(
      <PreferencesDialog
        preferences={preferences}
        onClose={vi.fn()}
        onSave={vi.fn()}
        onResetDemoRequest={reset}
        onClearHistoryRequest={clear}
        maintenanceBlocked={false}
        maintenanceMessage={null}
        maintenanceBusy={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Reset demo workspace" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear run history" }));
    expect(reset).toHaveBeenCalledOnce();
    expect(clear).toHaveBeenCalledOnce();

    view.rerender(
      <PreferencesDialog
        preferences={preferences}
        onClose={vi.fn()}
        onSave={vi.fn()}
        onResetDemoRequest={reset}
        onClearHistoryRequest={clear}
        maintenanceBlocked
        maintenanceMessage="Finish or reject active runs first."
        maintenanceBusy={false}
      />,
    );
    expect(screen.getByText("Finish or reject active runs first.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset demo workspace" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Clear run history" })).toBeDisabled();

    view.rerender(
      <PreferencesDialog
        preferences={preferences}
        onClose={vi.fn()}
        onSave={vi.fn()}
        onResetDemoRequest={reset}
        onClearHistoryRequest={clear}
        maintenanceBlocked={false}
        maintenanceMessage="Finish or reject active or approval-required runs before changing demo data or clearing history."
        maintenanceBusy={false}
      />,
    );
    expect(screen.getByRole("button", { name: "Reset demo workspace" })).not.toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Reset demo workspace" }));
    expect(reset).toHaveBeenCalledTimes(2);
  });

  it("requires explicit confirmation for destructive maintenance actions", () => {
    const cancel = vi.fn();
    const confirm = vi.fn();
    render(
      <ConfirmationDialog
        title="Reset demo workspace?"
        body="Restore the seeded demo services."
        confirmLabel="Reset workspace"
        busy={false}
        error={null}
        onCancel={cancel}
        onConfirm={confirm}
      />,
    );

    expect(screen.getByRole("dialog")).toHaveTextContent("Restore the seeded demo services.");
    fireEvent.click(screen.getByRole("button", { name: "Reset workspace" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("shows provider provenance and connected controls without exposing credentials", async () => {
    const catalog = {
      demo_mode: false,
      connections: [
        {
          service: "mail" as const,
          provider: "Gmail",
          state: "disconnected" as const,
          account_label: "alex@example.com",
          capabilities: ["search_mail"],
          last_error: null,
          requires_reauth: false,
          metadata: { mode: "gmail" },
        },
        {
          service: "calendar" as const,
          provider: "Google Calendar",
          state: "disconnected" as const,
          account_label: "alex@example.com",
          capabilities: ["list_events"],
          last_error: null,
          requires_reauth: false,
          metadata: { mode: "google_calendar" },
        },
        {
          service: "tasks" as const,
          provider: "Google Tasks",
          state: "disconnected" as const,
          account_label: "alex@example.com",
          capabilities: ["list_tasks"],
          last_error: null,
          requires_reauth: false,
          metadata: { mode: "google_tasks" },
        },
        {
          service: "files" as const,
          provider: "Local Mac",
          state: "disconnected" as const,
          account_label: null,
          capabilities: ["read_file"],
          last_error: null,
          requires_reauth: false,
          metadata: { mode: "local" },
        },
        {
          service: "x" as const,
          provider: "X",
          state: "disconnected" as const,
          account_label: null,
          capabilities: ["search_posts"],
          last_error: null,
          requires_reauth: false,
          metadata: { mode: "x_api" },
        },
      ],
    };
    const connectGoogle = vi.fn(async () => {});
    render(
      <ConnectionSettings
        catalog={catalog}
        fileRoots={[]}
        onConnectGoogle={connectGoogle}
        onDisconnectGoogle={vi.fn(async () => {})}
        onConnectX={vi.fn(async () => {})}
        onDisconnectX={vi.fn(async () => {})}
        onAddFileRoot={vi.fn(async () => {})}
        onRemoveFileRoot={vi.fn(async () => {})}
      />,
    );
    expect(screen.getByText(/Mail · Calendar · Tasks/)).toHaveTextContent("alex@example.com");
    expect(screen.getByRole("button", { name: "Connect Google" })).toBeInTheDocument();
    expect(screen.queryByText("google-access-token")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Connect Google" }));
    expect(connectGoogle).toHaveBeenCalledOnce();
  });

  it("shows X managed-auth unavailability without exposing a broken connect action", () => {
    const catalog = {
      demo_mode: false,
      connections: [
        ...capabilityCatalog.servers.slice(0, 3).map((server, index) => ({
          service: (["mail", "calendar", "tasks"] as const)[index],
          provider: "Google Workspace",
          state: "disconnected" as const,
          account_label: null,
          capabilities: server.tools,
          last_error: "Connect Google Workspace through Composio to use this capability.",
          requires_reauth: false,
          metadata: { mode: "managed", toolkit: "googlesuper" },
          connection_mode: "managed" as const,
        })),
        {
          service: "files" as const, provider: "Local Mac", state: "disconnected" as const,
          account_label: "0 folders", capabilities: [], last_error: null, requires_reauth: false,
          metadata: { mode: "local" }, connection_mode: "local" as const,
        },
        {
          service: "x" as const, provider: "X", state: "unavailable" as const,
          account_label: null, capabilities: [],
          last_error: "Managed connection is currently unavailable for X.", requires_reauth: false,
          metadata: { mode: "managed", toolkit: "twitter" }, connection_mode: "managed" as const,
        },
      ],
    };
    render(
      <ConnectionSettings
        catalog={catalog}
        fileRoots={[]}
        onConnectGoogle={vi.fn(async () => {})}
        onDisconnectGoogle={vi.fn(async () => {})}
        onConnectX={vi.fn(async () => {})}
        onDisconnectX={vi.fn(async () => {})}
        onAddFileRoot={vi.fn(async () => {})}
        onRemoveFileRoot={vi.fn(async () => {})}
      />,
    );
    expect(screen.getByText("Managed connection unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect Google" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect X" })).not.toBeInTheDocument();
    expect(screen.queryByText(/ToolRouterV2|auth configs|request id/i)).not.toBeInTheDocument();
  });

  it("keeps personal connection actions behind the public admin gate", () => {
    const catalog = {
      demo_mode: false,
      connections: [
        {
          service: "mail" as const,
          provider: "Google Workspace",
          state: "unavailable" as const,
          account_label: null,
          capabilities: [],
          last_error: "Available to admin only.",
          requires_reauth: false,
          metadata: {},
          connection_mode: "managed" as const,
        },
      ],
    };
    render(
      <ConnectionSettings
        catalog={catalog}
        fileRoots={[]}
        publicDemoMode
        onConnectGoogle={vi.fn(async () => {})}
        onDisconnectGoogle={vi.fn(async () => {})}
        onConnectX={vi.fn(async () => {})}
        onDisconnectX={vi.fn(async () => {})}
        onAddFileRoot={vi.fn(async () => {})}
        onRemoveFileRoot={vi.fn(async () => {})}
      />,
    );
    expect(screen.getAllByText("Available to admin only").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Connect Google" })).not.toBeInTheDocument();
  });

  it("offers a small admin unlock entry point in public mode", async () => {
    const onAdminLogin = vi.fn(async () => {});
    render(
      <PreferencesDialog
        preferences={{ preferred_focus_block_minutes: 90, avoid_scheduling_after: "22:00", preferred_task_due_time: "18:00" }}
        onClose={vi.fn()}
        onSave={vi.fn()}
        onResetDemoRequest={vi.fn()}
        onClearHistoryRequest={vi.fn()}
        maintenanceBlocked={false}
        maintenanceMessage={null}
        maintenanceBusy={false}
        adminStatus={{ authenticated: false, public_demo_mode: true, expires_at: null, message: "Public demo mode." }}
        onAdminLogin={onAdminLogin}
        connections={{ demo_mode: false, connections: [] }}
      />,
    );
    const code = screen.getByLabelText("Admin access code");
    await userEvent.type(code, "owner-code");
    await userEvent.click(screen.getByRole("button", { name: "Unlock" }));
    expect(onAdminLogin).toHaveBeenCalledWith("owner-code");
  });

  it("shows and executes the explicit admin lock control", async () => {
    const onAdminLogout = vi.fn(async () => {});
    render(
      <PreferencesDialog
        preferences={{ preferred_focus_block_minutes: 90, avoid_scheduling_after: "22:00", preferred_task_due_time: "18:00" }}
        onClose={vi.fn()}
        onSave={vi.fn()}
        onResetDemoRequest={vi.fn()}
        onClearHistoryRequest={vi.fn()}
        maintenanceBlocked={false}
        maintenanceMessage={null}
        maintenanceBusy={false}
        adminStatus={{ authenticated: true, public_demo_mode: true, expires_at: null, message: "Admin mode enabled." }}
        onAdminLogout={onAdminLogout}
        connections={{ demo_mode: false, connections: [] }}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Lock admin mode" }));
    expect(onAdminLogout).toHaveBeenCalledOnce();
  });

  it("renders a grounded demo calendar receipt and opens its stored details", async () => {
    render(
      <PlanPanel
        run={makeRun({
          status: "completed",
          final_summary: "Your study block was scheduled.",
          created_outputs: [
            {
              action_id: "calendar-1",
              resource_type: "calendar_event",
              provider: "Calendar · DayPilot demo",
              resource_id: "event-demo-123",
              title: "Study block",
              secondary_text: "Sun, Aug 30 · 8:00 PM–9:30 PM",
              status: "verified",
              verified: true,
              verification_detail: "Persisted state confirmed by an MCP read tool.",
              external_url: null,
              items: [],
              details: [
                { label: "Title", value: "Study block" },
                { label: "When", value: "Sun, Aug 30 · 8:00 PM–9:30 PM" },
              ],
              error: null,
            },
          ],
        })}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Created resource" })).toBeInTheDocument();
    expect(screen.getByText("Study block")).toBeInTheDocument();
    expect(screen.getByText("Sun, Aug 30 · 8:00 PM–9:30 PM")).toBeInTheDocument();
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText("Your study block was scheduled.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /view event/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "View event" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("event-demo-123");
    expect(screen.getByRole("dialog")).toHaveTextContent("Calendar · DayPilot demo");
    fireEvent.click(screen.getByRole("button", { name: "Close output details" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps multiple output receipts compact and only links real provider URLs", () => {
    render(
      <CreatedOutputs
        outputs={[
          {
            action_id: "calendar-1",
            resource_type: "calendar_event",
            provider: "Google Calendar",
            resource_id: "google-event-1",
            title: "Study block",
            secondary_text: "Today · 8:00 PM–9:30 PM",
            status: "verified",
            verified: true,
            verification_detail: null,
            external_url: "https://calendar.google.com/event/1",
            items: [],
            details: [],
            error: null,
          },
          {
            action_id: "tasks-1",
            resource_type: "task_batch",
            provider: "Tasks · DayPilot demo",
            resource_id: null,
            title: "Created 2 tasks",
            secondary_text: null,
            status: "verified",
            verified: true,
            verification_detail: null,
            external_url: null,
            items: [
              { resource_id: "task-1", title: "Review notes", secondary_text: null },
              { resource_id: "task-2", title: "Prepare questions", secondary_text: null },
            ],
            details: [],
            error: null,
          },
          {
            action_id: "mail-1",
            resource_type: "mail_draft",
            provider: "Mail",
            resource_id: null,
            title: "Draft not created",
            secondary_text: null,
            status: "failed",
            verified: false,
            verification_detail: null,
            external_url: null,
            items: [],
            details: [],
            error: "Mail service unavailable",
          },
        ]}
      />,
    );

    expect(screen.getByText("Created 2 tasks")).toBeInTheDocument();
    expect(screen.getByText("Review notes")).toBeInTheDocument();
    expect(screen.getByText("Prepare questions")).toBeInTheDocument();
    expect(screen.getByText("Mail service unavailable")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view event/i })).toHaveAttribute(
      "href",
      "https://calendar.google.com/event/1",
    );
    expect(screen.getByRole("link", { name: /view event/i })).toHaveAttribute("target", "_blank");
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("never presents an empty task batch as verified success", () => {
    render(
      <CreatedOutputs
        outputs={[{
          action_id: "tasks-empty",
          resource_type: "task_batch",
          provider: "Google Tasks",
          resource_id: null,
          title: "Created 0 tasks",
          secondary_text: null,
          status: "verified",
          verified: true,
          verification_detail: "Persisted state confirmed.",
          external_url: null,
          items: [],
          details: [{ label: "Count", value: "0" }],
          error: null,
        }]}
      />,
    );

    expect(screen.getByText("No tasks created")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("The provider returned no created task resources.")).toBeInTheDocument();
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
  });

  it("renders semantic approval state in the execution timeline", () => {
    render(<TimelinePanel events={[makeEvent()]} />);
    expect(screen.getAllByText("Waiting for human approval").length).toBeGreaterThan(0);
    expect(screen.getByText(/3 external changes are blocked/i)).toBeInTheDocument();
    expect(screen.getByTestId("timeline-scroll").querySelector("[data-current-step]")).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(screen.queryByTestId("running-trail")).not.toBeInTheDocument();
    expect(screen.getByText("Approval")).toBeInTheDocument();
  });

  it("renders a branching dependency graph with exact edge and write semantics", () => {
    const actions = dependencyActions();
    render(
      <PlanDependencyGraph
        actions={actions}
        runStatus="waiting_approval"
        selectedActionId={null}
        onSelectAction={vi.fn()}
      />,
    );

    expect(screen.getAllByTestId("dependency-edge")).toHaveLength(3);
    expect(screen.getByTestId("graph-node-event").className).toContain("graphNodeWrite");
    expect(screen.getByTestId("graph-node-task").className).toContain("graphNodeApproval");
    expect(screen.getByTestId("graph-node-thread")).toHaveAttribute("data-action-id", "thread");
    expect(
      screen.getAllByLabelText(/Create task.*Depends on Read grounded thread/i).length,
    ).toBeGreaterThan(0);
  });

  it("keeps independent graph nodes free of decorative dependency edges", () => {
    const actions = dependencyActions().slice(0, 2).map((action) => ({
      ...action,
      depends_on: [],
    }));
    render(
      <PlanDependencyGraph
        actions={actions}
        runStatus="completed"
        selectedActionId={null}
        onSelectAction={vi.fn()}
      />,
    );
    expect(screen.queryAllByTestId("dependency-edge")).toHaveLength(0);
    expect(screen.getAllByText("Starts independently").length).toBeGreaterThan(0);
  });

  it("synchronizes dependency-node identity with the Sequence and Dependencies views", async () => {
    const actions = dependencyActions();
    render(
      <PlanPanel
        run={makeRun({ plan: actions })}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={vi.fn()}
      />,
    );

    expect(screen.getByRole("group", { name: "Plan dependency graph" })).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("graph-node-task"));
    expect(screen.getByTestId("graph-node-task").className).toContain("graphNodeSelected");
    await userEvent.click(screen.getByRole("button", { name: /sequence/i }));
    expect(document.querySelector('[data-action-id="task"]')).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: /dependencies/i }));
    expect(screen.getByTestId("graph-node-task")).toBeInTheDocument();
  });

  it("shows a meaningful completed-run dependency graph outside Executed actions", () => {
    render(
      <PlanPanel
        run={completedDependencyRun()}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "How the actions connect" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Plan dependency graph" })).toBeInTheDocument();
    expect(screen.getByTestId("graph-node-event")).toBeInTheDocument();
    const executed = screen.getByText("Executed actions").closest("details");
    expect(executed).not.toHaveAttribute("open");
  });

  it("keeps the completed-run graph visible when Executed actions is collapsed", async () => {
    render(
      <PlanPanel
        run={completedDependencyRun()}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={vi.fn()}
      />,
    );

    const executedSummary = screen.getByText("Executed actions");
    fireEvent.click(executedSummary);
    await waitFor(() => expect(executedSummary.closest("details")).toHaveAttribute("open"));
    fireEvent.click(executedSummary);
    await waitFor(() => expect(executedSummary.closest("details")).not.toHaveAttribute("open"));
    expect(screen.getByRole("group", { name: "Plan dependency graph" })).toBeInTheDocument();
    expect(screen.getByTestId("graph-node-task")).toBeInTheDocument();
  });

  it("defaults reopened completed plans to Dependencies while keeping Sequence available", async () => {
    const view = render(
      <PlanPanel
        run={completedDependencyRun()}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Dependencies" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Sequence" }));
    expect(screen.queryByRole("group", { name: "Plan dependency graph" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("graph-node-task")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Dependencies" }));
    expect(screen.getByRole("group", { name: "Plan dependency graph" })).toBeInTheDocument();

    view.unmount();
    render(
      <PlanPanel
        run={completedDependencyRun({ id: "reopened-completed-run" })}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={vi.fn()}
      />,
    );
    expect(screen.getByRole("group", { name: "Plan dependency graph" })).toBeInTheDocument();
  });

  it("keeps trivial and dependency-free historical plans in the compact sequence disclosure", () => {
    const trivial = render(
      <PlanPanel
        run={completedDependencyRun({ id: "trivial-completed", plan: dependencyActions().slice(0, 1) })}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={vi.fn()}
      />,
    );
    expect(screen.queryByRole("heading", { name: "How the actions connect" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Plan dependency graph" })).not.toBeInTheDocument();

    trivial.unmount();
    render(
      <PlanPanel
        run={completedDependencyRun({
          id: "legacy-completed",
          plan: dependencyActions().map((action) => ({ ...action, depends_on: [] })),
        })}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={vi.fn()}
      />,
    );
    expect(screen.queryByRole("heading", { name: "How the actions connect" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Plan dependency graph" })).not.toBeInTheDocument();
    expect(screen.getByText("Executed actions")).toBeInTheDocument();
  });

  it("does not render an empty graph when a run failed before plan generation", () => {
    render(
      <PlanPanel
        run={completedDependencyRun({ id: "failed-before-plan", status: "failed", plan: [], error: "The run failed before a plan was generated." })}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={vi.fn()}
      />,
    );
    expect(screen.getByText("No executable plan was produced for this run.")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Plan dependency graph" })).not.toBeInTheDocument();
  });

  it("falls back safely when dependency data is invalid", () => {
    const actions = dependencyActions().slice(0, 2);
    actions[1] = { ...actions[1], depends_on: ["missing-action"] };
    render(
      <PlanDependencyGraph
        actions={actions}
        runStatus="running"
        selectedActionId={null}
        onSelectAction={vi.fn()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Dependency view unavailable");
    expect(screen.queryByTestId("dependency-edge")).not.toBeInTheDocument();
  });

  it("keeps a one-action plan in the simple sequence view", () => {
    render(
      <PlanPanel
        run={makeRun({ plan: dependencyActions().slice(0, 1) })}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={vi.fn()}
      />,
    );
    expect(screen.queryByRole("group", { name: "Plan view" })).not.toBeInTheDocument();
    expect(screen.getByText("Search interview mail")).toBeInTheDocument();
  });

  it("shows the rail-integrated running trail only for a genuinely live run", () => {
    const running = render(
      <TimelinePanel runId="run-trail" runStatus="running" events={timelineEvents(4)} />,
    );
    expect(screen.getByTestId("running-trail")).toBeInTheDocument();
    expect(screen.getAllByText("Workflow step 4", { selector: "strong" }).length).toBeGreaterThan(0);

    running.rerender(
      <TimelinePanel runId="run-trail" runStatus="completed" events={timelineEvents(4)} />,
    );
    expect(screen.queryByTestId("running-trail")).not.toBeInTheDocument();
    expect(screen.getByText("Complete")).toBeInTheDocument();
  });

  it("follows new live events while the user stays at the latest step", () => {
    const scrollTo = mockTimelineScroll();
    const view = render(
      <TimelinePanel runId="run-live" runStatus="running" events={timelineEvents(2)} />,
    );
    const initialCalls = scrollTo.mock.calls.length;
    expect(initialCalls).toBeGreaterThan(0);
    expect(screen.getByText("Running")).toBeInTheDocument();

    view.rerender(
      <TimelinePanel runId="run-live" runStatus="running" events={timelineEvents(3)} />,
    );
    expect(scrollTo.mock.calls.length).toBeGreaterThan(initialCalls);
    const afterFirstEvent = scrollTo.mock.calls.length;

    view.rerender(
      <TimelinePanel runId="run-live" runStatus="running" events={timelineEvents(4)} />,
    );
    expect(scrollTo.mock.calls.length).toBeGreaterThan(afterFirstEvent);
    expect(screen.getByTestId("timeline-scroll")).toHaveAttribute("data-follow-live", "true");
  });

  it("respects history inspection and restores follow mode with Jump to latest", () => {
    const scrollTo = mockTimelineScroll();
    const view = render(
      <TimelinePanel runId="run-history" runStatus="running" events={timelineEvents(5)} />,
    );
    const timeline = screen.getByTestId("timeline-scroll");
    setTimelineMetrics(timeline, { scrollTop: 120, scrollHeight: 1_000, clientHeight: 300 });
    fireEvent.wheel(timeline);
    fireEvent.scroll(timeline);

    expect(screen.getByRole("button", { name: /follow live/i })).toBeInTheDocument();
    expect(timeline).toHaveAttribute("data-follow-live", "false");
    const callsBeforeNewEvent = scrollTo.mock.calls.length;

    view.rerender(
      <TimelinePanel runId="run-history" runStatus="running" events={timelineEvents(6)} />,
    );
    expect(scrollTo).toHaveBeenCalledTimes(callsBeforeNewEvent);
    expect(screen.getByRole("button", { name: /follow live/i })).toHaveTextContent("1 new");

    fireEvent.click(screen.getByRole("button", { name: /follow live/i }));
    expect(scrollTo.mock.calls.length).toBeGreaterThan(callsBeforeNewEvent);
    expect(screen.queryByRole("button", { name: /follow live/i })).not.toBeInTheDocument();
    expect(timeline).toHaveAttribute("data-follow-live", "true");
  });

  it("re-enables follow mode when the user returns near the bottom", () => {
    mockTimelineScroll();
    render(<TimelinePanel runId="run-near-bottom" runStatus="running" events={timelineEvents(5)} />);
    const timeline = screen.getByTestId("timeline-scroll");
    setTimelineMetrics(timeline, { scrollTop: 100, scrollHeight: 1_000, clientHeight: 300 });
    fireEvent.wheel(timeline);
    fireEvent.scroll(timeline);
    expect(screen.getByRole("button", { name: /follow live/i })).toBeInTheDocument();

    setTimelineMetrics(timeline, { scrollTop: 650, scrollHeight: 1_000, clientHeight: 300 });
    fireEvent.scroll(timeline);
    expect(screen.queryByRole("button", { name: /follow live/i })).not.toBeInTheDocument();
    expect(timeline).toHaveAttribute("data-follow-live", "true");
  });

  it("does not auto-scroll completed runs and initializes a switched live run at latest", () => {
    const scrollTo = mockTimelineScroll();
    const view = render(
      <TimelinePanel runId="run-complete" runStatus="completed" events={timelineEvents(4)} />,
    );
    expect(scrollTo).not.toHaveBeenCalled();

    view.rerender(
      <TimelinePanel runId="run-complete" runStatus="completed" events={timelineEvents(5)} />,
    );
    expect(scrollTo).not.toHaveBeenCalled();

    view.rerender(
      <TimelinePanel runId="run-next" runStatus="running" events={timelineEvents(2)} />,
    );
    expect(scrollTo).toHaveBeenCalled();
  });

  it("uses non-animated timeline scrolling when reduced motion is requested", () => {
    const scrollTo = mockTimelineScroll();
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));
    render(<TimelinePanel runId="run-reduced" runStatus="running" events={timelineEvents(5)} />);
    const timeline = screen.getByTestId("timeline-scroll");
    setTimelineMetrics(timeline, { scrollTop: 100, scrollHeight: 1_000, clientHeight: 300 });
    fireEvent.wheel(timeline);
    fireEvent.scroll(timeline);
    fireEvent.click(screen.getByRole("button", { name: /follow live/i }));

    expect(scrollTo.mock.calls.at(-1)?.[0]).toMatchObject({ behavior: "auto" });
    vi.unstubAllGlobals();
  });

  it("expands discovered MCP tools and shows risk classifications", () => {
    render(<ToolInspector catalog={toolCatalog} />);
    fireEvent.click(screen.getByRole("button", { name: /mail mcp/i }));
    expect(screen.getByText("search_mail")).toBeInTheDocument();
    expect(screen.getByText("create_draft")).toBeInTheDocument();
    expect(screen.getByText("Read")).toBeInTheDocument();
    expect(screen.getByText("Write")).toBeInTheDocument();
  });

  it("replaces the old onboarding copy with real capability cards and catalog counts", () => {
    render(<CapabilityStrip catalog={capabilityCatalog} selectedServer={null} onSelect={vi.fn()} />);

    expect(screen.queryByText("A calmer path from goal to action")).not.toBeInTheDocument();
    expect(screen.getByText("Mail")).toBeInTheDocument();
    expect(screen.getByText("Calendar")).toBeInTheDocument();
    expect(screen.getByText("Tasks")).toBeInTheDocument();
    expect(screen.getByText("Files")).toBeInTheDocument();
    expect(screen.getByText("X")).toBeInTheDocument();
    expect(screen.getByTestId("capability-card-web")).toHaveTextContent("1 tool");
    expect(screen.getByTestId("capability-card-mail")).toHaveTextContent("4 tools");
    expect(screen.getByTestId("capability-card-calendar")).toHaveTextContent("3 tools");
    expect(screen.getByTestId("capability-card-tasks")).toHaveTextContent("4 tools");
    expect(screen.getByTestId("capability-card-files")).toHaveTextContent("4 tools");
    expect(screen.getByTestId("capability-card-x")).toHaveTextContent("5 tools");
  });

  it("opens and selects the matching MCP details from each capability card", async () => {
    const user = userEvent.setup();
    render(<CapabilityHarness />);
    expect(screen.getByText("6 MCP servers · 21 tools")).toBeInTheDocument();

    await user.click(screen.getByTestId("capability-card-mail"));
    expect(screen.getByRole("button", { name: /collapse connected capabilities/i })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /mail mcp/i })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("search_mail")).toBeInTheDocument();
    expect(screen.getByTestId("capability-card-mail")).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByTestId("capability-card-calendar"));
    expect(screen.getByRole("button", { name: /calendar mcp/i })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("list_events")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /mail mcp/i })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("capability-card-calendar")).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByTestId("capability-card-tasks"));
    expect(screen.getByRole("button", { name: /tasks mcp/i })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("list_tasks")).toBeInTheDocument();
    expect(screen.getByTestId("capability-card-tasks")).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByTestId("capability-card-files"));
    expect(screen.getByRole("button", { name: /files mcp/i })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("search_files")).toBeInTheDocument();
    expect(screen.getByTestId("capability-card-files")).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByTestId("capability-card-x"));
    expect(screen.getByRole("button", { name: /^x mcp/i })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("search_posts")).toBeInTheDocument();
    expect(screen.getByTestId("capability-card-x")).toHaveAttribute("aria-pressed", "true");
  });

  it("supports keyboard activation and keeps inspector/card selection in sync", async () => {
    const user = userEvent.setup();
    render(<CapabilityHarness />);
    const calendarCard = screen.getByTestId("capability-card-calendar");

    calendarCard.focus();
    await user.keyboard("{Enter}");
    expect(calendarCard).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /calendar mcp/i })).toHaveAttribute("aria-expanded", "true");

    await user.click(screen.getByRole("button", { name: /collapse connected capabilities/i }));
    expect(screen.getByRole("button", { name: /expand connected capabilities/i })).toHaveAttribute("aria-expanded", "false");
    expect(calendarCard).toHaveAttribute("aria-pressed", "false");
  });

  it("keeps the standalone inspector expandable and collapsible", async () => {
    const user = userEvent.setup();
    render(<ToolInspector catalog={capabilityCatalog} collapsible />);
    const toggle = screen.getByRole("button", { name: /expand connected capabilities/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(screen.getByRole("button", { name: /collapse connected capabilities/i })).toHaveAttribute("aria-expanded", "true");
    await user.click(screen.getByRole("button", { name: /collapse connected capabilities/i }));
    expect(screen.getByRole("button", { name: /expand connected capabilities/i })).toHaveAttribute("aria-expanded", "false");
  });

  it("shows a pending revision state and replaces the old plan", async () => {
    const onEdit = vi.fn();
    const initial = makeRun();
    const { rerender } = render(
      <PlanPanel
        key={initial.plan_revision}
        run={initial}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={onEdit}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /edit plan/i }));
    fireEvent.change(screen.getByLabelText("Plan feedback"), {
      target: { value: "Create exactly two tasks" },
    });
    fireEvent.click(screen.getByRole("button", { name: /revise plan/i }));
    expect(onEdit).toHaveBeenCalledWith("Create exactly two tasks");

    rerender(
      <PlanPanel
        key={initial.plan_revision}
        run={initial}
        busy
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={onEdit}
      />,
    );
    expect(screen.getByRole("button", { name: "Revising plan…" })).toBeDisabled();

    const revised = makeRun({
      plan_revision: 2,
      plan_hash: "plan-hash-2",
      plan: [
        {
          ...initial.plan[1],
          id: "write-revised",
          description: "Create exactly two preparation tasks",
          tool_name: "create_task_batch",
          server_name: "tasks",
        },
      ],
    });
    rerender(
      <PlanPanel
        key={revised.plan_revision}
        run={revised}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={onEdit}
      />,
    );
    await waitFor(() => expect(screen.getByText("Revision 2")).toBeInTheDocument());
    expect(screen.getAllByText("Create exactly two preparation tasks").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /approve & execute/i })).toBeInTheDocument();
  });

  it("sends feedback with the plan revision and returns revised state", async () => {
    const revised = makeRun({ plan_revision: 2, plan_hash: "plan-hash-2" });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => revised,
    });
    vi.stubGlobal("fetch", fetchMock);
    const result = await editRun("run-test", "Create exactly two tasks", 1);
    expect(result.plan_revision).toBe(2);
    const [, options] = fetchMock.mock.calls[0];
    expect(JSON.parse(options.body)).toEqual({
      feedback: "Create exactly two tasks",
      plan_revision: 1,
    });
    vi.unstubAllGlobals();
  });

  it("loads enough run history to keep older pending approvals reachable", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => [] });
    vi.stubGlobal("fetch", fetchMock);

    await listRuns();

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/runs?limit=100"),
      expect.any(Object),
    );
    vi.unstubAllGlobals();
  });

  it("starts with an empty request and a Simon placeholder", () => {
    render(<RequestComposer onSubmit={vi.fn()} busy={false} />);
    const input = screen.getByRole("textbox", { name: "Goal" });
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("placeholder", "Prepare me for my interview with Simon tomorrow.");
    expect(screen.queryByText("Interview preparation")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try a demo/i })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "Start DayPilot run" })).toBeDisabled();
  });

  it("disables every composer entry point while runtime initialization is unfinished", () => {
    render(<RequestComposer onSubmit={vi.fn()} busy={false} disabled />);

    expect(screen.getByRole("textbox", { name: "Goal" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Start DayPilot run" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /try a demo/i })).toBeDisabled();
  });

  it("uses the workspace-focused two-line headline with a stable rotating slot", () => {
    render(<RequestComposer onSubmit={vi.fn()} busy={false} />);
    const heading = screen.getByRole("heading", { level: 1 });
    const rotatingSlot = screen.getByTestId("rotating-action").parentElement;

    expect(heading).toHaveTextContent("An MCP agent that");
    expect(heading).toHaveTextContent("across your workspace");
    expect(rotatingSlot?.querySelectorAll('[aria-hidden="true"]')).toHaveLength(HERO_WORDS.length);
  });

  it("opens the demo picker, selects a prompt without submitting, and closes on Escape", () => {
    const onSubmit = vi.fn();
    render(<RequestComposer onSubmit={onSubmit} busy={false} />);
    const trigger = screen.getByRole("button", { name: /try a demo/i });
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("menu", { name: "Demo prompts" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitemradio", { name: /focus block/i }));
    expect(screen.getByRole("textbox", { name: "Goal" })).toHaveValue("Find a free 90-minute focus block tonight and schedule it.");
    expect(onSubmit).not.toHaveBeenCalled();
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(trigger);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu", { name: "Demo prompts" })).not.toBeInTheDocument();
  });

  it("closes the demo picker on outside pointer and keeps whitespace disabled", () => {
    render(<RequestComposer onSubmit={vi.fn()} busy={false} />);
    const trigger = screen.getByRole("button", { name: /try a demo/i });
    fireEvent.click(trigger);
    expect(screen.getByRole("menu", { name: "Demo prompts" })).toBeInTheDocument();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("menu", { name: "Demo prompts" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Goal" }), { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "Start DayPilot run" })).toBeDisabled();
  });

  it("runs one deterministic rotating-word progression", () => {
    vi.useFakeTimers();
    render(<RequestComposer onSubmit={vi.fn()} busy={false} />);
    const word = screen.getByTestId("rotating-action");

    expect(word).toHaveTextContent("organizes");
    expect(word).toHaveAttribute("data-phase", "settled");
    expect(vi.getTimerCount()).toBe(1);

    act(() => vi.advanceTimersByTime(HERO_MOTION.idleDwellMs));
    expect(word).toHaveTextContent("organizes");
    expect(word).toHaveAttribute("data-phase", "exiting");
    expect(vi.getTimerCount()).toBe(1);

    act(() => vi.advanceTimersByTime(HERO_MOTION.transitionMs));
    expect(word).toHaveTextContent("schedules");
    expect(word).toHaveAttribute("data-phase", "entering");
    expect(vi.getTimerCount()).toBe(1);

    act(() => vi.advanceTimersByTime(HERO_MOTION.transitionMs));
    expect(word).toHaveAttribute("data-phase", "settled");
    expect(vi.getTimerCount()).toBe(1);
  });

  it("switches to one interactive timer without hover duplication", () => {
    vi.useFakeTimers();
    render(<RequestComposer onSubmit={vi.fn()} busy={false} />);
    const composer = screen.getByTestId("request-composer");
    const word = screen.getByTestId("rotating-action");

    fireEvent.pointerEnter(composer);
    expect(vi.getTimerCount()).toBe(1);
    act(() => vi.advanceTimersByTime(HERO_MOTION.interactiveDwellMs - 1));
    expect(word).toHaveAttribute("data-phase", "settled");
    act(() => vi.advanceTimersByTime(1));
    expect(word).toHaveAttribute("data-phase", "exiting");
    expect(vi.getTimerCount()).toBe(1);
  });

  it("does not double-advance when the composer is focused and hovered", () => {
    vi.useFakeTimers();
    render(<RequestComposer onSubmit={vi.fn()} busy={false} />);
    const composer = screen.getByTestId("request-composer");
    const input = screen.getByRole("textbox", { name: "Goal" });
    const word = screen.getByTestId("rotating-action");

    fireEvent.pointerEnter(composer);
    fireEvent.focus(input);
    expect(vi.getTimerCount()).toBe(1);
    act(() => vi.advanceTimersByTime(HERO_MOTION.interactiveDwellMs));
    act(() => vi.advanceTimersByTime(HERO_MOTION.transitionMs));
    expect(word).toHaveTextContent("schedules");
    expect(vi.getTimerCount()).toBe(1);
  });

  it("stays interactive while either focus or hover remains", () => {
    vi.useFakeTimers();
    const focusedRender = render(<RequestComposer onSubmit={vi.fn()} busy={false} />);
    const focusedComposer = screen.getByTestId("request-composer");
    const focusedInput = screen.getByRole("textbox", { name: "Goal" });
    fireEvent.pointerEnter(focusedComposer);
    fireEvent.focus(focusedInput);
    fireEvent.pointerLeave(focusedComposer);
    act(() => vi.advanceTimersByTime(HERO_MOTION.interactiveDwellMs));
    expect(screen.getByTestId("rotating-action")).toHaveAttribute("data-phase", "exiting");
    expect(vi.getTimerCount()).toBe(1);
    focusedRender.unmount();

    render(<RequestComposer onSubmit={vi.fn()} busy={false} />);
    const hoveredComposer = screen.getByTestId("request-composer");
    const hoveredInput = screen.getByRole("textbox", { name: "Goal" });
    fireEvent.pointerEnter(hoveredComposer);
    fireEvent.focus(hoveredInput);
    fireEvent.blur(hoveredInput);
    act(() => vi.advanceTimersByTime(HERO_MOTION.interactiveDwellMs));
    expect(screen.getByTestId("rotating-action")).toHaveAttribute("data-phase", "exiting");
    expect(vi.getTimerCount()).toBe(1);
  });

  it("restores idle cadence after repeated hover changes", () => {
    vi.useFakeTimers();
    render(<RequestComposer onSubmit={vi.fn()} busy={false} />);
    const composer = screen.getByTestId("request-composer");
    const word = screen.getByTestId("rotating-action");

    for (let index = 0; index < 4; index += 1) {
      fireEvent.pointerEnter(composer);
      fireEvent.pointerLeave(composer);
    }
    expect(vi.getTimerCount()).toBe(1);
    act(() => vi.advanceTimersByTime(HERO_MOTION.idleDwellMs - 1));
    expect(word).toHaveAttribute("data-phase", "settled");
    act(() => vi.advanceTimersByTime(1));
    expect(word).toHaveAttribute("data-phase", "exiting");
  });

  it("cleans the scheduler on unmount and creates only one on remount", () => {
    vi.useFakeTimers();
    const first = render(<RequestComposer onSubmit={vi.fn()} busy={false} />);
    expect(vi.getTimerCount()).toBe(1);
    first.unmount();
    expect(vi.getTimerCount()).toBe(0);

    render(<RequestComposer onSubmit={vi.fn()} busy={false} />);
    expect(vi.getTimerCount()).toBe(1);
    act(() => vi.advanceTimersByTime(HERO_MOTION.idleDwellMs));
    expect(screen.getByTestId("rotating-action")).toHaveAttribute("data-phase", "exiting");
    expect(vi.getTimerCount()).toBe(1);
  });

  it("restores normal cadence when interaction ends", () => {
    vi.useFakeTimers();
    render(<RequestComposer onSubmit={vi.fn()} busy={false} />);
    const composer = screen.getByTestId("request-composer");
    const word = screen.getByTestId("rotating-action");

    fireEvent.pointerEnter(composer);
    act(() => vi.advanceTimersByTime(HERO_MOTION.interactiveDwellMs));
    act(() => vi.advanceTimersByTime(HERO_MOTION.transitionMs * 2));
    expect(word).toHaveTextContent("schedules");
    expect(word).toHaveAttribute("data-phase", "settled");

    fireEvent.pointerLeave(composer);
    act(() => vi.advanceTimersByTime(HERO_MOTION.idleDwellMs - 1));
    expect(word).toHaveAttribute("data-phase", "settled");
    act(() => vi.advanceTimersByTime(1));
    expect(word).toHaveAttribute("data-phase", "exiting");
    expect(vi.getTimerCount()).toBe(1);
  });

  it("renders organizes deterministically on the server and hydrates without a mismatch", async () => {
    const element = <RequestComposer onSubmit={vi.fn()} busy={false} />;
    const markup = renderToString(element);
    const container = document.createElement("div");
    container.innerHTML = markup;
    expect(container.querySelector('[data-testid="rotating-action"]')).toHaveTextContent("organizes");
    document.body.appendChild(container);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    let root: ReturnType<typeof hydrateRoot> | null = null;
    await act(async () => {
      root = hydrateRoot(container, element);
      await Promise.resolve();
    });

    expect(screen.getByTestId("rotating-action")).toHaveTextContent("organizes");
    expect(consoleError.mock.calls.flat().join(" ")).not.toMatch(/hydration|didn't match/i);
    await act(async () => root?.unmount());
    consoleError.mockRestore();
    container.remove();
  });

  it("keeps organizes static when reduced motion is requested", () => {
    vi.useFakeTimers();
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    render(<RequestComposer onSubmit={vi.fn()} busy={false} />);
    expect(screen.getByTestId("rotating-action")).toHaveTextContent("organizes");
    expect(vi.getTimerCount()).toBe(0);
    act(() => vi.advanceTimersByTime(20_000));
    expect(screen.getByTestId("rotating-action")).toHaveTextContent("organizes");
    vi.unstubAllGlobals();
  });

  it("keeps Enter, Shift+Enter, IME, and duplicate submission behavior safe", async () => {
    let resolveSubmission: (() => void) | null = null;
    const onSubmit = vi.fn(() => new Promise<void>((resolve) => { resolveSubmission = resolve; }));
    render(<RequestComposer onSubmit={onSubmit} busy={false} />);
    const input = screen.getByRole("textbox", { name: "Goal" });
    fireEvent.change(input, { target: { value: "  Prepare for Simon  " } });

    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith("Prepare for Simon");
    await act(async () => resolveSubmission?.());
  });
});
