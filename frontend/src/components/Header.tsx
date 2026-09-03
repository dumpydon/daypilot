import { Cable, Menu } from "lucide-react";

import type { MCPServer } from "@/lib/types";

import { DayPilotLogo } from "./DayPilotLogo";
import styles from "./workspace.module.css";

interface HeaderProps {
  servers: MCPServer[];
  reasoningMode: string;
  onMenu: () => void;
  active?: boolean;
  readinessState?: "starting" | "ready" | "degraded";
  publicDemoMode?: boolean;
  adminAuthenticated?: boolean;
}

export function Header({
  servers,
  reasoningMode,
  onMenu,
  active = false,
  readinessState = "ready",
  publicDemoMode = false,
  adminAuthenticated = false,
}: HeaderProps) {
  const connected = servers.filter((server) => server.connected).length;
  const realConnected = servers.filter(
    (server) => server.connected
      && server.provider
      && server.provider !== "DayPilot demo"
      && server.provider_state === "connected",
  ).length;
  const hasConfiguredProvider = servers.some(
    (server) => server.provider && server.provider !== "DayPilot demo",
  );
  const openAIReady = reasoningMode === "openai";
  const waking = readinessState === "starting";
  const publicVisitor = publicDemoMode && !adminAuthenticated;
  return (
    <header className={styles.header}>
      <button className={styles.mobileMenu} onClick={onMenu} aria-label="Open navigation">
        <Menu size={17} />
      </button>
      {/* Home and run detail share `/`; document navigation clears only the local view,
          including in-flight view updates, while persisted runs continue on the server. */}
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
      <a className={styles.brand} href="/" aria-label="DayPilot home">
        <DayPilotLogo className={styles.mark} active={active} aria-label="DayPilot logo" />
        <div>
          <strong>DayPilot</strong>
          <span>MCP-powered personal operations</span>
        </div>
      </a>
      <div className={styles.runtime}>
        <span className={styles.demoBadge}>
          {waking
            ? "Waking DayPilot…"
            : publicDemoMode && !adminAuthenticated
              ? "Public demo"
              : !hasConfiguredProvider
                ? "Demo workspace"
                : realConnected > 1
                  ? "Connected workspace"
                  : `${realConnected}/5 connected`}
        </span>
        <span className={styles.serverHealth} aria-label={waking ? "MCP servers connecting" : publicVisitor ? "Public demo services ready" : `${connected}/${servers.length} MCP servers`}>
          <Cable size={13} />
          <i className={waking ? styles.warnDot : publicVisitor && readinessState === "ready" ? styles.okDot : connected === servers.length ? styles.okDot : styles.warnDot} />
          {waking ? "Connecting services…" : publicVisitor ? readinessState === "ready" ? "Public demo · Web ready" : "Public demo · Limited services" : <><strong>{connected}/{servers.length}</strong> MCP servers</>}
        </span>
        <span className={styles.runtimeStack} role="group" aria-label="Runtime status">
          <span>
            <i
              className={openAIReady ? styles.okDot : styles.mutedDot}
              data-runtime-state={openAIReady ? "ready" : "unavailable"}
              data-testid="openai-runtime-indicator"
            />
            {openAIReady ? "OpenAI runtime" : "OpenAI unavailable"}
          </span>
          <span><i className={styles.okDot} />Local runtime</span>
        </span>
      </div>
    </header>
  );
}
