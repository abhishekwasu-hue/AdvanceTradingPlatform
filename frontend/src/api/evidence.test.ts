import { describe, expect, it } from "vitest";
import { evidenceBody } from "./client";

const bars = [{ timestamp: "2026-10-09T03:45:00Z", open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 }];

describe("H-C1 a evidence body", () => {
  it("never posts candles in broker mode: the server fetches them", () => {
    const body = evidenceBody(bars, "broker:upstox", 20);
    expect(body).toEqual({ broker: "upstox", lookback_days: 20 });
    expect("candles" in body).toBe(false);
  });
  it("lets the server pick the primary broker when none is chosen", () => {
    expect(evidenceBody(bars, "broker:")).toEqual({ broker: null });
  });
  it("posts sample candles as a sample", () => {
    expect(evidenceBody(bars, "sample")).toEqual({ candles: bars, data_source: "sample" });
  });
});
