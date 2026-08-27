import { Check, Circle, Info, LoaderCircle, ShieldAlert, X } from "lucide-react";
import { useMemo, useState } from "react";

import type { TimelineEvent } from "@/lib/types";

import styles from "./workspace.module.css";

export function TimelinePanel({ events }: { events: TimelineEvent[] }) {
  const [showDetails, setShowDetails] = useState(false);
  const groupedEvents = useMemo(() => groupEvents(events), [events]);
  return (
    <aside className={styles.timelinePanel}>
      <div className={styles.sectionHeader}>
        <div><span className={styles.eyebrow}>Workflow</span><h2>Live timeline</h2></div>
        <button className={styles.sectionAction} onClick={() => setShowDetails((value) => !value)} aria-pressed={showDetails}>
          <Info size={13} />{showDetails ? "Less detail" : "Details"}
        </button>
      </div>
      <div className={styles.timeline}>
        {groupedEvents.length === 0 ? <p className={styles.emptyTimeline}>The workflow is starting…</p> : groupedEvents.map((event, index) => (
          <div className={`${styles.timelineItem} ${styles[event.state]}`} key={event.id}>
            <div className={styles.timelineTrack}>
              <span>{eventIcon(event)}</span>
              {index < groupedEvents.length - 1 && <i />}
            </div>
            <div className={styles.timelineContent}>
              <strong>{event.title}</strong>
              {(showDetails || isImportant(event)) && event.detail && <p>{event.detail}</p>}
              <small>{formatEventTime(event.created_at)} · {prettyEvent(event.event_type)}</small>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}

function groupEvents(events: TimelineEvent[]) {
  const grouped: TimelineEvent[] = [];
  for (let index = 0; index < events.length; index += 1) {
    const event = events[index];
    const next = events[index + 1];
    if (event.event_type === "tool_called" && next && (next.event_type === "tool_completed" || next.event_type === "tool_failed")) {
      grouped.push({ ...next, title: event.title, payload: { ...event.payload, ...next.payload } });
      index += 1;
    } else {
      grouped.push(event);
    }
  }
  return grouped;
}

function isImportant(event: TimelineEvent) {
  return event.state === "failed" || ["approval_required", "plan_revised", "plan_generated", "run_completed", "request_understood"].includes(event.event_type);
}

function eventIcon(event: TimelineEvent) {
  if (event.state === "failed") return <X size={10} />;
  if (event.state === "waiting_for_approval") return <ShieldAlert size={10} />;
  if (event.state === "running") return <LoaderCircle size={10} className={styles.spin} />;
  if (event.state === "completed") return <Check size={10} />;
  return <Circle size={8} />;
}

function formatEventTime(value: string) {
  return new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function prettyEvent(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
