import { ArrowDown, Check, Circle, Info, ShieldAlert, X } from "lucide-react";
import { useMemo, useState } from "react";

import type { RunStatus, TimelineEvent } from "@/lib/types";

import { useFollowTimeline } from "./useFollowTimeline";
import styles from "./workspace.module.css";

interface TimelinePanelProps {
  events: TimelineEvent[];
  runId?: string;
  runStatus?: RunStatus;
  pending?: boolean;
}

export function TimelinePanel({ events, runId, runStatus, pending = false }: TimelinePanelProps) {
  const [showDetails, setShowDetails] = useState(false);
  const groupedEvents = useMemo(() => groupEvents(events), [events]);
  const effectiveStatus = runStatus ?? inferredStatus(groupedEvents);
  const isLive = ["queued", "running", "resuming"].includes(effectiveStatus);
  const resolvedRunId = runId ?? groupedEvents.at(-1)?.run_id ?? "timeline";
  const latest = groupedEvents.at(-1);
  const currentIndex = currentEventIndex(groupedEvents, effectiveStatus);
  const currentEvent = currentIndex >= 0 ? groupedEvents[currentIndex] : latest;
  const {
    containerRef,
    following,
    newEvents,
    showJumpToLatest,
    onScroll,
    markUserScrollIntent,
    jumpToLatest,
  } = useFollowTimeline({
    runId: resolvedRunId,
    itemKey: latest ? `${latest.id}-${latest.event_type}-${latest.state}` : "empty",
    itemCount: groupedEvents.length,
    isLive,
    startAtLatest: isLive || effectiveStatus === "waiting_approval",
  });

  return (
    <aside className={styles.timelinePanel}>
      <div className={`${styles.sectionHeader} ${styles.timelineHeader}`}>
        <div className={styles.timelineTitle}>
          <span>Activity</span>
          <h2>Live timeline</h2>
        </div>
        <div className={styles.timelineHeaderActions}>
          {showJumpToLatest && (
            <button className={styles.jumpLatest} type="button" onClick={jumpToLatest}>
              <ArrowDown size={13} />
              <span>Follow live</span>
              {newEvents > 0 && <b>{newEvents} new</b>}
            </button>
          )}
          <span className={`${styles.timelineStatus} ${styles[`timelineStatus_${effectiveStatus}`]}`}>
            <i />{timelineStatusLabel(effectiveStatus)}
          </span>
          <button
            className={styles.sectionAction}
            onClick={() => setShowDetails((value) => !value)}
            aria-pressed={showDetails}
          >
            <Info size={14} />{showDetails ? "Less" : "Details"}
          </button>
        </div>
      </div>
      {currentEvent && (
        <div className={`${styles.timelineNow} ${isLive ? styles.timelineNowLive : ""}`} aria-live="polite">
          <span />
          <strong>{currentEvent.title}</strong>
        </div>
      )}
      <div className={styles.timelineShell}>
        <div
          className={styles.timelineViewport}
          ref={containerRef}
          data-testid="timeline-scroll"
          data-follow-live={following ? "true" : "false"}
          onScroll={onScroll}
          onWheel={markUserScrollIntent}
          onTouchStart={markUserScrollIntent}
          onKeyDown={markUserScrollIntent}
          tabIndex={0}
          aria-label="Workflow timeline events"
        >
          <div className={styles.timeline}>
            {groupedEvents.length === 0 ? (
              <p className={`${styles.emptyTimeline} ${pending ? styles.timelinePending : ""}`}>
                {pending && <i className={styles.timelinePendingDot} aria-hidden="true" />}
                {pending ? "Starting run…" : "The workflow is starting…"}
              </p>
            ) : groupedEvents.map((event, index) => {
              const current = index === currentIndex;
              const visualState = !isLive && event.state === "running"
                ? "completed"
                : event.state;
              return (
                <div
                  className={`${styles.timelineItem} ${styles[visualState]} ${current ? styles.timelineCurrent : ""}`}
                  key={event.id}
                  aria-current={current ? "step" : undefined}
                  data-current-step={current ? "true" : "false"}
                >
                  <div className={styles.timelineTrack}>
                    <span>{eventIcon(visualState)}</span>
                    {index < groupedEvents.length - 1 && <i />}
                    {current && isLive && (
                      <b className={styles.runningTrail} aria-hidden="true" data-testid="running-trail">
                        <i /><i /><i />
                      </b>
                    )}
                  </div>
                  <div className={styles.timelineContent}>
                    <strong>{event.title}</strong>
                    {(showDetails || isImportant(event)) && event.detail && <p>{event.detail}</p>}
                    <small>
                      <time>{formatEventTime(event.created_at)}</time>
                      {showDetails && <span>{prettyEvent(event.event_type)}</span>}
                    </small>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        <span className={styles.srOnly} aria-live="polite">
          {newEvents > 0 ? `${newEvents} new workflow events` : ""}
        </span>
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

function inferredStatus(events: TimelineEvent[]): RunStatus {
  if (events.some((event) => event.state === "waiting_for_approval")) return "waiting_approval";
  if (events.some((event) => event.state === "running")) return "running";
  return "completed";
}

function currentEventIndex(events: TimelineEvent[], runStatus: RunStatus) {
  if (["completed", "failed", "rejected"].includes(runStatus)) return -1;
  return events.length - 1;
}

function eventIcon(state: TimelineEvent["state"]) {
  if (state === "failed") return <X size={10} />;
  if (state === "waiting_for_approval") return <ShieldAlert size={10} />;
  if (state === "running") return <Circle size={8} fill="currentColor" />;
  if (state === "completed") return <Check size={10} />;
  return <Circle size={8} />;
}

function timelineStatusLabel(status: RunStatus) {
  if (["queued", "running", "resuming"].includes(status)) return "Running";
  if (status === "waiting_approval") return "Approval";
  if (status === "failed") return "Failed";
  if (status === "rejected") return "Rejected";
  return "Complete";
}

function formatEventTime(value: string) {
  return new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function prettyEvent(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
