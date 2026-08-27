import { AlertTriangle, X } from "lucide-react";

import styles from "./workspace.module.css";

interface ConfirmationDialogProps {
  title: string;
  body: string;
  confirmLabel: string;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmationDialog({
  title,
  body,
  confirmLabel,
  busy,
  error,
  onCancel,
  onConfirm,
}: ConfirmationDialogProps) {
  return (
    <div
      className={styles.dialogBackdrop}
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && !busy && onCancel()}
    >
      <section className={`${styles.dialog} ${styles.confirmationDialog}`} role="dialog" aria-modal="true" aria-labelledby="confirmation-title">
        <div className={styles.dialogHeader}>
          <span><AlertTriangle size={15} /><strong id="confirmation-title">{title}</strong></span>
          <button type="button" aria-label="Close confirmation" disabled={busy} onClick={onCancel}><X size={15} /></button>
        </div>
        <p>{body}</p>
        {error && <p className={styles.confirmationError}>{error}</p>}
        <div className={styles.dialogActions}>
          <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className={styles.dangerPrimary} type="button" onClick={onConfirm} disabled={busy}>
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}
