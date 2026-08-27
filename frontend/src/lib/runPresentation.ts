import type { RunRecord } from "./types";

const LEADING_FILLER = /^(?:please|can you|could you|would you|i want you to|help me)\s+/i;
const FALLBACK_FILLER = new Set([
  "a",
  "an",
  "and",
  "for",
  "from",
  "find",
  "get",
  "my",
  "read",
  "show",
  "tell",
  "the",
  "to",
  "with",
]);

/** Stable, local-only presentation title; it never triggers a model call. */
export function runDisplayTitle(run: Pick<RunRecord, "user_request">): string {
  return titleForRequest(run.user_request);
}

export function titleForRequest(request: string): string {
  const normalized = request.trim().replace(/\s+/g, " ");
  if (!normalized) return "Workspace run";

  const isX = /(?:\bx\b|twitter|tweet|post|social)/i.test(normalized);
  const isLaunch = /\blaunch\b/i.test(normalized);
  const isResearch = /(?:research|recent|what people|what users|search|saying|tell me)/i.test(normalized);
  const isDraft = /\bdraft\b/i.test(normalized);
  if (isX && isLaunch && isDraft) return "X launch draft";
  if (isX && isLaunch && isResearch) return "MCP launch research";
  if (isX && /\bpublish\b/i.test(normalized)) return "X project post";
  if (isX && isDraft) return "X post draft";
  if (isX && isResearch) return "X post research";

  if (/\bresume\b/i.test(normalized)) return "Resume lookup";
  if (/\binterview\b/i.test(normalized) && /(?:prepare|prep|preparation|ready)/i.test(normalized)) {
    return "Interview preparation";
  }
  if (/\binterview\b/i.test(normalized)) return "Interview lookup";

  if (/(?:conflict|overlap|clash)/i.test(normalized) && /(?:calendar|event|schedule)/i.test(normalized)) {
    return "Calendar conflict check";
  }
  if (/(?:calendar|event|schedule|scheduling|free slot|focus block|availability)/i.test(normalized)) {
    const requestedTitle = normalized.match(/\b(?:called|named)\s+["“']([^"”']+)["”']/i)?.[1];
    return requestedTitle ? `${compactWords(requestedTitle, 3)} scheduling` : "Calendar scheduling";
  }

  if (/(?:checklist|task|to-do)/i.test(normalized)) {
    return /(?:create|make|build)/i.test(normalized) ? "Checklist creation" : "Task review";
  }
  if (/\bproject\b/i.test(normalized) && /(?:about|overview|details)/i.test(normalized)) {
    return "Project overview";
  }
  if (/(?:file|document|notes|brief|workspace)/i.test(normalized)) return "Workspace document lookup";
  if (/(?:mail|email|conversation|thread)/i.test(normalized)) return "Mail lookup";

  const fallback = normalized
    .replace(LEADING_FILLER, "")
    .split(/[.!?]/, 1)[0]
    .split(" ")
    .filter((word) => !FALLBACK_FILLER.has(word.toLowerCase()))
    .slice(0, 4)
    .join(" ");
  return fallback ? titleCase(fallback) : "Workspace run";
}

function compactWords(value: string, maxWords: number): string {
  return titleCase(value.trim().split(/\s+/).slice(0, maxWords).join(" "));
}

function titleCase(value: string): string {
  return value
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
    .replace(/\bAi\b/g, "AI")
    .replace(/\bMcp\b/g, "MCP");
}
