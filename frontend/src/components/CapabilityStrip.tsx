import { AtSign, CalendarDays, Files, Globe2, ListChecks, Mail, type LucideIcon } from "lucide-react";

import type { ToolCatalog } from "@/lib/types";

import styles from "./workspace.module.css";

interface CapabilityStripProps {
  catalog: ToolCatalog;
  selectedServer: string | null;
  onSelect: (serverName: string) => void;
}

interface CapabilityDefinition {
  serverName: "web" | "mail" | "calendar" | "tasks" | "files" | "x";
  label: string;
  description: string;
  Icon: LucideIcon;
}

const capabilities: CapabilityDefinition[] = [
  { serverName: "web", label: "Web", description: "Research fresh public information", Icon: Globe2 },
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
        const providerConnected = connected && (server?.provider_state
          ? server.provider_state === "connected"
          : true);
        const toolCount = server?.tool_count ?? 0;
        const requiresReconnect = server?.provider_state === "error"
          || server?.provider_state === "reconnect_required";
        const status = providerConnected
          ? "Connected"
          : server?.provider_state === "connecting"
            ? "Connecting"
            : server?.provider_state === "unavailable"
              ? "Unavailable"
            : requiresReconnect
              ? "Reconnect required"
              : "Disconnected";
        const provider = server?.provider ?? "DayPilot demo";
        const modeLabel = server?.connection_mode === "managed"
          ? "Managed"
          : server?.connection_mode === "direct"
            ? "Direct"
            : null;
        const providerLabel = modeLabel ? `${provider} · ${modeLabel}` : provider;
        const selected = selectedServer === serverName;

        return (
          <button
            type="button"
            key={serverName}
            className={`${styles.capabilityCard} ${selected ? styles.capabilityCardSelected : ""}`}
            aria-pressed={selected}
            aria-label={`${label} capability, ${providerLabel}, ${status}, ${toolCount} ${toolCount === 1 ? "tool" : "tools"}`}
            data-testid={`capability-card-${serverName}`}
            onClick={() => onSelect(serverName)}
          >
            <span className={styles.capabilityCardIcon} aria-hidden="true"><Icon size={15} /></span>
            <span className={styles.capabilityCardCopy}>
              <strong>{label}</strong>
              <span>{description}</span>
              <small className={styles.capabilityCardProvider}>{providerLabel}</small>
            </span>
            <span className={`${styles.capabilityCardMeta} ${providerConnected ? styles.capabilityConnected : styles.capabilityDisconnected}`} title={status}>
              <i aria-hidden="true" />
              {toolCount} {toolCount === 1 ? "tool" : "tools"}
            </span>
          </button>
        );
      })}
    </div>
  );
}
