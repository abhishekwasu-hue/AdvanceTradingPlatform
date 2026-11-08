import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { eodApi } from "../api/eod";
import { eodQuotes, resetEodCache } from "../chart/eodQuotes";
import { QUOTE_POLL_MS, QuotePoller, keepPrices, quoteBatches, type QuoteRow, type QuotesReply, type WatchItem } from "../chart/watchlistsV2";
import { FALLBACK_PLAN, HELD_TEXT, RATE_LIMITED, batchSize, heldBack, runInBatches, type BatchReply } from "./universe";

/** M90 (owner): the Screener without the fixed 50 (store-first, streamed, paced) and every watchlist row priced. */
const syms = (n: number, prefix = "S") => Array.from({ length: n }, (_, i) => `${prefix}${i}`);
const row = (symbol: string): WatchItem => ({ symbol, exchange: "NSE" });

function reply(batch: string[], extra: Partial<BatchReply> = {}, held: string[] = []): BatchReply {
  const read = batch.filter((s) => !(extra.pending ?? []).includes(s));
  return {
    run_id: 1, text: "close > 1", base_tf: "1d", data_source: "lake:daily", scanned: read.length, matched: read.filter((s) => !held.includes(s)),
    results: read.map((s) => (held.includes(s) ? { symbol: s, matched: false, reason: HELD_TEXT, code: RATE_LIMITED } : { symbol: s, matched: true, reason: null })),
    funnel: { universe: read.length, with_data: read.length - held.length, stages: [{ text: "close > 1", survivors: read.length - held.length }] },
    disclaimer: "x", budget: { broker_calls: 0, refused: 0, per_minute: 50 }, ...extra,
  };
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); resetEodCache(); });

describe("M90 B2 / B3: the screener streams a list of any size", () => {
  it("a timeframe the store serves goes as ONE request for 500 symbols", async () => {
    const plan = { ...FALLBACK_PLAN, size: 10, store_timeframes: ["1d", "1w", "1M"] };
    expect(batchSize(plan, "1d")).toBe(500);
    expect(batchSize(plan, "15m")).toBe(10);
    expect(batchSize(FALLBACK_PLAN, "1d")).toBe(10);                          // flag off: the broker's batch
    const sent: string[][] = [];
    const out = await runInBatches(syms(500), plan, { run: async (b) => { sent.push(b); return reply(b); }, onProgress: vi.fn(), now: () => 0 },
      batchSize(plan, "1d"));
    expect(sent.map((b) => b.length)).toEqual([500]);
    expect(out?.scanned).toBe(500);
    expect(out?.matched).toHaveLength(500);
  });

  it("pending symbols are sent next, in order, with progress '120 / 500'-style counts and partial results", async () => {
    const progress: [number, number, number][] = [];
    const sent: string[][] = [];
    const out = await runInBatches(syms(300), { ...FALLBACK_PLAN, size: 300 }, {
      run: async (b) => { sent.push(b); return reply(b, b.length > 120 ? { pending: b.slice(120) } : {}); },
      onProgress: (done, total, merged) => progress.push([done, total, merged.results.length]), now: () => 0, sleep: async () => undefined,
    }, 300);
    expect(sent.map((b) => b.length)).toEqual([300, 180, 60]);
    expect(sent.flat().filter((s, i, all) => all.indexOf(s) === i)).toEqual(syms(300));
    expect(progress).toEqual([[120, 300, 120], [240, 300, 240], [300, 300, 300]]);
    expect(out?.scanned).toBe(300);
  });

  it("cancel: stops between batches and keeps what ran", async () => {
    let stop = false;
    const out = await runInBatches(syms(500), { ...FALLBACK_PLAN, size: 50 }, {
      run: async (b) => { stop = true; return reply(b); }, onProgress: vi.fn(), stopped: () => stop, now: () => 0,
    });
    expect(out?.scanned).toBe(50);
  });
});

describe("M90 B5: the broker's limit slows the queue, never fails the run", () => {
  it("a symbol the limit held back is tried again later, more slowly, and the screen says slowed", async () => {
    const waits: number[] = [];
    const slowed: boolean[] = [];
    let first = true;
    const out = await runInBatches(syms(20), { ...FALLBACK_PLAN, size: 20 }, {
      run: async (b) => { const held = first ? ["S3", "S7"] : []; first = false; return reply(b, { budget: { broker_calls: b.length, refused: held.length, per_minute: 50, slowed: true } }, held); },
      onProgress: (_d, _t, _m, s) => slowed.push(s), sleep: async (ms) => { waits.push(ms); }, now: () => 0,
    });
    expect(out?.matched.sort()).toEqual(syms(20).sort());                    // the retried two matched too
    expect(heldBack(out)).toEqual([]);
    expect(out?.results.filter((r) => r.symbol === "S3")).toHaveLength(1);    // one row per symbol
    expect(slowed.every(Boolean)).toBe(true);
    expect(waits[0]).toBeGreaterThan(Math.ceil((20 * 60_000) / 50));         // the pause grew past the plain share
  });

  it("a 429 from the server or the network is retried with a growing pause; never an error", async () => {
    const waits: number[] = [];
    let calls = 0;
    const out = await runInBatches(syms(30), { ...FALLBACK_PLAN, size: 10 }, {
      run: async (b) => { calls += 1; if (calls === 2 || calls === 3) throw new ApiError(429, "Too many requests"); return reply(b); },
      onProgress: vi.fn(), sleep: async (ms) => { waits.push(ms); }, now: () => 0,
    });
    expect(out?.scanned).toBe(30);
    expect(waits).toEqual([4_000, 8_000]);                                   // 2 s doubled each time
    await expect(runInBatches(syms(5), FALLBACK_PLAN, { run: async () => { throw new ApiError(422, "bad screen"); }, onProgress: vi.fn() }))
      .rejects.toThrow("bad screen");                                         // a real problem with the scan still says so
  });

  it("past every retry, the symbols are listed as held back (run again), and the run still ends", async () => {
    let calls = 0;
    const out = await runInBatches(syms(20), { ...FALLBACK_PLAN, size: 10 }, {
      run: async (b) => { calls += 1; if (calls > 1) throw new ApiError(503, "busy"); return reply(b); },
      onProgress: vi.fn(), sleep: async () => undefined, now: () => 0,
    });
    expect(out?.scanned).toBe(20);
    expect(heldBack(out)).toEqual(syms(20).slice(10));
  });
});

describe("M90 B4: every watchlist row priced, one request per 500 rows", () => {
  it("500 rows = 1 quote request, 501 = 2", async () => {
    expect(quoteBatches(Array.from({ length: 500 }, (_, i) => row(`S${i}`)))).toHaveLength(1);
    expect(quoteBatches(Array.from({ length: 501 }, (_, i) => row(`S${i}`))).map((b) => b.length)).toEqual([500, 1]);
    vi.useFakeTimers();
    const load = vi.fn(async (items: WatchItem[]): Promise<QuotesReply> => ({ items: items.map((i) => ({ ...i, ltp: 1, change: 0, change_pct: 0, missing: false })) }));
    const data = vi.fn();
    const p = new QuotePoller(load, { data, error: vi.fn() }, () => false, () => Date.now());
    p.update(Array.from({ length: 501 }, (_, i) => row(`S${i}`)));
    await vi.advanceTimersByTimeAsync(0);
    expect(load).toHaveBeenCalledTimes(2);
    expect(data.mock.calls[0][0].items).toHaveLength(501);                   // all prices in one answer to the panel
    p.close();
  });

  it("a hidden tab asks for nothing; a slowed answer doubles the wait and keeps the last prices", async () => {
    vi.useFakeTimers();
    const load = vi.fn(async (): Promise<QuotesReply> => ({ items: [], slowed: true }));
    let hidden = true;
    const p = new QuotePoller(load, { data: vi.fn(), error: vi.fn() }, () => hidden, () => Date.now());
    p.update([row("A")]);
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS * 3);
    expect(load).not.toHaveBeenCalled();                                      // not on screen: not fetched
    hidden = false;
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS);
    expect(load).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS);
    expect(load).toHaveBeenCalledTimes(1);                                    // slowed: 20 s, not 10 s
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS);
    expect(load).toHaveBeenCalledTimes(2);
    p.close();
    const old: QuoteRow = { symbol: "A", exchange: "NSE", ltp: 101, change: 1, change_pct: 1, missing: false };
    const kept = keepPrices(new Map([["NSE:A", old]]), [{ symbol: "A", exchange: "NSE", ltp: null, change: null, change_pct: null, missing: true, slowed: true }]);
    expect(kept.get("NSE:A")?.ltp).toBe(101);
  });

  it("without a broker, every NSE row gets its last close, 60 a request, read once for a few minutes", async () => {
    const closes = vi.spyOn(eodApi, "closes").mockImplementation(async (symbols) => ({
      items: symbols.map((s) => ({ symbol: s, close: 10, change: 1, change_pct: 10, missing: false, date: "2026-11-06" })) as never, as_of: "2026-11-06", source: "eod" }));
    const items = Array.from({ length: 200 }, (_, i) => row(`E${i}`));
    const out = await eodQuotes(items, undefined, () => 0);
    expect(closes).toHaveBeenCalledTimes(4);                                  // 60 + 60 + 60 + 20
    expect(out.items.every((r) => !r.missing && r.ltp === 10)).toBe(true);
    expect(out.eod_as_of).toBe("2026-11-06");
    await eodQuotes(items, undefined, () => 1_000);
    expect(closes).toHaveBeenCalledTimes(4);                                  // the next poll reads nothing new
  });
});

