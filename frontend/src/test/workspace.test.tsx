import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CapabilityStrip } from "@/components/CapabilityStrip";
import { ConfirmationDialog } from "@/components/ConfirmationDialog";
import { CreatedOutputs } from "@/components/CreatedOutputs";
import { DayPilotLogo } from "@/components/DayPilotLogo";
import { Header } from "@/components/Header";
import { PlanPanel } from "@/components/PlanPanel";
import { PreferencesDialog } from "@/components/PreferencesDialog";
import { RequestComposer } from "@/components/RequestComposer";
import { Sidebar } from "@/components/Sidebar";
import { HERO_MOTION, HERO_WORDS } from "@/components/RotatingHeroWord";
import { TimelinePanel } from "@/components/TimelinePanel";
import { ToolInspector } from "@/components/ToolInspector";
import { editRun } from "@/lib/api";
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

function SidebarHarness({ run, onSelect }: { run: ReturnType<typeof makeRun>; onSelect: (runId: string) => void }) {
  return (
    <Sidebar
      runs={[run]}
      activeRunId={null}
      preferences={{ preferred_focus_block_minutes: 90, avoid_scheduling_after: "22:00", preferred_task_due_time: "18:00" }}
      collapsed={false}
      mobileOpen={false}
      onSelect={onSelect}
      onNew={vi.fn()}
      onPreferences={vi.fn()}
      onToggle={vi.fn()}
      onCloseMobile={vi.fn()}
      onWidthChange={vi.fn()}
    />
  );
}

describe("DayPilot operations workspace", () => {
  it("renders one scalable canonical logo with a restrained active state", () => {
    const { rerender } = render(<DayPilotLogo size={16} />);
    const logo = screen.getByRole("img", { name: "DayPilot logo" });
    expect(logo).toHaveAttribute("width", "16");
    expect(logo).toHaveAttribute("height", "16");
    expect(logo.querySelectorAll("path")).toHaveLength(4);

    rerender(<DayPilotLogo size={64} active />);
    expect(screen.getByRole("img", { name: "DayPilot logo" }).getAttribute("class")).toContain("logoActive");
  });

  it("keeps OpenAI and local runtime status in one compact group", () => {
    const view = render(<Header servers={capabilityCatalog.servers} reasoningMode="openai" onMenu={vi.fn()} />);
    const group = screen.getByRole("group", { name: "Runtime status" });
    expect(group).toHaveTextContent("OpenAI runtime");
    expect(group).toHaveTextContent("Local runtime");
    expect(screen.getByTestId("openai-runtime-indicator")).toHaveAttribute("data-runtime-state", "ready");
    expect(screen.getByText("5/5 MCP servers")).toBeInTheDocument();

    view.rerender(<Header servers={capabilityCatalog.servers} reasoningMode="deterministic_demo" onMenu={vi.fn()} />);
    expect(screen.getByRole("group", { name: "Runtime status" })).toHaveTextContent("OpenAI unavailable");
    expect(screen.getByTestId("openai-runtime-indicator")).toHaveAttribute("data-runtime-state", "unavailable");
    view.rerender(<Header servers={capabilityCatalog.servers} reasoningMode="openai" onMenu={vi.fn()} />);
    expect(screen.getByTestId("openai-runtime-indicator")).toHaveAttribute("data-runtime-state", "ready");
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

    expect(screen.getByRole("heading", { name: "Created outputs" })).toBeInTheDocument();
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

  it("renders semantic approval state in the execution timeline", () => {
    render(<TimelinePanel events={[makeEvent()]} />);
    expect(screen.getByText("Waiting for human approval")).toBeInTheDocument();
    expect(screen.getByText(/3 external changes are blocked/i)).toBeInTheDocument();
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
    expect(screen.getByTestId("capability-card-mail")).toHaveTextContent("4 tools");
    expect(screen.getByTestId("capability-card-calendar")).toHaveTextContent("3 tools");
    expect(screen.getByTestId("capability-card-tasks")).toHaveTextContent("4 tools");
    expect(screen.getByTestId("capability-card-files")).toHaveTextContent("4 tools");
    expect(screen.getByTestId("capability-card-x")).toHaveTextContent("5 tools");
  });

  it("opens and selects the matching MCP details from each capability card", async () => {
    const user = userEvent.setup();
    render(<CapabilityHarness />);
    expect(screen.getByText("5 MCP servers · 20 tools")).toBeInTheDocument();

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
    expect(screen.getByText("Create exactly two preparation tasks")).toBeInTheDocument();
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

  it("starts with an empty request and a Simon placeholder", () => {
    render(<RequestComposer onSubmit={vi.fn()} busy={false} />);
    const input = screen.getByRole("textbox", { name: "Goal" });
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("placeholder", "Prepare me for my interview with Simon tomorrow.");
    expect(screen.queryByText("Interview preparation")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try a demo/i })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "Start DayPilot run" })).toBeDisabled();
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
