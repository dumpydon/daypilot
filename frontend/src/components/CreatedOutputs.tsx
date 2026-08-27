import {
  AtSign,
  CalendarDays,
  CheckCircle2,
  CircleAlert,
  ExternalLink,
  FileCheck2,
  Info,
  ListChecks,
  Mail,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

import type { ResourceReceipt, ReceiptStatus } from "@/lib/types";

import styles from "./workspace.module.css";

interface CreatedOutputsProps {
  outputs: ResourceReceipt[];
}

export function CreatedOutputs({ outputs }: CreatedOutputsProps) {
  const [selected, setSelected] = useState<ResourceReceipt | null>(null);

  useEffect(() => {
    if (!selected) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selected]);

  if (outputs.length === 0) return null;
  const hasFailure = outputs.some((output) => output.status === "failed" || output.status === "partially_completed");

  return (
    <section className={styles.outputs} aria-labelledby="created-outputs-heading">
      <div className={styles.outputsHeader}>
        <div>
          <span className={styles.eyebrow}>Execution receipt</span>
          <h3 id="created-outputs-heading">Created outputs</h3>
        </div>
        <span className={`${styles.outputsCount} ${hasFailure ? styles.outputsCountWarning : ""}`}>
          {outputs.length} {outputs.length === 1 ? "output" : "outputs"}
        </span>
      </div>
      <div className={styles.outputList}>
        {outputs.map((output) => (
          <OutputRow key={output.action_id} output={output} onOpen={() => setSelected(output)} />
        ))}
      </div>
      {selected && <OutputDetails output={selected} onClose={() => setSelected(null)} />}
    </section>
  );
}

function OutputRow({ output, onOpen }: { output: ResourceReceipt; onOpen: () => void }) {
  const icon = iconFor(output.resource_type);
  const actionLabel = actionLabelFor(output);
  const content = (
    <>
      <span>{actionLabel}</span>
      <ExternalLink size={12} />
    </>
  );

  return (
    <article className={`${styles.outputRow} ${styles[`output_${output.status}`]}`}>
      <span className={styles.outputIcon}>{icon}</span>
      <div className={styles.outputCopy}>
        <div className={styles.outputMeta}>
          <span>{output.provider}</span>
          <StatusBadge status={output.status} verified={output.verified} />
        </div>
        <strong>{output.title}</strong>
        {output.secondary_text && <p>{output.secondary_text}</p>}
        {output.items.length > 0 && (
          <ul className={styles.outputItems}>
            {output.items.map((item, index) => (
              <li key={item.resource_id ?? `${item.title}-${index}`}>
                <span>{item.title}</span>
                {item.secondary_text && <small>{item.secondary_text}</small>}
              </li>
            ))}
          </ul>
        )}
        {output.error && <p className={styles.outputError}>{output.error}</p>}
      </div>
      <div className={styles.outputAction}>
        {output.external_url ? (
          <a href={output.external_url} target="_blank" rel="noreferrer">
            {content}
          </a>
        ) : (
          <button type="button" onClick={onOpen}>
            {content}
          </button>
        )}
      </div>
    </article>
  );
}

function StatusBadge({ status, verified }: { status: ReceiptStatus; verified: boolean }) {
  const label = statusLabel(status, verified);
  return (
    <span className={styles.outputStatus}>
      {status === "verified" ? <CheckCircle2 size={12} /> : status === "failed" || status === "partially_completed" ? <CircleAlert size={12} /> : <Info size={12} />}
      {label}
    </span>
  );
}

function OutputDetails({ output, onClose }: { output: ResourceReceipt; onClose: () => void }) {
  return (
    <div
      className={styles.outputDetailsBackdrop}
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className={styles.outputDetails} role="dialog" aria-modal="true" aria-labelledby="output-details-heading">
        <div className={styles.outputDetailsHeader}>
          <div>
            <span className={styles.eyebrow}>{output.provider}</span>
            <h4 id="output-details-heading">{output.title}</h4>
          </div>
          <button type="button" aria-label="Close output details" onClick={onClose}><X size={15} /></button>
        </div>
        <div className={styles.outputDetailsStatus}>
          <StatusBadge status={output.status} verified={output.verified} />
          {output.resource_id && <code>{output.resource_id}</code>}
        </div>
        {output.details.length > 0 && (
          <dl className={styles.outputDetailsList}>
            {output.details.map((detail) => (
              <div key={`${detail.label}-${detail.value}`}>
                <dt>{detail.label}</dt>
                <dd>{detail.value}</dd>
              </div>
            ))}
          </dl>
        )}
        {output.items.length > 0 && (
          <div className={styles.outputDetailsItems}>
            <span className={styles.eyebrow}>Items</span>
            {output.items.map((item, index) => (
              <div key={item.resource_id ?? `${item.title}-${index}`}>
                <strong>{item.title}</strong>
                {item.secondary_text && <small>{item.secondary_text}</small>}
                {item.resource_id && <code>{item.resource_id}</code>}
              </div>
            ))}
          </div>
        )}
        {output.error && <p className={styles.outputDetailsError}>{output.error}</p>}
        {output.verification_detail && <p className={styles.outputDetailsVerification}>{output.verification_detail}</p>}
        <button className={styles.outputDetailsClose} type="button" onClick={onClose}>Close</button>
      </section>
    </div>
  );
}

function iconFor(resourceType: string) {
  if (resourceType === "calendar_event") return <CalendarDays size={16} />;
  if (resourceType === "mail_draft") return <Mail size={16} />;
  if (resourceType === "x_post" || resourceType === "x_post_draft") return <AtSign size={16} />;
  if (resourceType === "task" || resourceType === "task_batch") return <ListChecks size={16} />;
  return <FileCheck2 size={16} />;
}

function actionLabelFor(output: ResourceReceipt) {
  if (output.external_url) {
    if (output.resource_type === "calendar_event") return "View event";
    if (output.resource_type === "x_post") return "View post";
    return "Open resource";
  }
  if (output.resource_type === "calendar_event") return "View event";
  return "View details";
}

function statusLabel(status: ReceiptStatus, verified: boolean) {
  if (status === "verified" && verified) return "Verified";
  if (status === "created") return "Created · verification unavailable";
  if (status === "partially_completed") return "Partially completed";
  return "Failed";
}
