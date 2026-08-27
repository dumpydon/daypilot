import { ImageResponse } from "next/og";

import { DAYPILOT_MARK_PATHS, DAYPILOT_MARK_VIEWBOX } from "@/components/daypilotMarkGeometry";

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
        <path fill="#6ea0ff" fillRule="evenodd" d={DAYPILOT_MARK_PATHS.upperRoute} />
        <path fill="#4d84ee" d={DAYPILOT_MARK_PATHS.lowerRoute} />
        <path fill="#3f7bf2" d={DAYPILOT_MARK_PATHS.executionBody} />
        <path fill="#4d84ee" d={DAYPILOT_MARK_PATHS.lowerRoute} />
        <path fill="#3f7bf2" d={DAYPILOT_MARK_PATHS.executionBody} />
        <path fill="#4d84ee" d={DAYPILOT_MARK_PATHS.lowerRoute} />
        <path fill="#3f7bf2" d={DAYPILOT_MARK_PATHS.executionBody} />
        <path fill="#f1f4fb" d={DAYPILOT_MARK_PATHS.decision} />
      </svg>
    </div>,
    size,
  );
}
