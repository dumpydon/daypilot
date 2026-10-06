// @vitest-environment node
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import {
  activateWarmLease, dailyExpiry, ensureDailyLease, handleScheduled, warmActivation,
  DAILY_CRON, KEEPALIVE_CRON, WARM_KEY, WARM_WINDOW_MS, type WarmEnv,
} from "../../worker/warm";

const NOW = Date.parse("2026-10-06T04:30:00Z");
const MORNING = Date.parse("2026-10-06T03:30:00Z");
const NOON = Date.parse("2026-10-06T06:30:00Z");
const vinextFetch = vi.hoisted(() => vi.fn());
vi.mock("vinext/server/fetch-handler", () => ({ default: { fetch: vinextFetch } }));
import worker from "../../worker/index";

describe("fixed warm windows", () => {
  let value: string | null;
  let env: WarmEnv;
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    value = null;
    env = {
      DAYPILOT_STATE: {
        get: vi.fn(async () => value),
        put: vi.fn(async (_key, next) => { value = next; }),
      },
      RENDER_API_BASE_URL: "https://render.example",
      DAYPILOT_MAINTENANCE_SECRET: "test-heartbeat-secret",
    };
    fetchMock = vi.fn(async () => Response.json({ status: "ok", database: "postgresql", checked_at: "2026-10-06 03:30:05+00" }));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(console, "info").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

  it.each([null, "0", "garbage", String(NOW - 1), String(NOW)])("creates a fixed lease for missing/expired/invalid state %s", async (stored) => {
    value = stored;
    expect(await activateWarmLease(env, NOW)).toBe(NOW + WARM_WINDOW_MS);
    expect(env.DAYPILOT_STATE!.put).toHaveBeenCalledWith(WARM_KEY, String(NOW + WARM_WINDOW_MS));
  });
  it("reuses an active expiry without a KV write", async () => {
    value = String(NOW + WARM_WINDOW_MS);
    expect(await activateWarmLease(env, NOW + 60_000)).toBe(NOW + WARM_WINDOW_MS);
    expect(env.DAYPILOT_STATE!.put).not.toHaveBeenCalled();
  });
  it("only pings health within an active window and never renews it", async () => {
    value = String(NOW + WARM_WINDOW_MS);
    await handleScheduled({ cron: KEEPALIVE_CRON, scheduledTime: NOW }, env, NOW);
    expect(String(fetchMock.mock.calls[0][0])).toBe("https://render.example/health");
    expect(fetchMock.mock.calls[0][1]).not.toHaveProperty("headers");
    expect(env.DAYPILOT_STATE!.put).not.toHaveBeenCalled();
    fetchMock.mockClear();
    await handleScheduled({ cron: KEEPALIVE_CRON, scheduledTime: NOW }, env, NOW + WARM_WINDOW_MS);
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("handles missing KV and failed health without throwing", async () => {
    await handleScheduled({ cron: KEEPALIVE_CRON, scheduledTime: NOW }, {}, NOW);
    expect(fetchMock).not.toHaveBeenCalled();
    value = String(NOW + WARM_WINDOW_MS);
    fetchMock.mockRejectedValueOnce(new Error("sensitive connection details"));
    await expect(handleScheduled({ cron: KEEPALIVE_CRON, scheduledTime: NOW }, env, NOW)).resolves.toBeUndefined();
    expect(console.warn).not.toHaveBeenCalledWith(expect.stringContaining("sensitive"));
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 }));
    await expect(handleScheduled({ cron: KEEPALIVE_CRON, scheduledTime: NOW }, env, NOW)).resolves.toBeUndefined();
  });
  it("anchors a late daily event to noon IST, performs the DB heartbeat, and revisits do not extend it", async () => {
    expect(dailyExpiry(MORNING)).toBe(NOON);
    await handleScheduled({ cron: DAILY_CRON, scheduledTime: MORNING }, env, MORNING + 5 * 60_000);
    expect(value).toBe(String(NOON));
    expect(String(fetchMock.mock.calls[0][0])).toBe("https://render.example/internal/database-heartbeat");
    expect(fetchMock.mock.calls[0][1].headers).toEqual({ Authorization: "Bearer test-heartbeat-secret" });
    expect(await activateWarmLease(env, NOW)).toBe(NOON);
  });
  it("preserves a longer user lease during the daily activation", async () => {
    value = String(NOON + 60_000);
    expect(await ensureDailyLease(env, NOON)).toBe(NOON + 60_000);
    expect(env.DAYPILOT_STATE!.put).not.toHaveBeenCalled();
  });
  it("repeated daily activation never slides the window", async () => {
    await handleScheduled({ cron: DAILY_CRON, scheduledTime: MORNING }, env, MORNING);
    await handleScheduled({ cron: DAILY_CRON, scheduledTime: MORNING }, env, NOW);
    expect(value).toBe(String(NOON));
    expect(env.DAYPILOT_STATE!.put).toHaveBeenCalledTimes(1);
  });
  it("still performs the daily DB heartbeat when KV fails", async () => {
    vi.mocked(env.DAYPILOT_STATE!.get).mockRejectedValueOnce(new Error("KV unavailable"));
    await handleScheduled({ cron: DAILY_CRON, scheduledTime: MORNING }, env, MORNING);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("skips the overlapping ten-minute trigger, stale daily events, and unknown cron events", async () => {
    for (const event of [
      { cron: KEEPALIVE_CRON, scheduledTime: MORNING },
      { cron: DAILY_CRON, scheduledTime: MORNING },
      { cron: "unknown", scheduledTime: NOW },
    ]) await handleScheduled(event, env, NOON);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(env.DAYPILOT_STATE!.put).not.toHaveBeenCalled();
  });
  it("retries an early cold-start 503 once before accepting a real heartbeat", async () => {
    vi.useFakeTimers();
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 }));
    const scheduled = handleScheduled({ cron: DAILY_CRON, scheduledTime: MORNING }, env, MORNING);
    await vi.advanceTimersByTimeAsync(10_000);
    await scheduled;
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it("caps daily retries and leaves the lease intact on database failure", async () => {
    vi.useFakeTimers();
    fetchMock.mockResolvedValue(new Response(null, { status: 503 }));
    const scheduled = handleScheduled({ cron: DAILY_CRON, scheduledTime: MORNING }, env, MORNING);
    await vi.advanceTimersByTimeAsync(30_000);
    await expect(scheduled).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(value).toBe(String(NOON));
  });
  it("does not retry authentication failures or mistake cached health for DB success", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }));
    await handleScheduled({ cron: DAILY_CRON, scheduledTime: MORNING }, env, MORNING);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fetchMock.mockResolvedValueOnce(Response.json({ status: "ok", database: "connected" }));
    await handleScheduled({ cron: DAILY_CRON, scheduledTime: MORNING }, env, MORNING);
    expect(console.info).not.toHaveBeenCalledWith("warm.database_heartbeat_succeeded", expect.anything());
  });
  it("fails closed when maintenance secret is missing", async () => {
    delete env.DAYPILOT_MAINTENANCE_SECRET;
    await handleScheduled({ cron: DAILY_CRON, scheduledTime: MORNING }, env, MORNING);
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("rejects wrong methods and cross-origin activation; returns safe KV errors", async () => {
    expect((await warmActivation(new Request("https://site.example/api/warm/activate"), env)).status).toBe(405);
    expect((await warmActivation(new Request("https://site.example/api/warm/activate", { method: "POST", headers: { Origin: "https://other.example" } }), env)).status).toBe(403);
    const unavailable = await warmActivation(new Request("https://site.example/api/warm/activate", { method: "POST" }), {});
    expect(unavailable.status).toBe(503);
    expect(unavailable.headers.get("cache-control")).toBe("no-store");
    expect(await unavailable.json()).toEqual({ available: false });
  });
  it("delegates application/assets to vinext and intercepts only the warm route", async () => {
    vinextFetch.mockResolvedValue(new Response("app"));
    const request = new Request("https://site.example/");
    expect(await (await worker.fetch(request, env, undefined)).text()).toBe("app");
    expect(vinextFetch).toHaveBeenCalledWith(request, env, undefined);
    const response = await worker.fetch(new Request("https://site.example/api/warm/activate", { method: "POST" }), env, undefined);
    expect(response.status).toBe(200);
    expect(vinextFetch).toHaveBeenCalledTimes(1);
  });
});
