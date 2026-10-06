export async function activateWarmWindow(): Promise<void> {
  try {
    // Same-origin Worker route; never delay or retry normal application bootstrap.
    await fetch("/api/warm/activate", {
      method: "POST", credentials: "omit", cache: "no-store",
      signal: AbortSignal.timeout(3_000),
    });
  } catch {
    // Warm storage is optional; existing backend startup/readiness handles wake-up.
  }
}
