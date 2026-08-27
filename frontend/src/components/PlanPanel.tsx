import { Check, FilePenLine, ShieldCheck, ShieldAlert, X } from "lucide-react";
import { useState } from "react";

import type { PlanAction, RunDetail } from "@/lib/types";

import { CreatedOutputs } from "./CreatedOutputs";
import styles from "./workspace.module.css";

interface PlanPanelProps {
  run: RunDetail;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
  onEdit: (feedback: string) => void;
}

export function PlanPanel({ run, busy, onApprove, onReject, onEdit }: PlanPanelProps) {
  const [editing, setEditing] = useState(false);
  const [feedback, setFeedback] = useState("");
  const writes = run.plan.filter((action) => action.side_effecting);
  const waiting = run.status === "waiting_approval";
  const hasUnverifiedOutput = run.created_outputs.some(
    (output) => output.status === "failed" || output.status === "partially_completed" || !output.verified,
  );

  return (
    <section className={styles.planPanel}>
      <div className={styles.sectionHeader}>
        <div>
          <span className={styles.eyebrow}>Proposed plan</span>
          <h2>
            {run.plan.length ? `${run.plan.length} coordinated actions` : "Building action plan"}
            {run.plan_revision > 0 && (
              <span className={styles.revision}>Revision {run.plan_revision}</span>
            )}
          </h2>
        </div>
        {writes.length > 0 && <span className={styles.writeCount}>{writes.length} need approval</span>}
      </div>

      {run.plan.length === 0 ? <PlanSkeleton /> : (
        <div className={styles.planList}>
          {run.plan.map((action, index) => <PlanRow action={action} index={index} key={action.id} />)}
        </div>
      )}

      {waiting && (
        <div className={styles.approval}>
          <div className={styles.approvalIcon}><ShieldAlert size={15} /></div>
          <div className={styles.approvalCopy}>
            <span className={styles.eyebrow}>Human approval required</span>
            <h3>Review proposed changes</h3>
            <p>Context was gathered automatically. These {writes.length} exact write payloads remain blocked until you approve them.</p>
          </div>
          {!editing ? (
            <div className={styles.actions}>
              <button className={styles.primary} onClick={onApprove} disabled={busy}><ShieldCheck size={13} />Approve &amp; execute</button>
              <button onClick={() => setEditing(true)} disabled={busy}><FilePenLine size={12} />Edit plan</button>
              <button onClick={onReject} disabled={busy}><X size={12} />Reject</button>
            </div>
          ) : (
            <div className={styles.editBox}>
              <label htmlFor="plan-feedback">Plan feedback</label>
              <textarea
                id="plan-feedback"
                value={feedback}
                onChange={(event) => setFeedback(event.target.value)}
                placeholder="e.g. Remove the draft and make the block 60 minutes"
                autoFocus
              />
              <div>
                <button onClick={() => setEditing(false)}>Cancel</button>
                <button className={styles.primary} disabled={!feedback.trim() || busy} onClick={() => onEdit(feedback.trim())}>{busy ? "Revising plan…" : "Revise plan"}</button>
              </div>
            </div>
          )}
        </div>
      )}

      {run.final_summary && (
        <div className={`${styles.summary} ${run.status === "failed" || hasUnverifiedOutput ? styles.failedSummary : ""}`}>
          <span>{run.status === "rejected" ? <ShieldCheck size={15} /> : hasUnverifiedOutput ? <ShieldAlert size={15} /> : <Check size={15} />}</span>
          <div><p className={styles.eyebrow}>Final summary</p><strong>{run.final_summary}</strong></div>
        </div>
      )}
      <CreatedOutputs outputs={run.created_outputs} />
      {run.error && <div className={styles.runError}>{run.error}</div>}
    </section>
  );
}

function PlanRow({ action, index }: { action: PlanAction; index: number }) {
  const done = ["completed", "executed", "verified"].includes(action.status);
  return (
    <article className={styles.planItem}>
      <span className={styles.index}>{done ? <Check size={12} /> : String(index + 1).padStart(2, "0")}</span>
      <div>
        <strong>{action.description}</strong>
        <span><code>{action.tool_name}</code> · {action.server_name} MCP</span>
      </div>
      <span className={action.side_effecting ? styles.write : styles.read}>{action.side_effecting ? "Write" : "Read"}</span>
    </article>
  );
}

function PlanSkeleton() {
  return <div className={styles.planSkeleton} aria-label="Plan loading">{[1, 2, 3, 4].map((item) => <span key={item} />)}</div>;
}
