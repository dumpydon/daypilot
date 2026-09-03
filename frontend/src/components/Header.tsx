import { Cable, Menu } from "lucide-react";
import Link from "next/link";

import type { MCPServer } from "@/lib/types";
import { endTiming, startTiming } from "@/lib/timing";

import { DayPilotLogo } from "./DayPilotLogo";
import styles from "./workspace.module.css";

interface HeaderProps {
  servers: MCPServer[];
  reasoningMode: string;
  onMenu: () => void;
  onHome?: () => void;
  active?: boolean;
  readinessState?: "starting" | "ready" | "degraded";
  publicDemoMode?: boolean;
  adminAuthenticated?: boolean;
}

export function Header({
  servers,
  reasoningMode,
  onMenu,
  onHome,
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
      <Link
        className={styles.brand}
        href="/"
        aria-label="DayPilot home"
        onClick={(event) => {
          // Home and run detail share `/`. Keep the warm workspace mounted and
          // clear only the local view when the link is clicked in place.
          if (
            onHome
            && typeof window !== "undefined"
            && window.location.pathname === "/"
            && !event.metaKey
            && !event.ctrlKey
            && !event.shiftKey
            && !event.altKey
          ) {
            event.preventDefault();
            const navigationTiming = startTiming("home-navigation");
            onHome();
            endTiming("home-navigation", navigationTiming);
          }
        }}
      >
        <DayPilotLogo className={styles.mark} active={active} aria-label="DayPilot logo" />
        <div>
          <strong>DayPilot</strong>
          <span>MCP-powered personal operations</span>
        </div>
      </Link>
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
