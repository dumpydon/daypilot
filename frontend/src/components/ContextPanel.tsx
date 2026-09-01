import { AtSign, CalendarDays, CheckSquare2, ChevronDown, Files, Globe2, Mail } from "lucide-react";
import { useState } from "react";

import type { ContextRecord } from "@/lib/types";

import styles from "./workspace.module.css";

export function ContextPanel({ context }: { context: Record<string, ContextRecord[]> }) {
  const [expanded, setExpanded] = useState(false);
  const failures = Object.entries(context).flatMap(([service, records]) => (
    records.filter((record) => !record.success && record.error).map((record) => ({
      service,
      error: record.error as string,
    }))
  ));
  const mail = context.mail?.findLast((record) => record.tool_name === "get_thread" && record.success)
    ?? context.mail?.findLast((record) => record.tool_name === "search_mail" && record.success);
  const events = context.calendar?.findLast((record) => record.tool_name === "list_events" && record.success);
  const slots = context.calendar?.findLast((record) => record.tool_name === "find_free_slots" && record.success);
  const tasks = context.tasks?.findLast((record) => record.tool_name === "list_tasks" && record.success);
  const files = context.files?.findLast((record) => record.tool_name === "read_file" && record.success)
    ?? context.files?.findLast((record) => record.tool_name === "search_files" && record.success);
  const posts = context.x?.findLast((record) => ["search_posts", "get_user_posts", "get_post"].includes(record.tool_name) && record.success);
  const web = context.web?.findLast((record) => record.tool_name === "search_web" && record.success);
  const webAttempted = context.web?.some((record) => record.tool_name === "search_web") ?? false;
  const mailMessages = mail?.result?.messages;
  const mailThreads = mail?.result?.threads;
  const calendarEvents = events?.result?.events;
  const taskItems = tasks?.result?.tasks;
  const fileItems = files?.result?.files;
  const postItems = posts?.result?.posts;
  const webSources = web?.result?.sources;

  return (
    <section className={`${styles.contextPanel} ${expanded ? styles.contextExpanded : ""}`}>
      <div className={styles.sectionHeader}>
        <div><span className={styles.eyebrow}>Evidence</span><h2>Context used</h2></div>
        <button className={styles.sectionAction} onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
          {failures.length > 0 && <span className={styles.contextIssueCount}>{failures.length} issue{failures.length === 1 ? "" : "s"}</span>}
          {expanded ? "Hide details" : "View details"}<ChevronDown size={13} className={expanded ? styles.chevronOpen : ""} />
        </button>
      </div>
      <div className={styles.contextSummary}>
        <ContextStat icon={<Globe2 size={14} />} label="Web" value={Array.isArray(webSources) ? countLabel(webSources.length, "source") : webAttempted ? "Unavailable" : "Not queried"} />
        <ContextStat
          icon={<Mail size={14} />}
          label="Mail"
          value={
            Array.isArray(mailMessages)
              ? countLabel(mailMessages.length, "message")
              : Array.isArray(mailThreads)
                ? countLabel(mailThreads.length, "thread")
                : mail
                  ? "Queried"
                  : "Not queried"
          }
        />
        <ContextStat icon={<CalendarDays size={14} />} label="Calendar" value={Array.isArray(calendarEvents) ? countLabel(calendarEvents.length, "event") : slots ? "Availability found" : "Not queried"} />
        <ContextStat icon={<CheckSquare2 size={14} />} label="Tasks" value={Array.isArray(taskItems) ? countLabel(taskItems.length, "task") : "Not queried"} />
        <ContextStat icon={<Files size={14} />} label="Files" value={files?.result?.filename ? "1 file read" : Array.isArray(fileItems) ? countLabel(fileItems.length, "file") : "Not queried"} />
        <ContextStat icon={<AtSign size={14} />} label="X" value={Array.isArray(postItems) ? countLabel(postItems.length, "post") : posts?.result?.text ? "1 post read" : "Not queried"} />
      </div>
      {expanded && (
        <>
          <div className={styles.contextDetails}>
            <ContextCard icon={<Globe2 size={13} />} label="Web" title={webTitle(web)} detail={webDetail(web)} />
            <ContextCard icon={<Mail size={13} />} label="Mail" title={mailTitle(mail)} detail={mailDetail(mail)} />
            <ContextCard icon={<CalendarDays size={13} />} label="Calendar" title={calendarTitle(events, slots)} detail={calendarDetail(events, slots)} />
            <ContextCard icon={<CheckSquare2 size={13} />} label="Tasks" title={taskTitle(tasks)} detail="Grounded task IDs remain available to the planner." />
            <ContextCard icon={<Files size={13} />} label="Files" title={fileTitle(files)} detail={fileDetail(files)} />
            <ContextCard icon={<AtSign size={13} />} label="X" title={postTitle(posts)} detail={postDetail(posts)} />
          </div>
          {failures.length > 0 && (
            <div className={styles.contextIssues} role="status">
              {failures.map((failure, index) => (
                <p key={`${failure.service}-${index}`}><strong>{failure.service}</strong>{failure.error}</p>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function ContextStat({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return <div className={styles.contextStat}><span>{icon}</span><div><strong>{label}</strong><small>{value}</small></div></div>;
}

function ContextCard({ icon, label, title, detail }: { icon: React.ReactNode; label: string; title: string; detail: string }) {
  return <article><span>{icon}{label}</span><strong>{title}</strong><p>{detail}</p></article>;
}

function mailTitle(record?: ContextRecord) {
  const result = record?.result;
  if (typeof result?.subject === "string") return result.subject;
  const threads = result?.threads;
  if (Array.isArray(threads)) {
    return `${threads.length} matching mail thread${threads.length === 1 ? "" : "s"}`;
  }
  return "No matching thread read";
}

function webTitle(record?: ContextRecord) {
  const sources = record?.result?.sources;
  return Array.isArray(sources)
    ? `${sources.length} public source${sources.length === 1 ? "" : "s"} reviewed`
    : "Web not researched";
}

function webDetail(record?: ContextRecord) {
  return typeof record?.result?.query === "string"
    ? `Research query: ${record.result.query}`
    : "Fresh public evidence will appear after a successful search.";
}

function mailDetail(record?: ContextRecord) {
  const messages = record?.result?.messages;
  if (Array.isArray(messages)) {
    return `${messages.length} grounded message${messages.length === 1 ? "" : "s"} retrieved.`;
  }
  const threads = record?.result?.threads;
  if (Array.isArray(threads)) {
    return `${threads.length} matching mail thread${threads.length === 1 ? "" : "s"} returned by search.`;
  }
  return "Mail facts will appear after a successful read.";
}

function calendarTitle(events?: ContextRecord, slots?: ContextRecord) {
  const slotList = slots?.result?.slots;
  if (Array.isArray(slotList) && slotList.length > 0) return "Suitable free time found";
  const eventList = events?.result?.events;
  if (Array.isArray(eventList)) return `${eventList.length} calendar event${eventList.length === 1 ? "" : "s"} found`;
  return "Calendar not read yet";
}

function calendarDetail(events?: ContextRecord, slots?: ContextRecord) {
  const slotList = slots?.result?.slots as Array<Record<string, unknown>> | undefined;
  if (slotList?.length && typeof slotList[0].start === "string" && typeof slotList[0].end === "string") {
    return `First slot: ${formatRange(slotList[0].start, slotList[0].end)}.`;
  }
  const eventList = events?.result?.events;
  return Array.isArray(eventList) ? "Availability was calculated from persisted events." : "Tool-confirmed availability will appear here.";
}

function taskTitle(record?: ContextRecord) {
  const tasks = record?.result?.tasks;
  return Array.isArray(tasks) ? `${tasks.length} existing tasks reviewed` : "Tasks not read yet";
}

function fileTitle(record?: ContextRecord) {
  const result = record?.result;
  return typeof result?.filename === "string" ? result.filename : Array.isArray(result?.files) ? `${result.files.length} matching files` : "Files not read yet";
}

function fileDetail(record?: ContextRecord) {
  const result = record?.result;
  return typeof result?.content === "string" ? "Content was read from the controlled workspace." : "Controlled file metadata will appear after a successful search.";
}

function postTitle(record?: ContextRecord) {
  const result = record?.result;
  if (Array.isArray(result?.posts)) return `${result.posts.length} public post${result.posts.length === 1 ? "" : "s"} found`;
  return typeof result?.text === "string" ? "Public post read" : "X posts not read yet";
}

function postDetail(record?: ContextRecord) {
  const result = record?.result;
  return typeof result?.source === "string" ? "Demo public context is grounded in the connected X MCP." : "Public post context will appear after a successful search.";
}

function formatRange(start: string, end: string) {
  const formatter = new Intl.DateTimeFormat("en", { hour: "numeric", minute: "2-digit" });
  return `${formatter.format(new Date(start))}–${formatter.format(new Date(end))}`;
}

function countLabel(count: number, noun: string) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}
