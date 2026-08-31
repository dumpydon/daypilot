import type { SVGProps } from "react";

import { DAYPILOT_MARK_COLOR, DAYPILOT_MARK_PATHS, DAYPILOT_MARK_VIEWBOX } from "./daypilotMarkGeometry";
import styles from "./daypilot-logo.module.css";

interface DayPilotLogoProps extends Omit<SVGProps<SVGSVGElement>, "viewBox"> {
  active?: boolean;
  monochrome?: boolean;
  size?: number;
}

/** Approved D/P monogram, shared with the app icon through canonical geometry. */
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
      <g className={styles.mark} fill={monochrome ? "currentColor" : DAYPILOT_MARK_COLOR}>
        <path d={DAYPILOT_MARK_PATHS.shell} />
        <path d={DAYPILOT_MARK_PATHS.stem} />
      </g>
    </svg>
  );
}
