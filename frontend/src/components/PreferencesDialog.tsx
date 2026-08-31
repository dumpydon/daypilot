import { Clock3, Database, History, X } from "lucide-react";
import { FormEvent, useState } from "react";

import type { ConnectionCatalog, FileRoot, Preferences } from "@/lib/types";

import { ConnectionSettings } from "./ConnectionSettings";
import styles from "./workspace.module.css";

interface PreferencesDialogProps {
  preferences: Preferences;
  onClose: () => void;
  onSave: (preferences: Preferences) => Promise<void>;
  onResetDemoRequest: () => void;
  onClearHistoryRequest: () => void;
  maintenanceBlocked: boolean;
  maintenanceMessage: string | null;
  maintenanceBusy: boolean;
  connections?: ConnectionCatalog;
  fileRoots?: FileRoot[];
  onConnectGoogle?: () => Promise<void>;
  onDisconnectGoogle?: () => Promise<void>;
  onConnectX?: () => Promise<void>;
  onDisconnectX?: () => Promise<void>;
  onAddFileRoot?: (path: string) => Promise<void>;
  onRemoveFileRoot?: (rootId: string) => Promise<void>;
}

export function PreferencesDialog({
  preferences,
  onClose,
  onSave,
  onResetDemoRequest,
  onClearHistoryRequest,
  maintenanceBlocked,
  maintenanceMessage,
  maintenanceBusy,
  connections,
  fileRoots = [],
  onConnectGoogle = noopAsync,
  onDisconnectGoogle = noopAsync,
  onConnectX = noopAsync,
  onDisconnectX = noopAsync,
  onAddFileRoot = noopAsync,
  onRemoveFileRoot = noopAsync,
}: PreferencesDialogProps) {
  const [draft, setDraft] = useState(preferences);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try { await onSave(draft); onClose(); } finally { setBusy(false); }
  }
  return (
    <div className={styles.dialogBackdrop} role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <form className={styles.dialog} onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="preferences-title">
        <div className={styles.dialogHeader}><span><Clock3 size={15} /><strong id="preferences-title">Planning preferences</strong></span><button type="button" aria-label="Close preferences" onClick={onClose}><X size={15} /></button></div>
        <p>These transparent defaults are included in every planning checkpoint.</p>
        <label>Default focus block <span>{draft.preferred_focus_block_minutes} minutes</span><input type="range" min="30" max="180" step="15" value={draft.preferred_focus_block_minutes} onChange={(event) => setDraft({ ...draft, preferred_focus_block_minutes: Number(event.target.value) })} /></label>
        <label>Avoid scheduling after<input type="time" value={draft.avoid_scheduling_after} onChange={(event) => setDraft({ ...draft, avoid_scheduling_after: event.target.value })} /></label>
        <label>Preferred task due time<input type="time" value={draft.preferred_task_due_time} onChange={(event) => setDraft({ ...draft, preferred_task_due_time: event.target.value })} /></label>
        {connections && (
          <ConnectionSettings
            catalog={connections}
            fileRoots={fileRoots}
            onConnectGoogle={onConnectGoogle}
            onDisconnectGoogle={onDisconnectGoogle}
            onConnectX={onConnectX}
            onDisconnectX={onDisconnectX}
            onAddFileRoot={onAddFileRoot}
            onRemoveFileRoot={onRemoveFileRoot}
          />
        )}
        <div className={styles.settingsDivider} />
        {(!connections || connections.demo_mode) && (
          <div className={styles.settingsSection}>
            <div className={styles.settingsSectionHeader}>
              <Database size={14} />
              <div><strong>Demo workspace</strong><p>Restore Mail, Calendar, Tasks, Files and X to their seeded state.</p></div>
            </div>
            <button
              className={styles.dangerButton}
              type="button"
              disabled={busy || maintenanceBusy || maintenanceBlocked}
              onClick={onResetDemoRequest}
            >
              Reset demo workspace
            </button>
          </div>
        )}
        <div className={styles.settingsSection}>
          <div className={styles.settingsSectionHeader}>
            <History size={14} />
            <div><strong>Run history</strong><p>Remove saved DayPilot runs without changing demo services or preferences.</p></div>
          </div>
          <button
            className={styles.dangerButton}
            type="button"
            disabled={busy || maintenanceBusy || maintenanceBlocked}
            onClick={onClearHistoryRequest}
          >
            Clear run history
          </button>
        </div>
        {maintenanceMessage && <p className={styles.settingsNotice}>{maintenanceMessage}</p>}
        <div className={styles.dialogActions}><button type="button" onClick={onClose}>Cancel</button><button className={styles.primary} disabled={busy}>{busy ? "Saving…" : "Save preferences"}</button></div>
      </form>
    </div>
  );
}

async function noopAsync() {}
