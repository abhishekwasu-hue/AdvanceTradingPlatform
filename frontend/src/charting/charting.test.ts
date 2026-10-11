import { LineSeries } from "lightweight-charts";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ANCHOR_RULES, DrawingConflict, drawingProblem, drawingsApi, type DrawingKind, type DrawingV1, type StoredDrawing } from "./drawings";
import { LightweightEngine, type CandleSeriesLike, type ChartLike, type LineSeriesLike } from "./lightweight";
import type { EngineEvent } from "./engine";

const T1 = "2026-03-02T04:00:00Z";
const T2 = "2026-03-03T05:15:00Z";
const T3 = "2026-03-04T06:30:00Z";

function sample(kind: DrawingKind): DrawingV1 {
  const special: Partial<Record<DrawingKind, DrawingV1["anchors"]>> = {
    hline: [{ p: 101.5 }], vline: [{ t: T1 }], text: [{ t: T1, p: 99 }],
    long_position: [{ t: T1, p: 100 }, { p: 95 }, { p: 112 }], short_position: [{ t: T1, p: 100 }, { p: 106 }, { p: 90 }],
  };
  const anchors = special[kind] ?? [T1, T2, T3].slice(0, ANCHOR_RULES[kind].length).map((t, i) => ({ t, p: 100 + i }));
  return { kind, anchors, ...(kind === "text" ? { text: "note" } : {}) };
}

describe("drawing/1 schema (mirrors the backend)", () => {
  it("accepts a well-formed drawing of every kind", () => {
    for (const kind of Object.keys(ANCHOR_RULES) as DrawingKind[]) expect(drawingProblem(sample(kind)), kind).toBeNull();
  });

  it("refuses the same things the server refuses", () => {
    expect(drawingProblem({ kind: "trendline", anchors: [{ t: T1, p: 1 }] })).toMatch(/needs 2 anchor/);
    expect(drawingProblem({ kind: "hline", anchors: [{ t: T1 }] })).toMatch(/needs a price/);
    expect(drawingProblem({ kind: "vline", anchors: [{ p: 1 }] })).toMatch(/needs a time/);
    expect(drawingProblem({ kind: "text", anchors: [{ t: T1, p: 1 }], text: "<b>" })).toMatch(/may not contain/);
    expect(drawingProblem({ ...sample("trendline"), levels: [0.5] })).toMatch(/Fibonacci/);
    expect(drawingProblem({ ...sample("trendline"), style: { color: "red" } })).toMatch(/#rrggbb/);
  });
});

describe("drawings API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("turns a stale edit (409) into a DrawingConflict carrying the current drawing", async () => {
    const current: StoredDrawing = { id: 7, symbol: "NIFTY 50", exchange: "NSE", kind: "trendline", drawing: sample("trendline"), version: 3, locked: false, updated_at: null };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ detail: { message: "changed", current } }), { status: 409, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const stale = { ...current, version: 2 };
    await expect(drawingsApi.update(stale, sample("trendline"))).rejects.toBeInstanceOf(DrawingConflict);
    try {
      await drawingsApi.update(stale, sample("trendline"));
    } catch (e) {
      expect((e as DrawingConflict).current?.version).toBe(3);
    }
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/charts/drawings/7");
    expect(JSON.parse(String(init.body))).toMatchObject({ version: 2 });
  });

  it("deletes with the version it was shown", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ deleted: 7 }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await drawingsApi.remove({ id: 7, symbol: "X", exchange: "NSE", kind: "hline", drawing: sample("hline"), version: 4, locked: false, updated_at: null });
    expect(String((fetchMock.mock.calls[0] as unknown as [string])[0])).toContain("/charts/drawings/7?version=4");
  });
});

function fakes() {
  const priceLines: { price: number; title: string }[] = [];
  const lineSeries: { data: { time: number; value: number }[]; removed: boolean; definition: unknown }[] = [];
  const handlers: Record<string, ((p: unknown) => void)[]> = { cross: [], click: [], range: [] };
  const candles: CandleSeriesLike & { data: unknown[]; updates: unknown[] } = {
    data: [], updates: [],
    setData(d) { this.data = d; },
    update(b) { this.updates.push(b); },
    createPriceLine(o) { const l = { price: o.price, title: o.title }; priceLines.push(l); return l as never; },
    removePriceLine(l) { priceLines.splice(priceLines.indexOf(l as never), 1); },
  };
  const chart: ChartLike = {
    addSeries(definition) { const s = { definition, data: [] as { time: number; value: number }[], removed: false, setData(d: { time: number; value: number }[]) { this.data = d; } }; lineSeries.push(s); return s as unknown as LineSeriesLike; },
    removeSeries(s) { (s as unknown as { removed: boolean }).removed = true; },
    subscribeCrosshairMove(h) { handlers.cross.push(h as (p: unknown) => void); },
    unsubscribeCrosshairMove(h) { handlers.cross = handlers.cross.filter((x) => x !== h); },
    subscribeClick(h) { handlers.click.push(h as (p: unknown) => void); },
    unsubscribeClick(h) { handlers.click = handlers.click.filter((x) => x !== h); },
    timeScale: () => ({
      subscribeVisibleTimeRangeChange(h) { handlers.range.push(h as (p: unknown) => void); },
      unsubscribeVisibleTimeRangeChange(h) { handlers.range = handlers.range.filter((x) => x !== h); },
    }),
  };
  return { chart, candles, priceLines, lineSeries, handlers };
}

describe("LightweightEngine (B-lite)", () => {
  it("renders the supported kinds, keeps the others, and loses nothing through serialize/deserialize", () => {
    const f = fakes();
    const engine = new LightweightEngine(f.chart, f.candles, (y) => 1000 - y);
    engine.setSymbol("NIFTY 50");
    engine.setTimeframe("5m");
    engine.addDrawing("a", sample("hline"));
    engine.addDrawing("b", sample("trendline"));
    engine.addDrawing("c", sample("fib_retracement"));
    expect(f.priceLines.map((l) => l.price)).toEqual([101.5]);
    expect(f.lineSeries[0].definition).toBe(LineSeries);                                // v5: series by definition
    expect(f.lineSeries[0].data).toEqual([{ time: Date.parse(T1) / 1000, value: 100 }, { time: Date.parse(T2) / 1000, value: 101 }]);
    expect(engine.supports("fib_retracement")).toBe(false);
    const layout = engine.serialize();
    expect(Object.keys(layout.drawings).sort()).toEqual(["a", "b", "c"]);              // kept even when not drawn yet
    const g = fakes();
    const other = new LightweightEngine(g.chart, g.candles);
    other.deserialize(layout);
    expect(other.serialize()).toEqual(layout);
    expect(g.priceLines.length).toBe(1);
  });

  it("updates and removes drawings and layers without leaking chart objects", () => {
    const f = fakes();
    const engine = new LightweightEngine(f.chart, f.candles);
    const events: EngineEvent[] = [];
    engine.on((e) => events.push(e));
    engine.addDrawing("a", sample("trendline"));
    engine.updateDrawing("a", { ...sample("trendline"), anchors: [{ t: T1, p: 90 }, { t: T3, p: 95 }] });
    expect(f.lineSeries[0].removed).toBe(true);
    expect(f.lineSeries[1].data.map((d) => d.value)).toEqual([90, 95]);
    expect(events[events.length - 1]).toMatchObject({ type: "drawingChanged", id: "a" });
    engine.setLayer("pa", { asOf: T1, lines: [{ price: 100, title: "swing high" }, { price: Number.NaN }] });
    expect(f.priceLines.map((l) => l.title)).toEqual(["swing high"]);
    engine.setLayer("pa", null);
    expect(f.priceLines).toEqual([]);
    engine.addDrawing("h", sample("hline"));
    engine.dispose();
    expect(f.priceLines).toEqual([]);
    expect(f.handlers.cross.length + f.handlers.click.length + f.handlers.range.length).toBe(0);
  });

  it("passes bars through and reports crosshair, click and range events with data values", () => {
    const f = fakes();
    const engine = new LightweightEngine(f.chart, f.candles, (y) => 500 - y);
    const events: EngineEvent[] = [];
    engine.on((e) => events.push(e));
    engine.setBars([{ time: 1, open: 1, high: 2, low: 0.5, close: 1.5 }]);
    engine.appendBar({ time: 2, open: 1.5, high: 2, low: 1, close: 1.8 });
    expect(f.candles.data.length).toBe(1);
    expect(f.candles.updates.length).toBe(1);
    f.handlers.cross[0]({ time: 2, point: { x: 10, y: 100 } });
    f.handlers.click[0]({ time: undefined });
    f.handlers.range[0]({ from: 1, to: 2 });
    expect(events).toEqual([{ type: "crosshair", time: 2, price: 400 }, { type: "click", time: null, price: null }, { type: "visibleRangeChanged", from: 1, to: 2 }]);
  });
});
