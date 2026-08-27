import { AtSign, CalendarDays, Files, ListChecks, Mail, type LucideIcon } from "lucide-react";

import type { ToolCatalog } from "@/lib/types";

import styles from "./workspace.module.css";

interface CapabilityStripProps {
  catalog: ToolCatalog;
  selectedServer: string | null;
  onSelect: (serverName: string) => void;
}

interface CapabilityDefinition {
  serverName: "mail" | "calendar" | "tasks" | "files" | "x";
  label: string;
  description: string;
  Icon: LucideIcon;
}

const capabilities: CapabilityDefinition[] = [
  { serverName: "mail", label: "Mail", description: "Find conversations and create drafts", Icon: Mail },
  { serverName: "calendar", label: "Calendar", description: "Check availability and schedule time", Icon: CalendarDays },
  { serverName: "tasks", label: "Tasks", description: "Review work and create tasks", Icon: ListChecks },
  { serverName: "files", label: "Files", description: "Find and read workspace documents", Icon: Files },
  { serverName: "x", label: "X", description: "Research posts and prepare publishing", Icon: AtSign },
];

export function CapabilityStrip({ catalog, selectedServer, onSelect }: CapabilityStripProps) {
  return (
    <div className={styles.capabilityCards} aria-label="DayPilot capabilities">
      {capabilities.map(({ serverName, label, description, Icon }) => {
        const server = catalog.servers.find((candidate) => candidate.name === serverName);
        const connected = server?.connected ?? false;
        const toolCount = server?.tool_count ?? 0;
        const status = connected ? "Connected" : "Disconnected";
        const selected = selectedServer === serverName;

        return (
          <button
            type="button"
            key={serverName}
            className={`${styles.capabilityCard} ${selected ? styles.capabilityCardSelected : ""}`}
            aria-pressed={selected}
            aria-label={`${label} capability, ${status}, ${toolCount} tools`}
            data-testid={`capability-card-${serverName}`}
            onClick={() => onSelect(serverName)}
          >
            <span className={styles.capabilityCardIcon} aria-hidden="true"><Icon size={15} /></span>
            <span className={styles.capabilityCardCopy}>
              <strong>{label}</strong>
              <span>{description}</span>
            </span>
            <span className={`${styles.capabilityCardMeta} ${connected ? styles.capabilityConnected : styles.capabilityDisconnected}`} title={status}>
              <i aria-hidden="true" />
              {toolCount} tools
            </span>
          </button>
        );
      })}
    </div>
  );
}
