import { Activity, Cable, Menu } from "lucide-react";

import type { MCPServer } from "@/lib/types";

import { DayPilotLogo } from "./DayPilotLogo";
import styles from "./workspace.module.css";

interface HeaderProps {
  servers: MCPServer[];
  reasoningMode: string;
  onMenu: () => void;
  active?: boolean;
}

export function Header({ servers, reasoningMode, onMenu, active = false }: HeaderProps) {
  const connected = servers.filter((server) => server.connected).length;
  const openAIReady = reasoningMode === "openai";
  return (
    <header className={styles.header}>
      <button className={styles.mobileMenu} onClick={onMenu} aria-label="Open navigation">
        <Menu size={17} />
      </button>
      <div className={styles.brand}>
        <DayPilotLogo className={styles.mark} active={active} aria-label="DayPilot logo" />
        <div>
          <strong>DayPilot</strong>
          <span>MCP-powered personal operations</span>
        </div>
      </div>
      <div className={styles.runtime}>
        <span className={styles.demoBadge}>Demo workspace</span>
        <span><Cable size={13} /><i className={connected === servers.length ? styles.okDot : styles.warnDot} />{connected}/{servers.length} MCP servers</span>
        <span className={styles.runtimeStack} role="group" aria-label="Runtime status">
          <span>
            <Activity size={13} />
            <i
              className={openAIReady ? styles.okDot : styles.mutedDot}
              data-runtime-state={openAIReady ? "ready" : "unavailable"}
              data-testid="openai-runtime-indicator"
            />
            {openAIReady ? "OpenAI runtime" : "OpenAI unavailable"}
          </span>
          <span><Activity size={13} /><i className={styles.okDot} />Local runtime</span>
        </span>
      </div>
    </header>
  );
}
