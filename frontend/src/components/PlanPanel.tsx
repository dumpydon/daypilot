import {
  Check,
  ChevronDown,
  FilePenLine,
  List,
  Network,
  ShieldAlert,
  ShieldCheck,
  X,
} from "lucide-react";
import { useState } from "react";

import type { PlanAction, RunDetail } from "@/lib/types";

import { CreatedOutputs } from "./CreatedOutputs";
import { PlanDependencyGraph } from "./PlanDependencyGraph";
import styles from "./workspace.module.css";

interface PlanPanelProps {
  run: RunDetail;
  busy: boolean;
  pending?: boolean;
  onApprove: () => void;
  onReject: () => void;
  onEdit: (feedback: string) => void;
}

export function PlanPanel({ run, busy, pending = false, onApprove, onReject, onEdit }: PlanPanelProps) {
  const [editing, setEditing] = useState(false);
  const [feedback, setFeedback] = useState("");
  const hasDependencies = run.plan.some((action) => action.depends_on.length > 0);
  const meaningfulDependencyGraph = hasDependencies && run.plan.length >= 3;
  const [planView, setPlanView] = useState<"list" | "graph">(
    meaningfulDependencyGraph ? "graph" : "list",
  );
  const [selectedActionId, setSelectedActionId] = useState<string | null>(null);
  const writes = run.plan.filter((action) => action.side_effecting);
  const waiting = run.status === "waiting_approval";
  const resolved = ["completed", "failed", "rejected"].includes(run.status);
  const answerOnly = resolved
    && run.plan.length === 0
    && ["general", "research"].includes(run.intent?.request_kind ?? "");
  const sources = researchSources(run);
  const hasFailedOutput = run.created_outputs.some(
    (output) => output.status === "failed"
      || output.status === "partially_completed"
      || (output.resource_type === "task_batch" && output.verified && output.items.length === 0),
  );
  const [executedPlanOpen, setExecutedPlanOpen] = useState(
    run.status !== "completed" || hasFailedOutput,
  );
  const hasUnverifiedOutput = run.created_outputs.some(
    (output) => output.status === "created" || !output.verified,
  );
  const summaryTone = run.status === "failed" || hasFailedOutput
    ? styles.failedSummary
    : hasUnverifiedOutput
      ? styles.warningSummary
      : run.status === "rejected"
        ? styles.rejectedSummary
        : "";

  return (
    <section className={`${styles.planPanel} ${resolved ? styles.planPanelResolved : ""} ${waiting ? styles.planPanelApproval : ""} ${pending ? styles.planPanelPending : ""}`}>
      <div className={styles.sectionHeader}>
        <div>
          <span className={styles.eyebrow}>{pending ? "Run starting" : resolved ? "Run result" : "Proposed plan"}</span>
          <h2>
            {pending ? "Preparing your request" : resolved ? resultHeading(run.status, hasFailedOutput) : run.plan.length ? `${run.plan.length} coordinated actions` : "Building action plan"}
            {!resolved && run.plan_revision > 0 && (
              <span className={styles.revision}>Revision {run.plan_revision}</span>
            )}
          </h2>
        </div>
        <div className={styles.planHeaderActions}>
          {!resolved && run.plan.length >= 2 && (
            <PlanViewToggle planView={planView} onChange={setPlanView} />
          )}
          {!resolved && writes.length > 0 && (
            <span className={styles.writeCount}>
              {writes.length} {writes.length === 1 ? "change requires" : "changes require"} approval
            </span>
          )}
        </div>
      </div>

      {resolved && run.error && (
        <div className={`${styles.runError} ${styles.runErrorProminent}`}>
          <ShieldAlert size={16} /><span>{run.error}</span>
        </div>
      )}
      {resolved && run.final_summary && <FinalSummary run={run} summaryTone={summaryTone} />}
      {resolved && sources.length > 0 && <ResearchSources sources={sources} />}
      {resolved && <CreatedOutputs outputs={run.created_outputs} />}

      {run.plan.length === 0 ? resolved ? (
        answerOnly ? null : (
          <p className={styles.emptyPlan}>No executable plan was produced for this run.</p>
        )
      ) : <PlanSkeleton pending={pending} /> : resolved ? (
        <>
          {meaningfulDependencyGraph && (
            <section className={styles.completedPlanSection} aria-labelledby={`plan-heading-${run.id}`}>
              <div className={styles.completedPlanSectionHeader}>
                <div>
                  <span className={styles.eyebrow}>Plan</span>
                  <h2 id={`plan-heading-${run.id}`}>How the actions connect</h2>
                </div>
                <PlanViewToggle planView={planView} onChange={setPlanView} />
              </div>
              {planView === "graph" ? (
                <PlanDependencyGraph
                  actions={run.plan}
                  runStatus={run.status}
                  selectedActionId={selectedActionId}
                  onSelectAction={setSelectedActionId}
                />
              ) : (
                <div className={styles.planList}>
                  {run.plan.map((action, index) => (
                    <PlanRow
                      action={action}
                      index={index}
                      key={action.id}
                      selected={selectedActionId === action.id}
                      onSelect={() => setSelectedActionId(action.id)}
                    />
                  ))}
                </div>
              )}
            </section>
          )}
          <details
            className={styles.completedPlan}
            open={executedPlanOpen}
            onToggle={(event) => setExecutedPlanOpen(event.currentTarget.open)}
          >
            <summary className={styles.completedPlanHeader}>
              <div>
                <span>Executed actions</span>
                <small>{run.plan.length} {run.plan.length === 1 ? "step" : "steps"} · Tool details</small>
              </div>
              <ChevronDown size={15} />
            </summary>
            <div className={styles.planList}>
              {run.plan.map((action, index) => (
                <PlanRow
                  action={action}
                  index={index}
                  key={action.id}
                  selected={selectedActionId === action.id}
                  onSelect={() => setSelectedActionId(action.id)}
                />
              ))}
            </div>
          </details>
        </>
      ) : (
        planView === "graph" ? (
          <PlanDependencyGraph
            actions={run.plan}
            runStatus={run.status}
            selectedActionId={selectedActionId}
            onSelectAction={setSelectedActionId}
          />
        ) : (
          <div className={styles.planList}>
            {run.plan.map((action, index) => (
              <PlanRow
                action={action}
                index={index}
                key={action.id}
                selected={selectedActionId === action.id}
                onSelect={() => setSelectedActionId(action.id)}
              />
            ))}
          </div>
        )
      )}

      {waiting && (
        <div className={styles.approval}>
          <div className={styles.approvalIcon}><ShieldAlert size={15} /></div>
          <div className={styles.approvalCopy}>
            <span className={styles.eyebrow}>Approval required · {writes.length} external {writes.length === 1 ? "change" : "changes"}</span>
            <h3>{writes.length === 1 ? writes[0].description : "Review the exact proposed changes"}</h3>
            <p>Nothing will be changed until you approve this plan.</p>
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

      {!resolved && run.final_summary && <FinalSummary run={run} summaryTone={summaryTone} />}
      {!resolved && <CreatedOutputs outputs={run.created_outputs} />}
      {!resolved && run.error && <div className={styles.runError}>{run.error}</div>}
    </section>
  );
}

function PlanViewToggle({
  planView,
  onChange,
}: {
  planView: "list" | "graph";
  onChange: (view: "list" | "graph") => void;
}) {
  return (
    <div className={styles.planViewToggle} role="group" aria-label="Plan view">
      <button
        type="button"
        className={planView === "list" ? styles.planViewActive : ""}
        aria-pressed={planView === "list"}
        onClick={() => onChange("list")}
      >
        <List size={13} />Sequence
      </button>
      <button
        type="button"
        className={planView === "graph" ? styles.planViewActive : ""}
        aria-pressed={planView === "graph"}
        onClick={() => onChange("graph")}
      >
        <Network size={13} />Dependencies
      </button>
    </div>
  );
}

function ResearchSources({
  sources,
}: {
  sources: Array<{ title: string; url: string }>;
}) {
  return (
    <div className={styles.researchSources} aria-label="Web research sources">
      <span>Sources</span>
      <div>
        {sources.map((source) => (
          <a key={source.url} href={source.url} target="_blank" rel="noreferrer">
            {source.title}
          </a>
        ))}
      </div>
    </div>
  );
}

function researchSources(run: RunDetail) {
  const record = run.context.web?.findLast(
    (item) => item.tool_name === "search_web" && item.success,
  );
  const raw = record?.result?.sources;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((source) => {
    if (!source || typeof source !== "object") return [];
    const title = "title" in source ? source.title : null;
    const url = "url" in source ? source.url : null;
    return typeof title === "string"
      && typeof url === "string"
      && url.startsWith("http")
      ? [{ title, url }]
      : [];
  }).slice(0, 4);
}

function FinalSummary({ run, summaryTone }: { run: RunDetail; summaryTone: string }) {
  const caution = summaryTone === styles.failedSummary || summaryTone === styles.warningSummary;
  const label = run.status === "rejected" ? "Closed safely" : caution ? "Needs attention" : "Done";
  return (
    <div className={`${styles.summary} ${summaryTone}`}>
      <span>
        {run.status === "rejected" ? <ShieldCheck size={17} /> : caution ? <ShieldAlert size={17} /> : <Check size={17} />}
      </span>
      <div><p>{label}</p><strong>{run.final_summary}</strong></div>
    </div>
  );
}

function PlanRow({
  action,
  index,
  selected,
  onSelect,
}: {
  action: PlanAction;
  index: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const done = ["completed", "executed", "verified"].includes(action.status);
  return (
    <article
      className={`${styles.planItem} ${action.side_effecting ? styles.planItemWrite : styles.planItemRead} ${done ? styles.planItemDone : ""} ${selected ? styles.planItemSelected : ""}`}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      data-action-id={action.id}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
    >
      <span className={styles.index}>{done ? <Check size={12} /> : String(index + 1).padStart(2, "0")}</span>
      <div>
        <strong>{action.description}</strong>
        <span className={styles.planMeta}><span>{serviceLabel(action.server_name)}</span><code>{action.tool_name}</code></span>
      </div>
      <span className={action.side_effecting ? styles.write : styles.read}>{action.side_effecting ? "Write" : "Read"}</span>
    </article>
  );
}

function PlanSkeleton({ pending = false }: { pending?: boolean }) {
  return (
    <div className={styles.planSkeleton} aria-label="Plan loading">
      <p><i />{pending ? "Starting run…" : "DayPilot is preparing the next actions…"}</p>
      <div><span /><span /><span /></div>
    </div>
  );
}

function resultHeading(status: RunDetail["status"], hasFailedOutput: boolean) {
  if (status === "failed" || hasFailedOutput) return "Run needs attention";
  if (status === "rejected") return "Run closed safely";
  return "Execution complete";
}

function serviceLabel(serverName: string) {
  const labels: Record<string, string> = {
    web: "Web research",
    mail: "Mail",
    calendar: "Calendar",
    tasks: "Google Tasks",
    files: "Workspace files",
    x: "X",
  };
  return labels[serverName] ?? serverName;
}
