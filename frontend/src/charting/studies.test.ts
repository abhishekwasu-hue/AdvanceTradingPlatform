import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import parity from "./__fixtures__/study_parity.json";
import { ApiError } from "../api/client";
import { STUDY_PANE, LightweightEngine, type LineSeriesLike } from "./lightweight";
import { StudiesList } from "./StudiesPanel";
import { createStudyMath, mainThreadMath } from "./studyClient";
import { ema, firesAt, rsi, runMath, sma, toLineData, type Num } from "./studyMath";
import { BUSY_RETRY_MS } from "./priceAction";
import { StudiesLoader, loadStudyList, saveStudyList, studiesApi, type StudiesResponse, type StudiesState } from "./studies";

// __fixtures__/study_parity.json is a byte-identical copy of backend/tests/fixtures/charts/study_parity.json - the
// screener runtime's SMA / EMA / RSI on the frozen 15m fixture (backend/tests/test_ch6_chart_studies.py checks the copy;
// rebuild both with `python -m tests.ch6_study_cases --write`).
const P = parity as unknown as { bars: [number, number][]; studies: Record<"sma" | "ema" | "rsi", { expr: string; period: number; values: (number | null)[] }> };
const closes = P.bars.map(([, c]) => c);

function same(got: Num[], want: (number | null)[], rel = 1e-9) {
  expect(got.length).toBe(want.length);
  got.forEach((g, i) => {
    const w = want[i];
    if (w == null) expect(g, `bar ${i}`).toBeNull();
    else expect(Math.abs((g as number) - w) <= rel * Math.max(1, Math.abs(w)), `bar ${i}: ${g} vs ${w}`).toBe(true);
  });
}

describe("browser study math = the screener runtime (CH6 parity)", () => {
  it("SMA, EMA and Wilder's RSI match the backend value for value on the frozen fixture", () => {
    same(sma(closes, P.studies.sma.period), P.studies.sma.values);
    same(ema(closes, P.studies.ema.period), P.studies.ema.values);
    same(rsi(closes, P.studies.rsi.period), P.studies.rsi.values);
    expect(P.studies.rsi.values.filter((v) => v != null).length).toBe(closes.length - 14);
  });

  it("50,000 bars of the three kernels plus the line conversion in well under a second", () => {
    const n = 50_000;
    const xs = Array.from({ length: n }, (_, i) => 20_000 + Math.sin(i / 50) * 300 + (i % 17));
    const times = Array.from({ length: n }, (_, i) => 1_600_000_000 + i * 900);
    const t0 = performance.now();
    const a = sma(xs, 20), b = ema(xs, 50), c = rsi(xs, 14);
    const line = toLineData(times, b);
    const took = performance.now() - t0;
    expect(a.length + b.length + c.length).toBe(3 * n);
    expect(line.length).toBe(n - 49);
    expect(took).toBeLessThan(1000);
  });

  it("lines skip gaps; fires sit on the candle's close; the worker protocol runs the same functions", () => {
    expect(toLineData([1, 2, 3, 4], [null, 5, Number.NaN, 7])).toEqual([{ time: 2, value: 5 }, { time: 4, value: 7 }]);
    expect(firesAt([2, 9], [1, 2, 3], [10, 20, 30])).toEqual([{ time: 2, value: 20 }]);
    expect(runMath({ op: "sma", xs: [1, 2, 3], period: 2 })).toEqual([null, 1.5, 2.5]);
    expect(runMath({ op: "line", times: [5], values: [1] })).toEqual([{ time: 5, value: 1 }]);
  });
});

describe("the study worker client", () => {
  it("answers by request id through the worker, and falls back to the main thread", async () => {
    let onmessage: ((e: MessageEvent) => void) | null = null;
    const posted: { id: number; req: unknown }[] = [];
    const fake = {
      postMessage: (m: unknown) => { posted.push(m as { id: number; req: unknown }); },
      terminate: vi.fn(),
      get onmessage() { return onmessage; }, set onmessage(f) { onmessage = f; },
      onerror: null as ((e: unknown) => void) | null,
    };
    const m = createStudyMath(() => fake);
    expect(m.inWorker).toBe(true);
    const p1 = m.run({ op: "sma", xs: [1, 2, 3], period: 2 });
    const p2 = m.run({ op: "ema", xs: [1, 2], period: 1 });
    onmessage!({ data: { id: posted[1].id, result: [9] } } as MessageEvent);           // answers out of order
    onmessage!({ data: { id: posted[0].id, result: [7] } } as MessageEvent);
    expect(await p1).toEqual([7]);
    expect(await p2).toEqual([9]);
    const p3 = m.run({ op: "rsi", xs: [1, 2], period: 1 });
    fake.onerror?.(new Event("error"));
    await expect(p3).rejects.toThrow("stopped");
    expect(m.inWorker).toBe(false);
    expect(await m.run({ op: "sma", xs: [2, 4], period: 2 })).toEqual([null, 3]);       // main thread from now on
    m.dispose();
    expect(fake.terminate).toHaveBeenCalled();
    expect((await createStudyMath(() => { throw new Error("no workers"); }).run({ op: "sma", xs: [1], period: 1 }))).toEqual([1]);
    expect(mainThreadMath().inWorker).toBe(false);
  });
});

const response = (over: Partial<StudiesResponse> = {}): StudiesResponse => ({
  schema: "study/1", instrument: "NSE:X", tf: "15m", at: "", start: "", source: "lake:15m", as_of: null, bars: 3, warmup_bars: 20, times: [1, 2, 3],
  first_time: 1, last_time: 3, fingerprint: "f", note: "reference", reason: null,
  studies: [
    { id: 0, expr: "SMA(close, 20)", text: "SMA(close, 20)", ok: true, problems: [], kind: "num", unit: "price", pane: "price", lookback: 20, values: [1, null, 3], last: 3, last_text: "3.00" },
    { id: 1, expr: "Rank(close)", text: "Rank(close)", ok: false, problems: [{ message: "Rank ranks a universe of symbols", pos: 0 }], kind: "num", unit: "count", pane: "own", lookback: 1 },
    { id: 2, expr: "close > SMA(close, 50)", text: "close > SMA(close, 50)", ok: true, problems: [], kind: "bool", unit: null, pane: "own", lookback: 50, fires: [2], true_bars: 2, last: true },
  ],
  ...over,
});

describe("studies on the chart (CH6)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("asks for every expression as of a time, and lists results and refusals", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(response()), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await studiesApi.get("NSE:X", ["SMA(close, 20)", "RSI(14)"], { tf: "15m", at: "2026-03-02T04:00:00.000Z" });
    const url = decodeURIComponent(String((fetchMock.mock.calls as unknown as [string][])[0][0]));
    expect(url).toContain("/charts/studies?instrument=NSE:X&tf=15m&expr=SMA(close,+20)&expr=RSI(14)&at=2026-03-02T04:00:00.000Z");
    const html = renderToStaticMarkup(createElement(StudiesList, { exprs: response().studies.map((s) => s.expr), result: response(), onRemove: () => {} }));
    expect(html).toContain("= 3.00");
    expect(html).toContain("Rank ranks a universe of symbols");
    expect(html).toContain("= true · true on 2 bars");
    expect(html).toContain('data-ok="false"');
  });

  it("a new question clears the old answer; a late answer is dropped; a failure draws nothing; 503 hides the panel", async () => {
    const pending: { resolve: (r: StudiesResponse) => void; reject: (e: unknown) => void }[] = [];
    const states: StudiesState[] = [];
    const last = () => states[states.length - 1];
    const loader = new StudiesLoader(() => new Promise((resolve, reject) => pending.push({ resolve, reject })), (s) => states.push(s));
    const q = (at: string) => ({ instrument: "NSE:X", tf: "15m" as const, exprs: ["SMA(close, 20)"], at, start: null });
    loader.load(q("a"));
    pending[0].resolve(response());
    await Promise.resolve(); await Promise.resolve();
    expect(last().result).not.toBeNull();
    loader.load(q("b"));
    expect(last().result).toBeNull();
    loader.load(q("c"));
    pending[1].resolve(response());
    await Promise.resolve(); await Promise.resolve();
    expect(last().result).toBeNull();
    pending[2].reject(new ApiError(422, "too long"));
    await Promise.resolve(); await Promise.resolve();
    expect(last()).toMatchObject({ result: null, failed: "too long" });
    loader.load(q("d"));
    pending[3].reject(new ApiError(503, "off"));
    await Promise.resolve(); await Promise.resolve();
    expect(last()).toEqual({ result: null, failed: null, available: false });
    loader.load({ ...q("e"), exprs: [] });
    expect(pending.length).toBe(4);                                                     // nothing to ask for: no request
  });

  it("a busy server (503 busy, retry) is asked again after a pause, and a newer question cancels the retry", async () => {
    vi.useFakeTimers();
    try {
      const pending: { resolve: (r: StudiesResponse) => void; reject: (e: unknown) => void }[] = [];
      const states: StudiesState[] = [];
      const loader = new StudiesLoader(() => new Promise((resolve, reject) => pending.push({ resolve, reject })), (s) => states.push(s));
      const q = (at: string) => ({ instrument: "NSE:X", tf: "15m" as const, exprs: ["SMA(close, 20)"], at, start: null });
      loader.load(q("a"));
      pending[0].reject(new ApiError(503, "busy", "busy, retry: the chart computations are all in use"));
      await vi.advanceTimersByTimeAsync(0);
      expect(pending.length).toBe(1);
      expect(states[states.length - 1].available).not.toBe(false);                      // busy is not "flag off"
      await vi.advanceTimersByTimeAsync(BUSY_RETRY_MS);
      expect(pending.length).toBe(2);                                                   // asked again
      pending[1].resolve(response());
      await vi.advanceTimersByTimeAsync(0);
      expect(states[states.length - 1].result).not.toBeNull();
      loader.load(q("b"));
      pending[2].reject(new ApiError(503, "busy", "busy, retry: your previous chart computation is still running"));
      await vi.advanceTimersByTimeAsync(0);
      loader.load(q("c"));                                                              // a newer question: no retry of "b"
      await vi.advanceTimersByTimeAsync(BUSY_RETRY_MS * 2);
      expect(pending.length).toBe(4);
    } finally {
      vi.useRealTimers();
    }
  });

  it("the study list is kept per browser, capped, and a broken storage gives none", () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
    saveStudyList(["SMA(close, 20)", " ", "RSI(14)", "a", "b", "c", "d", "e"], storage);
    expect(loadStudyList(storage)).toEqual(["SMA(close, 20)", "RSI(14)", "a", "b", "c"]);
    expect(loadStudyList({ getItem: () => "{oops" })).toEqual([]);
    expect(loadStudyList({ getItem: () => { throw new Error("blocked"); } })).toEqual([]);
  });

  it("the engine draws a study line on the price pane or the study pane, replaces data in place and removes it", () => {
    const made: { pane: number; color: string; title: string; data: unknown[] }[] = [];
    const removed: unknown[] = [];
    const lines = {
      add: (pane: number, color: string, title: string): LineSeriesLike => {
        const rec = { pane, color, title, data: [] as unknown[] };
        made.push(rec);
        return { setData: (d) => { rec.data = d; } };
      },
      remove: (l: LineSeriesLike) => { removed.push(l); },
    };
    const chart = { applyOptions() {}, paneSize: () => ({ width: 1, height: 1 }), priceScale: () => ({ width: () => 0 }),
                    subscribeCrosshairMove() {}, unsubscribeCrosshairMove() {}, subscribeClick() {}, unsubscribeClick() {},
                    timeScale: () => ({ subscribeVisibleTimeRangeChange() {}, unsubscribeVisibleTimeRangeChange() {}, logicalToCoordinate: () => 0, coordinateToLogical: () => 0 }) };
    const candles = { setData() {}, update() {}, createPriceLine: () => ({}), removePriceLine() {}, priceToCoordinate: () => 0, coordinateToPrice: () => 0,
                      data: () => [], subscribeDataChanged() {}, unsubscribeDataChanged() {}, attachPrimitive() {}, detachPrimitive() {} };
    const engine = new LightweightEngine(chart, candles, undefined, lines);
    engine.setStudy("s0", { data: [{ time: 1, value: 2 }], color: "#111111", pane: "price", title: "SMA" });
    engine.setStudy("s1", { data: [{ time: 1, value: 50 }], color: "#222222", pane: "own", title: "RSI" });
    expect(made.map((m) => [m.pane, m.title])).toEqual([[0, "SMA"], [STUDY_PANE, "RSI"]]);
    engine.setStudy("s0", { data: [{ time: 2, value: 3 }], color: "#111111", pane: "price" });
    expect(made.length).toBe(2);                                                         // same series, new data
    expect(made[0].data).toEqual([{ time: 2, value: 3 }]);
    engine.setStudy("s0", { data: [], color: "#111111", pane: "own" });                 // moved pane: a new series
    expect(made.length).toBe(3);
    expect(removed.length).toBe(1);
    engine.dispose();
    expect(removed.length).toBe(3);
  });
});
