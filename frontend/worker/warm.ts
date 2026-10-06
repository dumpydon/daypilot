export const WARM_KEY = "render_warm_until";
export const WARM_WINDOW_MS = 3 * 60 * 60 * 1_000;
export const KEEPALIVE_CRON = "*/10 * * * *";
export const DAILY_CRON = "30 3 * * *";

export interface WarmEnv {
  DAYPILOT_STATE?: {
    get(key: string): Promise<string | null>;
    put(key: string, value: string): Promise<void>;
  };
  RENDER_API_BASE_URL?: string;
  DAYPILOT_MAINTENANCE_SECRET?: string;
}

export interface WarmEvent {
  cron: string;
  scheduledTime: number;
}

async function readExpiry(env: WarmEnv): Promise<number> {
  if (!env.DAYPILOT_STATE) throw new Error("Warm storage unavailable");
  const expiry = Number(await env.DAYPILOT_STATE.get(WARM_KEY));
  return Number.isSafeInteger(expiry) && expiry > 0 ? expiry : 0;
}

export async function activateWarmLease(env: WarmEnv, now = Date.now()): Promise<number> {
  const existing = await readExpiry(env);
  if (existing > now) {
    console.info("warm.user_lease_reused", { warmUntil: existing });
    return existing;
  }
  const expiry = now + WARM_WINDOW_MS;
  await env.DAYPILOT_STATE!.put(WARM_KEY, String(expiry));
  console.info("warm.user_lease_created", { warmUntil: expiry });
  return expiry;
}

// Anchor to noon IST on the scheduled event's date, never invocation time + 3h.
export function dailyExpiry(scheduledTime: number): number {
  const ist = new Date(scheduledTime + 330 * 60_000);
  return Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate(), 6, 30);
}

export async function ensureDailyLease(env: WarmEnv, expiry: number): Promise<number> {
  const existing = await readExpiry(env);
  const effective = Math.max(existing, expiry);
  if (effective !== existing) await env.DAYPILOT_STATE!.put(WARM_KEY, String(effective));
  console.info("warm.daily_window_ensured", { warmUntil: effective });
  return effective;
}

function backendUrl(env: WarmEnv, path: string): URL {
  const base = new URL(env.RENDER_API_BASE_URL ?? "");
  if (base.protocol !== "https:" && base.hostname !== "localhost" && base.hostname !== "127.0.0.1") {
    throw new Error("Invalid backend origin");
  }
  return new URL(path, base.origin);
}

async function pingHealth(env: WarmEnv): Promise<void> {
  try {
    const response = await fetch(backendUrl(env, "/health"), {
      method: "GET", redirect: "manual", cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
    await response.body?.cancel();
    console.info(response.ok ? "warm.health_succeeded" : "warm.health_failed", { status: response.status });
  } catch {
    console.warn("warm.health_failed");
  }
}

async function dailyHeartbeat(env: WarmEnv): Promise<void> {
  if (!env.DAYPILOT_MAINTENANCE_SECRET) {
    console.warn("warm.database_heartbeat_unconfigured");
    return;
  }
  try {
    const url = backendUrl(env, "/internal/database-heartbeat");
    // Three bounded attempts handle an early 503 while Render cold-starts.
    // One 90-second budget applies to the entire daily operation.
    const signal = AbortSignal.timeout(90_000);
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const response = await fetch(url, {
          method: "GET", redirect: "manual", cache: "no-store", signal,
          headers: { Authorization: `Bearer ${env.DAYPILOT_MAINTENANCE_SECRET}` },
        });
        if (response.ok) {
          const result = await response.json() as { status?: string; database?: string; checked_at?: string };
          if (result.status === "ok" && result.database === "postgresql" && result.checked_at) {
            console.info("warm.database_heartbeat_succeeded", { checkedAt: result.checked_at });
            return;
          }
          console.warn("warm.database_heartbeat_invalid_response");
          return;
        }
        await response.body?.cancel();
        // Authentication/configuration failures won't improve with retries.
        if (response.status < 500 && response.status !== 429) break;
      } catch {
        if (signal.aborted) break;
      }
      if (attempt < 2) await pauseBeforeRetry(signal);
      if (signal.aborted) break;
    }
  } catch {
    // Do not log exception messages: they may contain configuration/secrets.
  }
  console.warn("warm.database_heartbeat_failed");
}

function pauseBeforeRetry(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, 10_000);
    signal.addEventListener("abort", finish, { once: true });
    if (signal.aborted) finish();
  });
}

export async function handleScheduled(event: WarmEvent, env: WarmEnv, now = Date.now()): Promise<void> {
  if (event.cron === DAILY_CRON) {
    const expiry = dailyExpiry(event.scheduledTime);
    if (now >= expiry) {
      console.info("warm.daily_window_expired");
      return;
    }
    try {
      await ensureDailyLease(env, expiry);
    } catch {
      console.warn("warm.daily_storage_failed");
    }
    // Still wake Render and query PostgreSQL if KV fails.
    await dailyHeartbeat(env);
    return;
  }
  if (event.cron !== KEEPALIVE_CRON) return;
  // The separate daily trigger owns the 03:30 request, avoiding duplicate pings.
  const scheduled = new Date(event.scheduledTime);
  if (scheduled.getUTCHours() === 3 && scheduled.getUTCMinutes() === 30) return;
  try {
    if (await readExpiry(env) <= now) {
      console.info("warm.keepalive_skipped");
      return;
    }
    await pingHealth(env);
  } catch {
    console.warn("warm.keepalive_storage_failed");
  }
}

export async function warmActivation(request: Request, env: WarmEnv): Promise<Response> {
  const headers = { "Cache-Control": "no-store" };
  if (request.method !== "POST") return new Response("Method not allowed", {
    status: 405, headers: { ...headers, Allow: "POST" },
  });
  const origin = request.headers.get("Origin");
  if ((origin && origin !== new URL(request.url).origin) || request.headers.get("Sec-Fetch-Site") === "cross-site") {
    return new Response("Origin not allowed", { status: 403, headers });
  }
  try {
    return Response.json({ warmUntil: await activateWarmLease(env) }, { headers });
  } catch {
    console.warn("warm.activation_unavailable");
    return Response.json({ available: false }, { status: 503, headers });
  }
}
