import { AtSign, CalendarDays, ChevronDown, Files, ListChecks, Mail, PlugZap } from "lucide-react";
import { type Ref, useState } from "react";

import type { ToolCatalog } from "@/lib/types";

import styles from "./workspace.module.css";

const icons = { mail: Mail, calendar: CalendarDays, tasks: ListChecks, files: Files, x: AtSign };

interface ToolInspectorProps {
  catalog: ToolCatalog;
  collapsible?: boolean;
  defaultOpen?: boolean;
  /** Optional controlled open state for surfaces that share this inspector with another control. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Optional controlled server selection. `null` means no server is expanded. */
  expandedServer?: string | null;
  onExpandedServerChange?: (serverName: string | null) => void;
  sectionRef?: Ref<HTMLElement>;
}

export function ToolInspector({
  catalog,
  collapsible = false,
  defaultOpen = false,
  open: openProp,
  onOpenChange,
  expandedServer: expandedServerProp,
  onExpandedServerChange,
  sectionRef,
}: ToolInspectorProps) {
  const [internalExpanded, setInternalExpanded] = useState<string | null>(null);
  const [internalOpen, setInternalOpen] = useState(collapsible ? defaultOpen : true);
  const open = openProp ?? internalOpen;
  const expanded = expandedServerProp !== undefined ? expandedServerProp : internalExpanded;
  const connected = catalog.servers.filter((server) => server.connected).length;
  const totalTools = catalog.servers.reduce((sum, server) => sum + server.tool_count, 0);

  function setInspectorOpen(nextOpen: boolean) {
    onOpenChange?.(nextOpen);
    if (!onOpenChange) setInternalOpen(nextOpen);
  }

  function setExpandedServer(serverName: string | null) {
    onExpandedServerChange?.(serverName);
    if (!onExpandedServerChange) setInternalExpanded(serverName);
  }

  return (
    <section
      ref={sectionRef}
      id="connected-capabilities"
      className={`${styles.toolsPanel} ${styles.inspector} ${open ? styles.inspectorOpen : ""}`}
    >
      <div className={styles.inspectorHeader}>
        <div><span className={styles.eyebrow}>Tools</span><h2>Connected capabilities</h2><small>{connected} MCP servers · {totalTools} tools</small></div>
        <button
          type="button"
          className={styles.inspectorToggle}
          onClick={() => setInspectorOpen(!open)}
          aria-expanded={open}
          aria-controls="connected-capabilities-details"
          aria-label={open ? "Collapse connected capabilities" : "Expand connected capabilities"}
        >
          {collapsible ? <ChevronDown size={15} className={open ? styles.chevronOpen : ""} /> : <PlugZap size={14} className={styles.panelIcon} />}
        </button>
      </div>
      {open && <div className={styles.serverGrid} id="connected-capabilities-details">
        {catalog.servers.map((server) => {
          const Icon = icons[server.name as keyof typeof icons] ?? PlugZap;
          const isOpen = expanded === server.name;
          const providerConnected = server.connected && (server.provider_state
            ? server.provider_state === "connected"
            : true);
          const provider = server.provider ?? "DayPilot demo";
          const modeLabel = server.connection_mode === "managed"
            ? "Managed"
            : server.connection_mode === "direct"
              ? "Direct"
              : null;
          const providerLabel = modeLabel ? `${provider} · ${modeLabel}` : provider;
          const requiresReconnect = server.provider_state === "error"
            || server.provider_state === "reconnect_required";
          return (
            <div className={styles.server} key={server.name}>
              <button
                type="button"
                aria-expanded={isOpen}
                aria-controls={`tools-${server.name}`}
                onClick={() => setExpandedServer(isOpen ? null : server.name)}
              >
                <span className={styles.serverIcon}><Icon size={13} /></span>
                <span><strong>{title(server.name)} MCP</strong><small><i className={providerConnected ? styles.okDot : styles.errorDot} />{providerLabel} · {providerConnected ? `Connected · ${server.tool_count} tools` : server.provider_state === "connecting" ? "Connecting" : server.provider_state === "unavailable" ? "Unavailable" : requiresReconnect ? "Reconnect required" : "Not connected"}</small></span>
                <ChevronDown size={13} className={isOpen ? styles.chevronOpen : ""} />
              </button>
              {isOpen && <div className={styles.toolList} id={`tools-${server.name}`}>{catalog.tools.filter((tool) => tool.server_name === server.name).map((tool) => <div key={tool.name}><code>{tool.name}</code><span className={tool.side_effecting ? styles.write : styles.read}>{tool.side_effecting ? "Write" : "Read"}</span></div>)}</div>}
            </div>
          );
        })}
      </div>}
    </section>
  );
}

function title(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
