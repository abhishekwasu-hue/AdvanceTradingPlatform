import { describe, expect, it } from "vitest";
import { POLL_MS, WORKER_HINT_AFTER_MS, isActive, metricLine, prob, progressFraction, shouldPoll, workerLooksIdle } from "./research";

const at = "2026-03-10T06:00:00Z";
const study = (status: "queued" | "running" | "done" | "failed" | "interrupted", tried = 0, max = 6) =>
  ({ status, created_at: at, progress: { drafts_tried: tried, max_drafts: max } });

describe("research studies panel logic", () => {
  it("polls only while a study is queued or running", () => {
    expect(isActive(study("queued")) && isActive(study("running"))).toBe(true);
    expect(["done", "failed", "interrupted"].map((s) => isActive(study(s as "done")))).toEqual([false, false, false]);
    expect(shouldPoll([study("done"), study("running")])).toBe(true);
    expect(shouldPoll([study("done"), study("failed"), study("interrupted")])).toBe(false);
    expect(shouldPoll([])).toBe(false);
    expect(POLL_MS).toBeGreaterThanOrEqual(5_000);                               // no hammering of the API
  });

  it("hints that the worker is not running only for a study queued too long", () => {
    const t0 = Date.parse(at);
    expect(workerLooksIdle(study("queued"), t0 + WORKER_HINT_AFTER_MS - 1)).toBe(false);
    expect(workerLooksIdle(study("queued"), t0 + WORKER_HINT_AFTER_MS + 1)).toBe(true);
    expect(workerLooksIdle(study("running"), t0 + 10 * WORKER_HINT_AFTER_MS)).toBe(false);
    expect(workerLooksIdle({ status: "queued", created_at: null }, t0 + 10 * WORKER_HINT_AFTER_MS)).toBe(false);
  });

  it("progress: drafts tried over the cap while active, full once finished", () => {
    expect(progressFraction(study("running", 3, 6))).toBe(0.5);
    expect(progressFraction(study("queued", 0, 6))).toBe(0);
    expect(progressFraction(study("running", 9, 6))).toBe(1);
    expect(progressFraction(study("failed", 1, 6))).toBe(1);
    expect(progressFraction(study("running", 0, 0))).toBe(0);
  });

  it("numbers are shown as they are, never invented", () => {
    expect(prob(0.4321)).toBe("0.43");
    expect(prob(null)).toBe("n/a");
    expect(prob(undefined)).toBe("n/a");
    expect(prob(Number.NaN)).toBe("n/a");
    expect(metricLine({ trades: 12, win_rate: 41.666, profit_factor: null, max_drawdown: 1520.5, net_pnl: -300, extra: 9 }))
      .toBe("trades 12 · win % 41.67 · max DD 1520.50 · net -300");
    expect(metricLine({})).toBe("");
  });
});
