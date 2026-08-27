import type { SVGProps } from "react";

import { DAYPILOT_MARK_PATHS, DAYPILOT_MARK_VIEWBOX } from "./daypilotMarkGeometry";
import styles from "./daypilot-logo.module.css";

interface DayPilotLogoProps extends Omit<SVGProps<SVGSVGElement>, "viewBox"> {
  active?: boolean;
  monochrome?: boolean;
  size?: number;
}

/** Canonical DayPilot mark: two routed trajectories crossing a controlled decision aperture. */
export function DayPilotLogo({ active = false, monochrome = false, size = 34, className, ...props }: DayPilotLogoProps) {
  return (
    <svg
      {...props}
      className={`${styles.logo} ${active ? styles.logoActive : ""} ${monochrome ? styles.logoMonochrome : ""} ${className ?? ""}`}
      width={size}
      height={size}
      viewBox={DAYPILOT_MARK_VIEWBOX}
      role="img"
      aria-label={props["aria-label"] ?? "DayPilot logo"}
      focusable="false"
    >
      <path className={`${styles.route} ${styles.routeUpper}`} d={DAYPILOT_MARK_PATHS.upperRoute} fillRule="evenodd" />
      <path className={`${styles.route} ${styles.routeLower}`} d={DAYPILOT_MARK_PATHS.lowerRoute} />
      <path className={styles.executionBody} d={DAYPILOT_MARK_PATHS.executionBody} />
      <path className={styles.decision} d={DAYPILOT_MARK_PATHS.decision} />
    </svg>
  );
}
