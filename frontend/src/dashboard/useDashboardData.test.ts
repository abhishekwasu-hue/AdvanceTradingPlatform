import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import hookSource from "./useDashboardData.ts?raw";
import { REFRESH_MS, dataOf, startPolling, stripDelay } from "./useDashboardData";
import type { MarketStrip } from "./types";

/** UI2-2: the dashboard's reads - refresh timing, keeping the last good data, skipping a hidden tab, read-only. */

const flush = async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); };

describe("UI2-2 polling", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it("reads now, then again after the delay the last answer names; stop ends it", async () => {
    let n = 0;
    const seen: number[] = [];
    const stop = startPolling({ load: async () => ++n, delay: (d) => (d === null ? 1000 : d * 1000), onData: (d) => seen.push(d), onError: () => undefined });
    await flush();
    expect(seen).toEqual([1]);
    await vi.advanceTimersByTimeAsync(999);
    expect(seen).toEqual([1]);
    await vi.advanceTimersByTimeAsync(1);
    expect(seen).toEqual([1, 2]);
    await vi.advanceTimersByTimeAsync(2000);
    expect(seen).toEqual([1, 2, 3]);
    stop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(seen).toEqual([1, 2, 3]);
  });

  it("reports a failure and keeps going; a hidden tab skips its turn without a read", async () => {
    let calls = 0;
    let hidden = false;
    const errors: number[] = [];
    startPolling({ load: async () => { calls += 1; if (calls === 1) throw new Error("x"); return calls; }, delay: () => REFRESH_MS,
      onData: () => undefined, onError: () => errors.push(calls), hidden: () => hidden });
    await flush();
    expect(errors).toEqual([1]);
    hidden = true;
    await vi.advanceTimersByTimeAsync(REFRESH_MS * 3);
    expect(calls).toBe(1);
    hidden = false;
    await vi.advanceTimersByTimeAsync(REFRESH_MS);
    expect(calls).toBe(2);
  });
});

describe("UI2-2 strip timing and data", () => {
  it("follows the server's interval: 15 s while open, 5 min closed, bounded", () => {
    const strip = (s: number) => ({ refresh_seconds: s }) as MarketStrip;
    expect(stripDelay(strip(15))).toBe(15_000);
    expect(stripDelay(strip(300))).toBe(300_000);
    expect(stripDelay(strip(1))).toBe(10_000);
    expect(stripDelay(strip(99_999))).toBe(600_000);
    expect(stripDelay(null)).toBe(15_000);
    expect(dataOf({ status: "ok", data: 3 })).toBe(3);
    expect(dataOf({ status: "error" })).toBeNull();
  });

  it("calls GET /market/strip and only reads (no write, no order call)", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ indices: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await api.marketStrip();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit | undefined];
    expect(url).toMatch(/\/market\/strip$/);
    expect((init?.method ?? "GET").toUpperCase()).toBe("GET");
    vi.unstubAllGlobals();
    const calls = [...hookSource.matchAll(/api\.(\w+)/g)].map((m) => m[1]);
    expect(new Set(calls)).toEqual(new Set(["marketStrip", "oiBanners", "portfolioExposure", "listPositions", "aiActions", "listDeployments",
      "paperRisk", "listNotifications", "systemStatus", "getAnalyticsSummary", "listAlertChannels"]));
  });
});
