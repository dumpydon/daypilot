import { History, PanelLeftClose, PanelLeftOpen, Plus, Settings2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { Preferences, RunRecord } from "@/lib/types";
import { runDisplayTitle } from "@/lib/runPresentation";

import styles from "./workspace.module.css";

interface SidebarProps {
  runs: RunRecord[];
  activeRunId: string | null;
  preferences: Preferences;
  collapsed: boolean;
  mobileOpen: boolean;
  onSelect: (runId: string) => void;
  onNew: () => void;
  onPreferences: () => void;
  onToggle: () => void;
  onCloseMobile: () => void;
  onWidthChange: (width: number) => void;
}

const statusLabel: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  waiting_approval: "Waiting for approval",
  resuming: "Resuming",
  completed: "Completed",
  rejected: "Rejected safely",
  failed: "Failed",
};

export function Sidebar({
  runs,
  activeRunId,
  preferences,
  collapsed,
  mobileOpen,
  onSelect,
  onNew,
  onPreferences,
  onToggle,
  onCloseMobile,
  onWidthChange,
}: SidebarProps) {
  const [resizing, setResizing] = useState(false);
  const resizeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!resizing) return;
    const handleMove = (event: PointerEvent) => {
      onWidthChange(Math.min(320, Math.max(220, event.clientX)));
    };
    const handleUp = () => setResizing(false);
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp, { once: true });
    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
    };
  }, [onWidthChange, resizing]);

  const select = (runId: string) => {
    onSelect(runId);
    onCloseMobile();
  };

  return (
    <>
      <aside className={`${styles.sidebar} ${collapsed ? styles.sidebarCollapsed : ""} ${mobileOpen ? styles.sidebarOpen : ""}`}>
        <div className={styles.sidebarTop}>
          <button className={styles.newRun} onClick={() => { onNew(); onCloseMobile(); }} aria-label="New run">
            <Plus size={15} />
            {!collapsed && <span>New run</span>}
          </button>
          <button
            className={styles.sidebarToggle}
            onClick={onToggle}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {mobileOpen ? <X size={15} /> : collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
          </button>
        </div>

        {!collapsed ? (
          <div className={styles.sideSection}>
            <p className={styles.eyebrow}><History size={12} /> Recent runs</p>
            {runs.length === 0 ? (
              <p className={styles.emptyHistory}>Completed and interrupted runs will appear here.</p>
            ) : runs.map((run) => (
              <button
                className={`${styles.historyItem} ${activeRunId === run.id ? styles.selected : ""}`}
                key={run.id}
                onClick={() => select(run.id)}
                data-testid={`history-run-${run.id}`}
                title={run.user_request}
                aria-label={`${runDisplayTitle(run)}. ${run.user_request}. ${statusLabel[run.status]} · ${relativeTime(run.updated_at)}`}
              >
                <span className={styles.historyTitle} data-testid={`history-title-${run.id}`}>{runDisplayTitle(run)}</span>
                <span className={styles.historyPreview} data-testid={`history-preview-${run.id}`}>{run.user_request}</span>
                <small>
                  <i className={styles[`status_${run.status}`]} />
                  {statusLabel[run.status]} · {relativeTime(run.updated_at)}
                </small>
              </button>
            ))}
          </div>
        ) : (
          <div className={styles.collapsedRuns} aria-label="Recent runs">
            {runs.slice(0, 8).map((run) => (
              <button
                className={`${styles.collapsedRun} ${activeRunId === run.id ? styles.selected : ""}`}
                key={run.id}
                onClick={() => select(run.id)}
                title={`${runDisplayTitle(run)} — ${run.user_request}`}
                aria-label={`${runDisplayTitle(run)}. ${run.user_request}`}
              >
                <i className={styles[`status_${run.status}`]} />
              </button>
            ))}
          </div>
        )}

        <div className={styles.preferences}>
          {!collapsed ? (
            <>
              <div className={styles.preferencesTitle}>
                <p className={styles.eyebrow}>Preferences applied</p>
                <button aria-label="Edit preferences" onClick={onPreferences}><Settings2 size={14} /></button>
              </div>
              <div><span>Focus block</span><strong>{preferences.preferred_focus_block_minutes} min</strong></div>
              <div><span>Avoid after</span><strong>{formatTime(preferences.avoid_scheduling_after)}</strong></div>
            </>
          ) : (
            <button className={styles.collapsedSettings} onClick={onPreferences} aria-label="Edit preferences" title="Edit preferences">
              <Settings2 size={15} />
            </button>
          )}
        </div>
        {!collapsed && <div ref={resizeRef} className={`${styles.sidebarResizer} ${resizing ? styles.resizing : ""}`} onPointerDown={() => setResizing(true)} aria-label="Resize sidebar" role="separator" />}
      </aside>
      {mobileOpen && <button className={styles.sidebarBackdrop} onClick={onCloseMobile} aria-label="Close navigation" />}
    </>
  );
}

function relativeTime(value: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function formatTime(value: string): string {
  const [hour, minute] = value.split(":").map(Number);
  const suffix = hour >= 12 ? "PM" : "AM";
  return `${hour % 12 || 12}:${String(minute).padStart(2, "0")} ${suffix}`;
}
