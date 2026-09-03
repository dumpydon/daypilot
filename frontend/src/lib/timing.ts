/** Secret-free browser performance marks for navigation and run lifecycle QA. */
let sequence = 0;

export function markTiming(name: string): void {
  if (typeof performance === "undefined" || typeof performance.mark !== "function") return;
  performance.mark(`daypilot:${name}`);
}

export function startTiming(name: string): string {
  const mark = `${name}:start:${sequence += 1}`;
  markTiming(mark);
  return mark;
}

export function endTiming(name: string, startMark: string): void {
  const endMark = `${name}:end:${sequence += 1}`;
  markTiming(endMark);
  measureTiming(name, startMark, endMark);
}

export function measureTiming(name: string, start: string, end: string): void {
  if (typeof performance === "undefined" || typeof performance.measure !== "function") return;
  try {
    performance.measure(`daypilot:${name}`, `daypilot:${start}`, `daypilot:${end}`);
  } catch {
    // Marks may be cleared by the browser between lifecycle callbacks.
  }
}
