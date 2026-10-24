/** C1: an empty broker answer is an empty state with its reason - never a silent `replace([])`. */
import { describe, expect, it } from "vitest";
import type { CandlesResponse } from "../types";
import { CandleFeed, emptyCandlesText, type FeedEvents } from "./useChartCandles";

const SYMBOL = "NIFTY2610325000CE";
const REASON = "No candles yet for this contract. It may be a new contract, or your broker sent no data for this period up to the last session close (23 Oct, 15:30 IST).";

function answer(bars: number, emptyReason?: string): CandlesResponse {
  return {
    source: { broker: "upstox", account_label: "primary" }, exchange: "NFO", timeframe: "30min", base_interval: "1min", lookback_days: 10,
    fetched_at: "", warnings: [], note: "",
    symbols: { [SYMBOL]: { bars: Array.from({ length: bars }, (_, i) => ({ timestamp: `2026-10-23T0${4 + i}:00:00Z`, open: 1, high: 1, low: 1, close: 1, volume: 0 })),
                           count: bars, first: null, last: null, error: null, ...(emptyReason ? { empty_reason: emptyReason } : {}) } },
  };
}

function events() {
  const log: string[] = [];
  const on: FeedEvents = {
    loading: (v) => log.push(`loading:${v}`), replace: (b) => log.push(`replace:${b.length}`), merge: (b) => log.push(`merge:${b.length}`),
    error: (t) => log.push(t ? "error" : "ok"), empty: (t) => log.push(t ? `empty:${t}` : "filled"),
  };
  return { log, on };
}

describe("C1: the chart's empty state", () => {
  it("shows the server's reason and never replaces the chart with nothing; the next tick reads the whole history", async () => {
    const asked: number[] = [];
    const replies = [answer(0, REASON), answer(3)];
    const { log, on } = events();
    const feed = new CandleFeed(async (days) => { asked.push(days); return replies.shift()!; }, SYMBOL, "30min", on);
    await feed.tick();
    expect(log).toEqual(["loading:true", `empty:${REASON}`, "ok", "loading:false"]);
    expect(log.some((l) => l.startsWith("replace"))).toBe(false);
    await feed.tick();
    expect(asked[0]).toBe(asked[1]);                         // the first read again, not a short refresh
    expect(log.slice(4)).toEqual(["loading:true", "replace:3", "filled", "ok", "loading:false"]);
  });

  it("falls back to plain words when the server sent no reason", () => {
    expect(emptyCandlesText(undefined, SYMBOL)).toBe(`No candles yet for ${SYMBOL}. Your broker sent no data for this period.`);
    expect(emptyCandlesText("  ", "TCS")).toContain("No candles yet for TCS");
    expect(emptyCandlesText(REASON, SYMBOL)).toBe(REASON);
  });
});
