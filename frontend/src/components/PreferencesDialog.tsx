import { Check, Clock3, Database, Eye, EyeOff, History, KeyRound, LoaderCircle, X } from "lucide-react";
import { FormEvent, useRef, useState } from "react";

import type { AdminStatus, ConnectionCatalog, FileRoot, Preferences } from "@/lib/types";

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
  adminStatus?: AdminStatus;
  onAdminLogin?: (accessCode: string) => Promise<void>;
  onAdminLogout?: () => Promise<void>;
  servicesLoading?: boolean;
  servicesError?: string | null;
  onRetryServices?: () => Promise<void>;
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
  adminStatus,
  onAdminLogin = noopAdminLogin,
  onAdminLogout = noopAdminLogout,
  servicesLoading = false,
  servicesError = null,
  onRetryServices = noopAsync,
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
  const [accessCode, setAccessCode] = useState("");
  const [showAccessCode, setShowAccessCode] = useState(false);
  const [adminBusy, setAdminBusy] = useState(false);
  const adminInFlight = useRef(false);
  const [adminError, setAdminError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try { await onSave(draft); onClose(); } finally { setBusy(false); }
  }
  async function login() {
    if (!accessCode.trim() || adminInFlight.current) return;
    adminInFlight.current = true;
    setShowAccessCode(false);
    setAdminBusy(true);
    setAdminError(null);
    try {
      await onAdminLogin(accessCode);
      setAccessCode("");
    } catch (cause) {
      setAdminError(cause instanceof Error ? cause.message : "Admin access could not be enabled.");
    } finally {
      adminInFlight.current = false;
      setAdminBusy(false);
    }
  }
  async function logout() {
    if (adminBusy) return;
    setAdminBusy(true);
    setAdminError(null);
    try { await onAdminLogout(); }
    catch (cause) { setAdminError(cause instanceof Error ? cause.message : "Admin mode could not be locked."); }
    finally { setAdminBusy(false); }
  }
  const publicDemoMode = Boolean(
    adminStatus?.public_demo_mode
      || connections?.connections.some((connection) => connection.last_error === "Available to admin only."),
  );
  const authenticated = adminStatus?.authenticated ?? false;
  return (
    <div className={styles.dialogBackdrop} role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <form className={styles.dialog} onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="preferences-title">
        <div className={styles.dialogHeader}><span><Clock3 size={15} /><strong id="preferences-title">Planning preferences</strong></span><button type="button" aria-label="Close preferences" onClick={onClose}><X size={15} /></button></div>
        <p>These transparent defaults are included in every planning checkpoint.</p>
        <label>Default focus block <span>{draft.preferred_focus_block_minutes} minutes</span><input type="range" min="30" max="180" step="15" value={draft.preferred_focus_block_minutes} onChange={(event) => setDraft({ ...draft, preferred_focus_block_minutes: Number(event.target.value) })} /></label>
        <label>Avoid scheduling after<input type="time" value={draft.avoid_scheduling_after} onChange={(event) => setDraft({ ...draft, avoid_scheduling_after: event.target.value })} /></label>
        <label>Preferred task due time<input type="time" value={draft.preferred_task_due_time} onChange={(event) => setDraft({ ...draft, preferred_task_due_time: event.target.value })} /></label>
        {servicesLoading || servicesError ? (
          <section className={styles.connectionSettings} aria-label="Connected services">
            <strong>Connected services</strong>
            {servicesLoading ? <p role="status"><LoaderCircle size={14} className={styles.spin} aria-hidden="true" /> Refreshing connected services…</p> : <><p className={styles.connectionError} role="alert">{servicesError}</p><button type="button" className={styles.secondaryButton} onClick={() => void onRetryServices()}>Retry refresh</button></>}
          </section>
        ) : connections && (
          <ConnectionSettings
            catalog={connections}
            fileRoots={fileRoots}
            publicDemoMode={Boolean(publicDemoMode && !adminStatus?.authenticated)}
            onConnectGoogle={onConnectGoogle}
            onDisconnectGoogle={onDisconnectGoogle}
            onConnectX={onConnectX}
            onDisconnectX={onDisconnectX}
            onAddFileRoot={onAddFileRoot}
            onRemoveFileRoot={onRemoveFileRoot}
          />
        )}
        {publicDemoMode && (
          <div className={styles.settingsSection}>
            <div className={`${styles.settingsSectionHeader} ${authenticated ? styles.adminEnabled : ""}`} role="status">
              {authenticated ? <Check size={14} /> : <KeyRound size={14} />}
              <div><strong>{authenticated ? "Admin mode enabled" : "Admin access"}</strong><p>{authenticated ? "Personal services are now available for this browser." : adminBusy ? "Verifying admin access…" : "Unlock personal services for this browser."}</p></div>
            </div>
            {authenticated ? (
              <button className={styles.secondaryButton} type="button" onClick={logout} disabled={adminBusy}>Lock admin mode</button>
            ) : (
              <div className={styles.adminAccessRow}>
                <div className={styles.adminPasswordField}>
                  <input type={showAccessCode ? "text" : "password"} aria-label="Admin access code" disabled={adminBusy} value={accessCode} onChange={(event) => setAccessCode(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); if (!event.nativeEvent.isComposing) void login(); } }} placeholder="Access code" autoComplete="off" />
                  <button type="button" className={styles.adminPasswordToggle} disabled={adminBusy} aria-label={showAccessCode ? "Hide password" : "Show password"} title={showAccessCode ? "Hide password" : "Show password"} onClick={() => setShowAccessCode((shown) => !shown)}>
                    {showAccessCode ? <EyeOff size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
                  </button>
                </div>
                <button className={styles.secondaryButton} type="button" onClick={login} disabled={adminBusy || !accessCode.trim()} aria-busy={adminBusy}>{adminBusy ? <><LoaderCircle size={13} className={styles.spin} aria-hidden="true" /> Unlocking…</> : "Unlock"}</button>
              </div>
            )}
            {adminError && <p className={styles.connectionError} role="alert">{adminError}</p>}
          </div>
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
async function noopAdminLogin() {}
async function noopAdminLogout() {}
