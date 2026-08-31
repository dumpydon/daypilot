import {
  AtSign,
  Check,
  FolderOpen,
  Globe2,
  Link2Off,
  Mail,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { type ReactNode, useState } from "react";

import type { ConnectionCatalog, FileRoot, ProviderConnection } from "@/lib/types";

import styles from "./workspace.module.css";

interface ConnectionSettingsProps {
  catalog: ConnectionCatalog;
  fileRoots: FileRoot[];
  onConnectGoogle: () => Promise<void>;
  onDisconnectGoogle: () => Promise<void>;
  onConnectX: () => Promise<void>;
  onDisconnectX: () => Promise<void>;
  onAddFileRoot: (path: string) => Promise<void>;
  onRemoveFileRoot: (rootId: string) => Promise<void>;
}

export function ConnectionSettings({
  catalog,
  fileRoots,
  onConnectGoogle,
  onDisconnectGoogle,
  onConnectX,
  onDisconnectX,
  onAddFileRoot,
  onRemoveFileRoot,
}: ConnectionSettingsProps) {
  const [folderManagerOpen, setFolderManagerOpen] = useState(false);
  const [rootPath, setRootPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const google = catalog.connections.filter((connection) =>
    ["mail", "calendar", "tasks"].includes(connection.service),
  );
  const googleConfigured = google.some((connection) => connection.provider !== "DayPilot demo");
  const googleConnected = googleConfigured && google.length > 0 && google.every((connection) => connection.state === "connected");
  const googleConnecting = google.some((connection) => connection.state === "connecting");
  const googleNeedsReconnect = google.some((connection) => connection.requires_reauth || connection.state === "error");
  const googleUnavailable = google.some((connection) => connection.state === "unavailable");
  const googleSummary = google.find((connection) => connection.state !== "connected") ?? google[0];
  const x = catalog.connections.find((connection) => connection.service === "x");
  const files = catalog.connections.find((connection) => connection.service === "files");

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The connection could not be updated.");
    } finally {
      setBusy(false);
    }
  }

  async function addRoot() {
    const path = rootPath.trim();
    if (!path) return;
    await run(async () => {
      await onAddFileRoot(path);
      setRootPath("");
    });
  }

  return (
    <section className={styles.connectionSettings} aria-labelledby="connections-title">
      <div className={styles.connectionHeading}>
        <Globe2 size={14} />
        <div>
          <strong id="connections-title">Connected services</strong>
          <p>{catalog.demo_mode ? "DayPilot demo providers are active. Set connected mode in the backend to use real accounts." : "Provider access stays behind the MCP boundary."}</p>
        </div>
      </div>

      <div className={styles.connectionRows}>
        <ConnectionRow
          icon={<Mail size={14} />}
          title="Google Workspace"
          detail="Mail · Calendar · Tasks"
          connection={googleSummary}
          account={google.find((item) => item.account_label)?.account_label ?? undefined}
          demoMode={catalog.demo_mode}
          warning={googleNeedsReconnect}
          busy={busy}
          actionLabel={googleConnecting || googleUnavailable ? undefined : googleConnected ? "Disconnect" : googleNeedsReconnect ? "Reconnect Google" : "Connect Google"}
          onAction={googleConnecting || googleUnavailable ? undefined : () => run(googleConnected ? onDisconnectGoogle : onConnectGoogle)}
        />

        <ConnectionRow
          icon={<FolderOpen size={14} />}
          title="Files"
          detail={`Local Mac · ${fileRoots.length} folder${fileRoots.length === 1 ? "" : "s"}`}
          connection={files}
          demoMode={catalog.demo_mode}
          warning={files?.state === "error"}
          busy={busy}
          actionLabel={catalog.demo_mode ? undefined : folderManagerOpen ? "Hide folders" : "Manage folders"}
          onAction={catalog.demo_mode ? undefined : () => setFolderManagerOpen((open) => !open)}
        />

        <ConnectionRow
          icon={<AtSign size={14} />}
          title="X"
          detail={x?.account_label ? `${x.account_label} · X API` : "X account"}
          connection={x}
          demoMode={catalog.demo_mode}
          warning={x?.requires_reauth}
          busy={busy}
          actionLabel={x?.state === "connecting" || x?.state === "unavailable" ? undefined : x?.provider !== "DayPilot demo" && x?.state === "connected" ? "Disconnect" : x?.requires_reauth ? "Reconnect X" : "Connect X"}
          onAction={x?.state === "connecting" || x?.state === "unavailable" ? undefined : () => run(x?.provider !== "DayPilot demo" && x?.state === "connected" ? onDisconnectX : onConnectX)}
        />
      </div>

      {folderManagerOpen && !catalog.demo_mode && (
        <div className={styles.folderManager}>
          <div className={styles.folderInputRow}>
            <input
              aria-label="Local folder path"
              value={rootPath}
              onChange={(event) => setRootPath(event.target.value)}
              placeholder="/Users/name/Documents/Notes"
              spellCheck={false}
            />
            <button type="button" onClick={addRoot} disabled={busy || !rootPath.trim()}><Plus size={13} />Add folder</button>
          </div>
          {fileRoots.length > 0 && (
            <ul className={styles.folderList}>
              {fileRoots.map((root) => (
                <li key={root.id}>
                  <span><strong>{root.label}</strong><small>{root.path}</small></span>
                  <button type="button" aria-label={`Remove ${root.label}`} onClick={() => run(() => onRemoveFileRoot(root.id))} disabled={busy}><Trash2 size={13} /></button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {error && <p className={styles.connectionError} role="alert">{error}</p>}
    </section>
  );
}

function ConnectionRow({
  icon,
  title,
  detail,
  connection,
  account,
  demoMode,
  warning = false,
  busy,
  actionLabel,
  onAction,
}: {
  icon: ReactNode;
  title: string;
  detail: string;
  connection?: ProviderConnection;
  account?: string;
  demoMode: boolean;
  warning?: boolean;
  busy: boolean;
  actionLabel?: string;
  onAction?: () => void;
}) {
  const state = demoMode ? "demo" : connection?.state ?? "disconnected";
  const stateLabel = state === "connected"
    ? "Connected"
    : state === "connecting"
      ? "Connecting…"
    : state === "demo"
      ? "DayPilot demo"
      : state === "unavailable" && connection?.connection_mode === "managed"
        ? "Managed connection unavailable"
      : state === "unavailable"
        ? "Unavailable"
      : warning
          ? "Reconnect required"
          : "Not connected";
  const modeLabel = !demoMode && connection?.connection_mode === "managed"
    ? "Managed"
    : !demoMode && connection?.connection_mode === "direct"
      ? "Direct"
      : null;
  const detailLabel = modeLabel ? `${detail} · ${modeLabel}` : detail;
  return (
    <div className={styles.connectionRow}>
      <span className={`${styles.connectionIcon} ${warning ? styles.connectionIconWarning : ""}`}>{icon}</span>
      <span className={styles.connectionCopy}>
        <strong>{title}</strong>
        <small>{account ? `${detailLabel} · ${account}` : detailLabel}</small>
      </span>
      <span className={`${styles.connectionState} ${warning ? styles.connectionStateWarning : state === "connected" || state === "demo" ? styles.connectionStateConnected : ""}`}>
        {state === "connected" || state === "demo" ? <Check size={11} /> : warning ? <Link2Off size={11} /> : <X size={11} />}
        {stateLabel}
      </span>
      {actionLabel && onAction && !demoMode && <button className={styles.connectionAction} type="button" onClick={onAction} disabled={busy}>{actionLabel}</button>}
    </div>
  );
}
