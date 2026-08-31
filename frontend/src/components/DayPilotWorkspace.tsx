"use client";

import { AlertTriangle, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import {
  API_URL,
  addFileRoot,
  disconnectGoogle,
  disconnectX,
  createRun,
  decideRun,
  editRun,
  getConnections,
  listFileRoots,
  removeFileRoot,
  getHealth,
  getPreferences,
  getRun,
  getTools,
  listRuns,
  clearRunHistory,
  resetDemoWorkspace,
  savePreferences,
  startGoogleConnection,
  startXConnection,
} from "@/lib/api";
import type {
  ConnectionCatalog,
  FileRoot,
  Preferences,
  RunDetail,
  RunRecord,
  ToolCatalog,
} from "@/lib/types";

import { ContextPanel } from "./ContextPanel";
import { ConfirmationDialog } from "./ConfirmationDialog";
import { CapabilityStrip } from "./CapabilityStrip";
import { DotMatrixWordmark } from "./dot-matrix/DotMatrixWordmark";
import { Header } from "./Header";
import { PlanPanel } from "./PlanPanel";
import { PreferencesDialog } from "./PreferencesDialog";
import { RequestComposer } from "./RequestComposer";
import { Sidebar } from "./Sidebar";
import { TimelinePanel } from "./TimelinePanel";
import { ToolInspector } from "./ToolInspector";
import styles from "./workspace.module.css";

const defaultPreferences: Preferences = {
  preferred_focus_block_minutes: 90,
  avoid_scheduling_after: "22:00",
  preferred_task_due_time: "18:00",
};

const emptyCatalog: ToolCatalog = {
  servers: ["mail", "calendar", "tasks", "files", "x"].map((name) => ({
    name,
    connected: false,
    tool_count: 0,
    tools: [],
    error: null,
  })),
  tools: [],
};

const emptyConnections: ConnectionCatalog = {
  demo_mode: true,
  connections: [],
};

const streamEvents = [
  "request_received", "request_understood", "tools_discovered",
  "context_gathering_started", "tool_called", "tool_completed", "tool_failed", "tool_blocked",
  "context_gathered", "plan_generated", "approval_required", "approval_received",
  "plan_feedback_received", "replanning_started", "plan_revised",
  "execution_started", "action_started", "action_completed",
  "action_failed", "execution_verified", "run_rejected", "run_completed", "run_failed",
];

export function DayPilotWorkspace() {
  const [catalog, setCatalog] = useState<ToolCatalog>(emptyCatalog);
  const [connections, setConnections] = useState<ConnectionCatalog>(emptyConnections);
  const [fileRoots, setFileRoots] = useState<FileRoot[]>([]);
  const [preferences, setPreferences] = useState<Preferences>(defaultPreferences);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [activeRun, setActiveRun] = useState<RunDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  const hydrated = useSyncExternalStore(subscribeNoop, getClientHydrated, getServerHydrated);
  const storedSidebarWidth = useSyncExternalStore(
    subscribeToSidebarStorage,
    readSidebarWidth,
    getDefaultSidebarWidth,
  );
  const storedSidebarCollapsed = useSyncExternalStore(
    subscribeToSidebarStorage,
    readSidebarCollapsed,
    getDefaultSidebarCollapsed,
  );
  const [sidebarWidthOverride, setSidebarWidthOverride] = useState<number | null>(null);
  const [sidebarCollapsedOverride, setSidebarCollapsedOverride] = useState<boolean | null>(null);
  const sidebarWidth = hydrated ? sidebarWidthOverride ?? storedSidebarWidth : 244;
  const sidebarCollapsed = hydrated ? sidebarCollapsedOverride ?? storedSidebarCollapsed : false;
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [capabilityInspectorOpen, setCapabilityInspectorOpen] = useState(false);
  const [selectedCapability, setSelectedCapability] = useState<string | null>(null);
  const capabilityInspectorRef = useRef<HTMLElement>(null);
  const [runtimeMode, setRuntimeMode] = useState("unknown");
  const [maintenanceAction, setMaintenanceAction] = useState<"reset" | "clear" | null>(null);
  const [maintenanceBusy, setMaintenanceBusy] = useState(false);
  const [maintenanceMessage, setMaintenanceMessage] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const hasProcessingRun = busy || Boolean(
    activeRun && ["queued", "running", "resuming"].includes(activeRun.status),
  ) || runs.some((run) => ["queued", "running", "resuming"].includes(run.status));
  const hasPendingApproval = Boolean(activeRun?.status === "waiting_approval")
    || runs.some((run) => run.status === "waiting_approval");
  const maintenanceBlockMessage = hasProcessingRun || hasPendingApproval
    ? "Finish or reject active or approval-required runs before changing demo data or clearing history."
    : null;

  useEffect(() => {
    if (!hydrated) return;
    try {
      window.localStorage.setItem("daypilot.sidebar.width", String(sidebarWidth));
      window.localStorage.setItem("daypilot.sidebar.collapsed", String(sidebarCollapsed));
    } catch {
      // Local preferences are best-effort; the workspace remains usable if storage is blocked.
    }
  }, [hydrated, sidebarCollapsed, sidebarWidth]);

  useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => setNotice(null), 4_000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const refreshRuns = useCallback(async () => setRuns(await listRuns()), []);
  const refreshActive = useCallback(async (runId: string) => {
    const detail = await getRun(runId);
    setActiveRun(detail);
    setRuns((current) => {
      const summary: RunRecord = detail;
      return [summary, ...current.filter((run) => run.id !== runId)]
        .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
    });
    return detail;
  }, []);

  useEffect(() => {
    Promise.all([getTools(), getPreferences(), listRuns(), getConnections(), listFileRoots()])
      .then(([nextCatalog, nextPreferences, nextRuns, nextConnections, nextFileRoots]) => {
        setCatalog(nextCatalog);
        setPreferences(nextPreferences);
        setRuns(nextRuns);
        setConnections(nextConnections);
        setFileRoots(nextFileRoots);
      })
      .catch((cause: unknown) => setError(messageFrom(cause)));
    getHealth()
      .then((health) => setRuntimeMode(health.reasoning_mode))
      .catch(() => setRuntimeMode("unavailable"));
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connection = params.get("connection");
    if (!connection) return;
    const successMessage = connection.endsWith("_connected")
      ? `${connection.startsWith("google") ? "Google Workspace" : "X"} connected.`
      : null;
    const errorMessage = connection.endsWith("_error")
      ? params.get("message") || "The connection could not be completed."
      : null;
    queueMicrotask(() => {
      if (successMessage) setNotice(successMessage);
      if (errorMessage) setError(errorMessage);
    });
    window.history.replaceState({}, "", window.location.pathname);
  }, []);

  useEffect(() => {
    if (!activeRun?.id) return;
    const runId = activeRun.id;
    const source = new EventSource(`${API_URL}/api/runs/${runId}/events`);
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    const scheduleRefresh = () => {
      if (refreshTimer) clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => {
        refreshActive(runId).catch((cause: unknown) => setError(messageFrom(cause)));
      }, 60);
    };
    for (const eventName of streamEvents) source.addEventListener(eventName, scheduleRefresh);
    source.addEventListener("end", () => {
      scheduleRefresh();
      source.close();
    });
    return () => {
      source.close();
      if (refreshTimer) clearTimeout(refreshTimer);
    };
  }, [activeRun?.id, refreshActive]);

  useEffect(() => {
    if (!activeRun || ["completed", "rejected", "failed", "waiting_approval"].includes(activeRun.status)) return;
    const timer = setInterval(() => {
      refreshActive(activeRun.id).catch((cause: unknown) => setError(messageFrom(cause)));
    }, 900);
    return () => clearInterval(timer);
  }, [activeRun, refreshActive]);

  const reasoningMode = activeRun?.reasoning_mode && activeRun.reasoning_mode !== "pending"
    ? activeRun.reasoning_mode
    : runtimeMode;
  const activeId = activeRun?.id ?? null;
  const shortId = useMemo(() => activeId?.replace("run-", "").slice(0, 6).toUpperCase(), [activeId]);
  const presentedStatus = activeRun ? presentationStatus(activeRun) : null;

  async function start(goal: string) {
    setBusy(true);
    setError(null);
    setActiveRun(null);
    try {
      const accepted = await createRun(goal);
      let detail: RunDetail | null = null;
      for (let attempt = 0; attempt < 20 && !detail; attempt += 1) {
        try { detail = await getRun(accepted.id); } catch { await delay(75); }
      }
      if (!detail) throw new Error("The run started but its persisted state is not readable yet.");
      setActiveRun(detail);
      await refreshRuns();
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function selectRun(runId: string) {
    setError(null);
    try { await refreshActive(runId); } catch (cause) { setError(messageFrom(cause)); }
  }

  async function decide(decision: "approve" | "reject") {
    if (!activeRun) return;
    setBusy(true);
    setError(null);
    try {
      await decideRun(activeRun.id, decision);
      await refreshActive(activeRun.id);
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function revise(feedback: string) {
    if (!activeRun) return;
    setBusy(true);
    setError(null);
    try {
      const revised = await editRun(activeRun.id, feedback, activeRun.plan_revision);
      setActiveRun(revised);
      await refreshRuns();
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function persistPreferences(next: Preferences) {
    const saved = await savePreferences(next);
    setPreferences(saved);
  }

  async function refreshConnections() {
    const [nextConnections, nextFileRoots, nextCatalog] = await Promise.all([
      getConnections(),
      listFileRoots(),
      getTools(),
    ]);
    setConnections(nextConnections);
    setFileRoots(nextFileRoots);
    setCatalog(nextCatalog);
  }

  async function connectGoogle() {
    const result = await startGoogleConnection();
    window.location.assign(result.authorization_url);
  }

  async function disconnectGoogleAccount() {
    await disconnectGoogle();
    await refreshConnections();
    setNotice("Google Workspace disconnected.");
  }

  async function connectX() {
    const result = await startXConnection();
    window.location.assign(result.authorization_url);
  }

  async function disconnectXAccount() {
    await disconnectX();
    await refreshConnections();
    setNotice("X disconnected.");
  }

  async function addLocalFileRoot(path: string) {
    await addFileRoot(path);
    await refreshConnections();
    setNotice("Local folder connected.");
  }

  async function removeLocalFileRoot(rootId: string) {
    await removeFileRoot(rootId);
    await refreshConnections();
    setNotice("Local folder removed.");
  }

  function requestMaintenanceAction(action: "reset" | "clear") {
    setMaintenanceMessage(hasPendingApproval ? maintenanceBlockMessage : null);
    setMaintenanceAction(action);
  }

  async function executeMaintenanceAction() {
    if (!maintenanceAction) return;
    setMaintenanceBusy(true);
    setMaintenanceMessage(null);
    setError(null);
    try {
      if (maintenanceAction === "reset") {
        await resetDemoWorkspace();
        const [nextCatalog, nextRuns] = await Promise.all([getTools(), listRuns()]);
        setCatalog(nextCatalog);
        setRuns(nextRuns);
        setNotice("Demo workspace restored.");
      } else {
        await clearRunHistory();
        setRuns([]);
        setActiveRun(null);
        setNotice("Run history cleared.");
      }
      setMaintenanceAction(null);
      setPreferencesOpen(false);
    } catch (cause) {
      setMaintenanceMessage(messageFrom(cause));
    } finally {
      setMaintenanceBusy(false);
    }
  }

  const selectCapability = useCallback((serverName: string) => {
    setSelectedCapability(serverName);
    setCapabilityInspectorOpen(true);
    requestAnimationFrame(() => {
      const target = capabilityInspectorRef.current;
      if (target && typeof target.scrollIntoView === "function") {
        target.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    });
  }, []);

  const handleCapabilityInspectorOpenChange = useCallback((nextOpen: boolean) => {
    setCapabilityInspectorOpen(nextOpen);
    if (!nextOpen) setSelectedCapability(null);
  }, []);

  const handleCapabilityInspectorServerChange = useCallback((serverName: string | null) => {
    setSelectedCapability(serverName);
    if (serverName) setCapabilityInspectorOpen(true);
  }, []);

  return (
    <>
      <div className={styles.footerCurtainStage}>
        <DotMatrixWordmark pulse={{ type: "idle", id: 0 }} />
      </div>

      <main className={`${styles.shell} ${styles.footerCurtainContent}`}>
        <Header
          servers={catalog.servers}
          reasoningMode={reasoningMode}
          active={busy || Boolean(activeRun && ["queued", "running", "resuming"].includes(activeRun.status))}
          onMenu={() => setMobileSidebarOpen(true)}
        />
        <div
          className={`${styles.layout} ${sidebarCollapsed ? styles.layoutCollapsed : ""} ${mobileSidebarOpen ? styles.layoutMobileOpen : ""}`}
          style={{ "--sidebar-width": `${sidebarCollapsed ? 64 : sidebarWidth}px` } as React.CSSProperties}
        >
          <Sidebar
            runs={runs}
            activeRunId={activeId}
            preferences={preferences}
            collapsed={sidebarCollapsed}
            mobileOpen={mobileSidebarOpen}
            onSelect={selectRun}
            onNew={() => { setActiveRun(null); setMobileSidebarOpen(false); }}
            onPreferences={() => setPreferencesOpen(true)}
            onToggle={() => setSidebarCollapsedOverride(!sidebarCollapsed)}
            onCloseMobile={() => setMobileSidebarOpen(false)}
            onWidthChange={setSidebarWidthOverride}
          />
          <section className={`${styles.workspace} ${activeRun ? styles.workspaceActive : ""}`}>
            {error && <div className={styles.errorBanner}><AlertTriangle size={14} /><span>{error}</span><button onClick={() => setError(null)}>Dismiss</button></div>}
            {!activeRun ? (
              <div className={styles.startView}>
                <RequestComposer onSubmit={start} busy={busy} />
                <div className={styles.landingGrid}>
                  <section className={styles.capabilityPanel} aria-labelledby="capability-heading">
                    <div className={styles.sectionHeader}>
                      <div>
                        <span className={styles.eyebrow}>Connected services</span>
                        <h2 id="capability-heading">What DayPilot can do</h2>
                      </div>
                    </div>
                    <CapabilityStrip catalog={catalog} selectedServer={selectedCapability} onSelect={selectCapability} />
                  </section>
                  <ToolInspector
                    catalog={catalog}
                    collapsible
                    open={capabilityInspectorOpen}
                    onOpenChange={handleCapabilityInspectorOpenChange}
                    expandedServer={selectedCapability}
                    onExpandedServerChange={handleCapabilityInspectorServerChange}
                    sectionRef={capabilityInspectorRef}
                  />
                </div>
              </div>
            ) : (
              <>
                <div className={styles.requestBar}>
                  <div className={styles.requestCopy}>
                    <span className={styles.eyebrow}>Active request</span>
                    <h1 title={activeRun.user_request}>{activeRun.user_request}</h1>
                  </div>
                  <div className={styles.runMeta}>
                    <div className={`${styles.currentState} ${styles[`currentState_${presentedStatus ?? activeRun.status}`]}`} aria-live="polite">
                      <i />
                      <div>
                        <strong>{currentStateLabel(activeRun, presentedStatus ?? activeRun.status)}</strong>
                        <span>
                          <b className={`${styles.runStatus} ${styles[`runStatus_${presentedStatus ?? activeRun.status}`]}`}>{prettyStatus(presentedStatus ?? activeRun.status)}</b>
                          <span className={styles.runId}>Run {shortId}</span>
                        </span>
                      </div>
                    </div>
                    <button onClick={() => refreshActive(activeRun.id)} aria-label="Refresh run" title="Refresh run">
                      <RotateCcw size={14} />
                    </button>
                  </div>
                </div>
                <div className={`${styles.grid} ${["completed", "failed", "rejected"].includes(activeRun.status) ? styles.gridResolved : ""}`}>
                  <PlanPanel key={`${activeRun.id}-${activeRun.plan_revision}-${activeRun.status}`} run={activeRun} busy={busy} onApprove={() => decide("approve")} onReject={() => decide("reject")} onEdit={revise} />
                  <TimelinePanel events={activeRun.events} runId={activeRun.id} runStatus={activeRun.status} />
                  <div className={styles.inspectorShelf}>
                    <ContextPanel context={activeRun.context} />
                    <ToolInspector catalog={catalog} collapsible />
                  </div>
                </div>
              </>
            )}
          </section>
        </div>
        {preferencesOpen && (
          <PreferencesDialog
            preferences={preferences}
            onClose={() => setPreferencesOpen(false)}
            onSave={persistPreferences}
            onResetDemoRequest={() => requestMaintenanceAction("reset")}
            onClearHistoryRequest={() => requestMaintenanceAction("clear")}
            maintenanceBlocked={hasProcessingRun}
            maintenanceMessage={maintenanceBlockMessage}
            maintenanceBusy={maintenanceBusy}
            connections={connections}
            fileRoots={fileRoots}
            onConnectGoogle={connectGoogle}
            onDisconnectGoogle={disconnectGoogleAccount}
            onConnectX={connectX}
            onDisconnectX={disconnectXAccount}
            onAddFileRoot={addLocalFileRoot}
            onRemoveFileRoot={removeLocalFileRoot}
          />
        )}
        {maintenanceAction === "reset" && (
          <ConfirmationDialog
            title="Reset demo workspace?"
            body="This will remove changes made to the demo Mail, Calendar, Tasks, Files and X services and restore their original seeded state. Your preferences and run history will be kept."
            confirmLabel="Reset workspace"
            busy={maintenanceBusy}
            error={maintenanceMessage}
            onCancel={() => { if (!maintenanceBusy) { setMaintenanceAction(null); setMaintenanceMessage(null); } }}
            onConfirm={executeMaintenanceAction}
          />
        )}
        {maintenanceAction === "clear" && (
          <ConfirmationDialog
            title="Clear run history?"
            body="This will remove saved DayPilot runs, their timelines, execution records, and persisted graph checkpoints. Demo service data and preferences will be kept."
            confirmLabel="Clear history"
            busy={maintenanceBusy}
            error={maintenanceMessage}
            onCancel={() => { if (!maintenanceBusy) { setMaintenanceAction(null); setMaintenanceMessage(null); } }}
            onConfirm={executeMaintenanceAction}
          />
        )}
        {notice && <div className={styles.toast} role="status">{notice}</div>}
        <footer className={styles.appFooter}>
          <div className={styles.appFooterInner}>
            <p>© 2026 daypilot, made with <span role="img" aria-label="lizard">🦎</span> in India</p>
            <nav aria-label="Footer links">
              <a href="#terms-of-use">Terms of use</a>
              <span aria-hidden="true">|</span>
              <a href="#privacy-policy">Privacy Policy</a>
            </nav>
          </div>
        </footer>
      </main>

      <div className={styles.footerCurtainSpacer} aria-hidden="true" />
    </>
  );
}

function messageFrom(cause: unknown): string {
  return cause instanceof Error ? cause.message : "DayPilot encountered an unexpected error.";
}

function delay(milliseconds: number) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function prettyStatus(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function currentStateLabel(run: RunDetail, status: RunDetail["status"]) {
  if (status === "completed") return "Run complete";
  if (status === "failed") return run.status === "completed" ? "Completed with issues" : "Run needs attention";
  if (status === "rejected") return "Closed safely";
  if (status === "waiting_approval") return "Waiting for your approval";
  return run.events.at(-1)?.title ?? prettyStatus(run.status);
}

function presentationStatus(run: RunDetail): RunDetail["status"] {
  if (run.status !== "completed") return run.status;
  const impossibleEmptySuccess = run.created_outputs.some((output) => (
    output.resource_type === "task_batch"
    && output.status === "verified"
    && output.verified
    && output.items.length === 0
    && /\b(?:created\s+)?0\s+tasks?\b/i.test(output.title)
  ));
  const reportedFailure = run.created_outputs.some(
    (output) => output.status === "failed" || output.status === "partially_completed",
  );
  return run.error || reportedFailure || impossibleEmptySuccess ? "failed" : run.status;
}

function subscribeToSidebarStorage(onStoreChange: () => void) {
  if (typeof window === "undefined") return () => undefined;
  const handleStorage = (event: StorageEvent) => {
    if (event.key === "daypilot.sidebar.width" || event.key === "daypilot.sidebar.collapsed") onStoreChange();
  };
  window.addEventListener("storage", handleStorage);
  return () => window.removeEventListener("storage", handleStorage);
}

function subscribeNoop() {
  return () => undefined;
}

function getClientHydrated() {
  return true;
}

function getServerHydrated() {
  return false;
}

function readSidebarWidth() {
  if (typeof window === "undefined") return 244;
  try {
    const storedWidth = Number(window.localStorage.getItem("daypilot.sidebar.width"));
    return Number.isFinite(storedWidth) && storedWidth >= 220 && storedWidth <= 320 ? storedWidth : 244;
  } catch {
    return 244;
  }
}

function getDefaultSidebarWidth() {
  return 244;
}

function readSidebarCollapsed() {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem("daypilot.sidebar.collapsed") === "true";
  } catch {
    return false;
  }
}

function getDefaultSidebarCollapsed() {
  return false;
}
