import { afterEach, expect, it, vi } from "vitest";
import { activateWarmWindow } from "@/lib/warmWindow";

afterEach(() => vi.unstubAllGlobals());

it("uses the same-origin route without backend credentials", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 503 }));
  vi.stubGlobal("fetch", fetchMock);
  await expect(activateWarmWindow()).resolves.toBeUndefined();
  expect(fetchMock).toHaveBeenCalledWith("/api/warm/activate", expect.objectContaining({ method: "POST", credentials: "omit", cache: "no-store", signal: expect.any(AbortSignal) }));
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("isolates network and timeout failures without retries", async () => {
  const fetchMock = vi.fn().mockRejectedValue(new Error("unavailable"));
  vi.stubGlobal("fetch", fetchMock);
  await expect(activateWarmWindow()).resolves.toBeUndefined();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
