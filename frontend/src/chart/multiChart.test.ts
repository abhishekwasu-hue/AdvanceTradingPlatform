import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import {
  GRIDS, LayoutConflict, cell, defaultLayout, layoutProblem, setGrid, setGroup, setGroupSync, setSymbol, setTimeframe, type LayoutV1,
} from "../charting/workspace";
import type { CandlesResponse } from "../types";
import { offeredTimeframes } from "./chartsPage";
import {
  CandlePool, LAST_LAYOUT_KEY, MULTI_DRAFT_KEY, cellChartType, cellTimeframe, layoutErrorText, readMultiDraft, saveMultiDraft, shownCount,
  startChartFrom, startLayoutV2, workspaceRedirect,
} from "./multiChart";
import { FeedGate, type FetchCandles } from "./useChartCandles";

const answer = (symbol: string): CandlesResponse => ({ symbols: { [symbol]: { bars: [], count: 0, first: null, last: null, error: null } } } as unknown as CandlesResponse);

/** A fake candle read: each call waits until `settle` answers it. */
function wire() {
  const calls: Parameters<FetchCandles>[] = [];
  const pending: { resolve: (v: CandlesResponse) => void; reject: (e: unknown) => void }[] = [];
  const fetch = vi.fn((...args: Parameters<FetchCandles>) => {
    calls.push(args);
    return new Promise<CandlesResponse>((resolve, reject) => pending.push({ resolve, reject }));
  }) as unknown as FetchCandles;
  const settle = async (i: number, fail = false) => {
    if (fail) pending[i].reject(new Error("down")); else pending[i].resolve(answer(calls[i][0][0]));
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  };
  return { calls, fetch, settle };
}

beforeEach(() => {
  const m = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); }, removeItem: (k: string) => { m.delete(k); } });
});
afterEach(() => { vi.unstubAllGlobals(); });

describe("UI2-4d Multi-chart: where a layout starts", () => {
  it("a new trader gets two charts of the page's symbol; a draft wins; an address symbol wins over both", () => {
    const fresh = startLayoutV2(null, null, "RELIANCE", "NSE");
    expect(fresh.grid).toBe(2);
    expect(fresh.cells.map((c) => c.symbol)).toEqual(["RELIANCE", "RELIANCE"]);
    const draft = setGrid(defaultLayout("TCS"), 6);
    expect(startLayoutV2(null, draft, "RELIANCE", "NSE")).toBe(draft);
    const start = startChartFrom(new URLSearchParams("tab=multi&symbol=infy&tf=week&exchange=bse"));
    expect(start).toEqual({ symbol: "infy", exchange: "BSE", timeframe: "week" });
    const l = startLayoutV2(start, draft, "RELIANCE", "NSE");
    expect(l.grid).toBe(2);
    expect(l.cells[0]).toEqual(cell("INFY", "BSE", "week"));
    expect(startChartFrom(new URLSearchParams("symbol=X&tf=2h"))?.timeframe).toBe("5min");
    expect(startChartFrom(new URLSearchParams("tab=multi"))).toBeNull();
  });

  it("the draft is kept per user and a broken one is ignored", () => {
    const l = setGrid(defaultLayout("SBIN"), 4);
    saveMultiDraft(7, l);
    expect(readMultiDraft(7)).toEqual(l);
    expect(readMultiDraft(8)).toBeNull();
    saveMultiDraft(null, l);
    expect(readMultiDraft(null)).toBeNull();
    localStorage.setItem(`${MULTI_DRAFT_KEY}:9`, JSON.stringify({ ...l, grid: 3 }));
    expect(readMultiDraft(9)).toBeNull();
    expect(LAST_LAYOUT_KEY).toBe("atp_workspace_last");              // the saved layout used last: shared with the CH5 page
  });

  it("an old workspace link opens the Multi-chart tab with its symbol, timeframe and exchange", () => {
    expect(workspaceRedirect(new URLSearchParams("workspace=1"))).toBe("/charts?tab=multi");
    expect(workspaceRedirect(new URLSearchParams("workspace=1&chart=NIFTY%2050&tf=15min&exchange=NSE")))
      .toBe("/charts?tab=multi&symbol=NIFTY+50&tf=15min&exchange=NSE");
  });

  it("grid sizes are CH5's 1, 2, 4, 6 and 8; a phone shows one or two of them", () => {
    expect([...GRIDS]).toEqual([1, 2, 4, 6, 8]);
    const eight = setGrid(defaultLayout(), 8);
    expect(shownCount(eight, false)).toBe(8);
    expect(shownCount(eight, true)).toBe(2);
    expect(shownCount(defaultLayout(), true)).toBe(1);
  });
});

describe("UI2-4d Multi-chart: cells on the v2 chart", () => {
  it("week and month only while the server lists them; a stored week waits for the list; otherwise 5m", () => {
    const all = offeredTimeframes(["1min", "3min", "5min", "15min", "30min", "60min", "day", "week", "month"]);
    const old = offeredTimeframes(["1min", "5min", "day"]);
    expect(cellTimeframe("week", all, true)).toBe("week");
    expect(cellTimeframe("month", old, true)).toBe("5min");
    expect(cellTimeframe("week", offeredTimeframes(null), false)).toBeNull();
    expect(cellTimeframe("day", offeredTimeframes(null), false)).toBe("day");
  });

  it("chart types: candles, bars, Heikin Ashi and line as they are; the CH5-only ones as the nearest v2 type", () => {
    expect(["candles", "bars", "heikin_ashi", "line", "hollow", "renko", "range", "area"].map((t) => cellChartType(t as never)))
      .toEqual(["candles", "bars", "heikin_ashi", "line", "candles", "candles", "candles", "line"]);
  });

  it("link groups: a symbol change reaches its own group only; a linked timeframe (week included) too", () => {
    let l: LayoutV1 = setGrid(defaultLayout("NIFTY 50"), 4);
    l = setGroup(l, 0, "A");
    l = setGroup(l, 1, "A");
    l = setGroup(l, 2, "B");
    l = setSymbol(l, 0, "NIFTY BANK");
    expect(l.cells.map((c) => c.symbol)).toEqual(["NIFTY BANK", "NIFTY BANK", "NIFTY 50", "NIFTY 50"]);
    l = setTimeframe(l, 0, "week");                                       // A links the symbol, not the timeframe
    expect(l.cells.map((c) => c.timeframe)).toEqual(["week", "5min", "5min", "5min"]);
    l = setGroupSync(l, "A", { interval: true });                          // turned on: aligned to A's first chart
    expect(l.cells.map((c) => c.timeframe)).toEqual(["week", "week", "5min", "5min"]);
    l = setTimeframe(l, 1, "month");
    expect(l.cells.map((c) => c.timeframe)).toEqual(["month", "month", "5min", "5min"]);
    expect(layoutProblem(l)).toBeNull();
  });

  it("save and delete errors are plain words with a next step", () => {
    expect(layoutErrorText(new LayoutConflict(null), "saved")).toMatch(/changed in another window/);
    expect(layoutErrorText(new ApiError(409, "x", "you already have a layout named 'A'"), "saved")).toBe("You already have a layout with that name. Pick another name.");
    expect(layoutErrorText(new ApiError(409, "x", "at most 50 saved layouts per user - delete one first"), "saved")).toMatch(/Delete one first/);
    expect(layoutErrorText(new Error("network"), "deleted")).toBe("The layout could not be deleted. Check your connection, then try again.");
  });
});

describe("UI2-4d Multi-chart: shared reads", () => {
  it("four charts on two symbols ask twice: identical reads in flight are joined", async () => {
    const w = wire();
    const pool = new CandlePool(w.fetch, { maxInFlight: 4, freshMs: 30_000 });
    const read = (s: string) => pool.fetchCandles([s], "5min", 10, "NSE", undefined, undefined, { forming: false, volume_from_future: true });
    const answers = [read("NIFTY 50"), read("NIFTY 50"), read("NIFTY BANK"), read("NIFTY BANK")];
    await Promise.resolve();
    expect(w.calls.map((c) => c[0][0])).toEqual(["NIFTY 50", "NIFTY BANK"]);
    await w.settle(0); await w.settle(1);
    const got = await Promise.all(answers);
    expect(got[0]).toBe(got[1]);
    expect(pool.sent).toBe(2);
    // Another timeframe or lookback of the same symbol is its own read.
    void pool.fetchCandles(["NIFTY 50"], "15min", 20, "NSE");
    await Promise.resolve();
    expect(w.calls).toHaveLength(3);
  });

  it("a read answered within freshMs is answered again; after it, or after a failure, it is sent again", async () => {
    let t = 0;
    const w = wire();
    const pool = new CandlePool(w.fetch, { maxInFlight: 2, freshMs: 30_000 }, () => t);
    const read = () => pool.fetchCandles(["SBIN"], "day", 730, "NSE");
    const first = read();
    await Promise.resolve();
    await w.settle(0);
    await first;
    t = 29_000;
    await read();
    expect(w.calls).toHaveLength(1);
    t = 31_000;
    const again = read();
    await Promise.resolve();
    expect(w.calls).toHaveLength(2);
    await w.settle(1, true);
    await expect(again).rejects.toThrow("down");
    const retry = read();
    await Promise.resolve();
    expect(w.calls).toHaveLength(3);                                       // a failure is never kept
    await w.settle(2);
    await retry;
  });

  it("eight first loads are staggered: two on the wire at once, the next as one is answered", async () => {
    const w = wire();
    const pool = new CandlePool(w.fetch, { maxInFlight: 2, freshMs: 30_000 });
    const all = ["A", "B", "C", "D", "E", "F", "G", "H"].map((s) => pool.fetchCandles([s], "5min", 10, "NSE"));
    await Promise.resolve();
    expect(w.calls.map((c) => c[0][0])).toEqual(["A", "B"]);
    await w.settle(0);
    expect(w.calls.map((c) => c[0][0])).toEqual(["A", "B", "C"]);
    for (let i = 1; i < 8; i += 1) await w.settle(i);
    await Promise.all(all);
    expect(pool.sent).toBe(8);
  });

  it("a paused chart (off screen, hidden tab) reads nothing; a read missed while paused follows once on resume", () => {
    const tick = vi.fn();
    const hidden = new FeedGate(tick, true);
    hidden.timer();
    hidden.timer();
    expect(tick).not.toHaveBeenCalled();
    hidden.setPaused(false);
    expect(tick).toHaveBeenCalledTimes(1);
    hidden.setPaused(false);
    expect(tick).toHaveBeenCalledTimes(1);
    hidden.setPaused(true);
    hidden.setPaused(false);                                               // nothing fell due meanwhile: no read
    expect(tick).toHaveBeenCalledTimes(1);
    hidden.timer();
    expect(tick).toHaveBeenCalledTimes(2);

    const shown = vi.fn();
    const gate = new FeedGate(shown);                                      // the Chart tab: reads at once, every tick
    gate.timer();
    expect(shown).toHaveBeenCalledTimes(2);
  });
});
