import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { RunResponse } from "./api";
import { UniversePicker, universeOptions } from "./UniversePicker";
import {
  CUSTOM, DEFAULT_CHOICE, DEFAULT_KEY, FALLBACK_PLAN, NIFTY50_FALLBACK, RUN_MAX, effectiveUniverse, heldBack, isUniverses, listLabel, mergeRuns,
  planBatches, runInBatches, serverHasDefault, waitAfter, type BatchBudget, type UniversesResponse,
} from "./universe";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").replace(/&amp;/g, "&");
const GROUPS: UniversesResponse["groups"] = [
  { group: "INDEX_STOCKS", label: "Index stocks", lists: [{ key: DEFAULT_KEY, name: "NIFTY 50 stocks (50)", count: 50 },
    { key: "index-nifty500", name: "NIFTY 500 stocks (500)", count: 500 }] },
  { group: "SECTORS", label: "Sectors", lists: [{ key: "sector-it", name: "IT", count: 20 }] },
  { group: "FNO", label: "F&O", lists: [{ key: "fno-stocks", name: "F&O stocks (180)", count: 180 }] },
];
const syms = (n: number, prefix = "S") => Array.from({ length: n }, (_, i) => `${prefix}${i}`);

function reply(batch: string[], budget?: BatchBudget): RunResponse & { budget?: BatchBudget } {
  const matched = batch.filter((_, i) => i % 2 === 0);
  return {
    run_id: 1, text: "close > 1", base_tf: "1d", data_source: "broker:upstox", scanned: batch.length, matched,
    results: batch.map((s) => ({ symbol: s, matched: matched.includes(s), reason: null })),
    funnel: { universe: batch.length, with_data: batch.length, stages: [{ text: "close > 1", survivors: matched.length }] },
    disclaimer: "Matches are not recommendations.", ...(budget ? { budget } : {}),
  };
}

describe("the scan universe (owner order 23 Oct)", () => {
  it("the picker opens on NIFTY 50 and offers the index, sector, F&O and watchlist lists, and the trader's own symbols", () => {
    const html = renderToStaticMarkup(
      <UniversePicker groups={GROUPS} builtinDefault={false} watchlists={[{ id: 3, name: "Banks", symbols: ["SBIN"], count: 1, created_at: null, updated_at: null }]}
                      choice={DEFAULT_CHOICE} onChoice={vi.fn()} symbolsText="" onSymbolsText={vi.fn()} count={50} name="NIFTY 50" maxSymbols={500} />);
    // SCR-FIX E (carried from D8): the shared picker, not the browser's <select>; the closed button shows the choice
    expect(html).not.toContain("<select");
    expect(html).toContain(`data-combobox="${DEFAULT_CHOICE}"`);
    expect(html).toContain('data-testid="universe-select"');
    expect(text(html)).toContain("NIFTY 50 (50)");
    expect(text(html)).toContain("50 stocks · NIFTY 50");
    const rows = universeOptions(GROUPS, false, [{ id: 3, name: "Banks", symbols: ["SBIN"], count: 1, created_at: null, updated_at: null }]);
    const labels = rows.map((o) => o.label);
    for (const want of ["NIFTY 500 (500)", "IT (20)", "F&O stocks (180)", "Banks (1)", "Type my own symbols"]) expect(labels).toContain(want);
    expect(rows.find((o) => o.label === "Banks (1)")).toMatchObject({ value: "wl:3", group: "My watchlists" });
    expect(rows[rows.length - 1]).toEqual({ value: CUSTOM, label: "Type my own symbols", group: "Other" });
    expect(html).not.toContain("screener-symbols");                                   // the box shows for "my own symbols" only
  });

  it("without a server list, the built-in NIFTY 50 names are the first choice", () => {
    const html = renderToStaticMarkup(<UniversePicker groups={[]} builtinDefault watchlists={null} choice={DEFAULT_CHOICE} onChoice={vi.fn()}
                                                      symbolsText="" onSymbolsText={vi.fn()} count={50} name="NIFTY 50" maxSymbols={500} />);
    expect(html).toContain(`data-combobox="${DEFAULT_CHOICE}"`);
    expect(text(html)).toContain("NIFTY 50");
    expect(universeOptions([], true, null)[0]).toEqual({ value: DEFAULT_CHOICE, label: "NIFTY 50", group: "Index stocks" });
    expect(NIFTY50_FALLBACK).toHaveLength(50);
    expect(new Set(NIFTY50_FALLBACK).size).toBe(50);
    expect(NIFTY50_FALLBACK.every((s) => /^[A-Z0-9&-]+$/.test(s))).toBe(true);          // names only: no prices, no weights
    expect(serverHasDefault(null)).toBe(false);
    expect(serverHasDefault({ default_key: DEFAULT_KEY, groups: GROUPS, batch: FALLBACK_PLAN })).toBe(true);
  });

  it("custom symbols show the box", () => {
    const html = renderToStaticMarkup(<UniversePicker groups={GROUPS} builtinDefault={false} watchlists={null} choice={CUSTOM} onChoice={vi.fn()}
                                                      symbolsText="TCS" onSymbolsText={vi.fn()} count={1} name="your own symbols" maxSymbols={500} />);
    expect(html).toContain("screener-symbols");
    expect(text(html)).toContain("1 stock · your own symbols");
  });

  it("an empty choice never runs empty: it falls back to NIFTY 50", () => {
    expect(effectiveUniverse([], [])).toEqual({ symbols: [...NIFTY50_FALLBACK], fellBack: true });
    expect(effectiveUniverse([], ["AAA", "BBB"])).toEqual({ symbols: ["AAA", "BBB"], fellBack: true });   // the server's NIFTY 50 first
    expect(effectiveUniverse(["TCS"], ["AAA"])).toEqual({ symbols: ["TCS"], fellBack: false });
  });

  it("names a list the way a trader says it", () => {
    expect(listLabel({ key: DEFAULT_KEY, name: "NIFTY 50 stocks (50)", count: 50 }, "INDEX_STOCKS")).toBe("NIFTY 50");
    expect(listLabel({ key: "fno-stocks", name: "F&O stocks (180)", count: 180 }, "FNO")).toBe("F&O stocks");
    expect(isUniverses({ default_key: DEFAULT_KEY, groups: GROUPS, batch: FALLBACK_PLAN })).toBe(true);
    expect(isUniverses({ detail: "disabled" })).toBe(false);
  });

  it("splits a long universe into batches no larger than one run", () => {
    expect(planBatches(syms(120), 40).map((b) => b.length)).toEqual([40, 40, 40]);
    expect(planBatches(syms(120), 500).every((b) => b.length <= RUN_MAX)).toBe(true);
    expect(planBatches(syms(7), 0).map((b) => b.length)).toEqual([1, 1, 1, 1, 1, 1, 1]);
    expect(planBatches([], 10)).toEqual([]);
  });

  it("waits after a batch for the broker calls it made at the per-minute share", () => {
    expect(waitAfter(0, 100, 0)).toBe(0);                     // served from cached bars: no wait
    expect(waitAfter(40, 100, 0)).toBe(24_000);               // 40 calls at 100 a minute
    expect(waitAfter(40, 100, 30_000)).toBe(0);               // the batch itself took longer
    expect(waitAfter(10, 50, 2_000)).toBe(10_000);
  });

  it("runs batch after batch, never faster than the budget, with the results growing", async () => {
    const symbols = syms(100);
    const plan = { size: 40, per_minute: 100, per_second: 4, max_symbols: 500 };
    let clock = 0;
    const waits: number[] = [];
    const sent: string[][] = [];
    const starts: number[] = [];
    const progress: [number, number, number][] = [];
    const out = await runInBatches(symbols, plan, {
      run: async (batch) => { starts.push(clock); sent.push(batch); clock += 1_000; return reply(batch, { broker_calls: batch.length, refused: 0, per_minute: 100 }); },
      onProgress: (done, total, merged) => progress.push([done, total, merged.results.length]),
      sleep: async (ms) => { waits.push(ms); clock += ms; },
      now: () => clock,
    });
    expect(sent.map((b) => b.length)).toEqual([40, 40, 20]);
    expect(new Set(sent.flat()).size).toBe(100);
    expect(waits).toEqual([23_000, 23_000]);                  // 40 calls a minute-share of 100 = 24 s, less the batch's 1 s
    // never more broker calls in any minute than the share
    for (const t of starts) {
      expect(sent.filter((_, i) => starts[i] >= t && starts[i] < t + 60_000).reduce((n, b) => n + b.length, 0)).toBeLessThanOrEqual(plan.per_minute);
    }
    expect(progress).toEqual([[40, 100, 40], [80, 100, 80], [100, 100, 100]]);
    expect(out?.scanned).toBe(100);
    expect(out?.funnel.universe).toBe(100);
    expect(out?.funnel.stages[0].survivors).toBe(50);         // 20 + 20 + 10, added up stage by stage
    expect(out?.matched).toHaveLength(50);
  });

  it("a batch answered from cached bars waits for nothing; a reply without its budget counts every symbol", async () => {
    const waits: number[] = [];
    await runInBatches(syms(60), { ...FALLBACK_PLAN, size: 30 }, {
      run: async (b) => reply(b, { broker_calls: 0, refused: 0, per_minute: 50 }), onProgress: vi.fn(), sleep: async (ms) => { waits.push(ms); }, now: () => 0,
    });
    expect(waits).toEqual([]);
    await runInBatches(syms(60), { ...FALLBACK_PLAN, size: 30, per_minute: 60 }, {
      run: async (b) => reply(b), onProgress: vi.fn(), sleep: async (ms) => { waits.push(ms); }, now: () => 0,
    });
    expect(waits).toEqual([30_000]);
  });

  it("stops when asked and keeps what ran", async () => {
    let stop = false;
    const out = await runInBatches(syms(100), { ...FALLBACK_PLAN, size: 25 }, {
      run: async (b) => { stop = true; return reply(b, { broker_calls: 0, refused: 0, per_minute: 50 }); }, onProgress: vi.fn(),
      stopped: () => stop, now: () => 0,
    });
    expect(out?.scanned).toBe(25);
  });

  it("lists the symbols the broker's limit held back", () => {
    const run = mergeRuns([reply(["A", "B"])]);
    run.results.push({ symbol: "C", matched: false, reason: "waited for the broker's limit - run again shortly" });
    expect(heldBack(run)).toEqual(["C"]);
    expect(heldBack(null)).toEqual([]);
  });
});
