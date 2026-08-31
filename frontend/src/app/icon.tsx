import { ImageResponse } from "next/og";

import { DAYPILOT_MARK_COLOR, DAYPILOT_MARK_PATHS, DAYPILOT_MARK_VIEWBOX } from "@/components/daypilotMarkGeometry";

export const size = { width: 64, height: 64 };
export const contentType = "image/png";

export default function Icon() {
  return new ImageResponse(
    <div
      style={{
        alignItems: "center",
        background: "#111214",
        borderRadius: 15,
        display: "flex",
        height: "100%",
        justifyContent: "center",
        width: "100%",
      }}
    >
      <svg width="50" height="50" viewBox={DAYPILOT_MARK_VIEWBOX}>
        <path fill={DAYPILOT_MARK_COLOR} d={DAYPILOT_MARK_PATHS.shell} />
        <path fill={DAYPILOT_MARK_COLOR} d={DAYPILOT_MARK_PATHS.stem} />
      </svg>
    </div>,
    size,
  );
}
