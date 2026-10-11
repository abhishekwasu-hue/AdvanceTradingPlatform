import { afterEach, describe, expect, it, vi } from "vitest";
import { ANCHOR_RULES, DrawingConflict, drawingProblem, drawingsApi, type DrawingKind, type DrawingV1, type StoredDrawing } from "./drawings";
import { LightweightEngine, type CandleSeriesLike, type ChartLike } from "./lightweight";
import type { DrawingPrimitive } from "./primitives";
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

describe("drawingsApi status mapping (CH2c review)", () => {
  it("404 (deleted elsewhere) is a conflict with no current drawing, for update, delete and lock", async () => {
    const stored = { id: 7, symbol: "X", exchange: "NSE", kind: "hline" as const, drawing: sample("hline"), version: 4, locked: false, updated_at: null };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Drawing not found" }), { status: 404, headers: { "content-type": "application/json" } })));
    for (const call of [() => drawingsApi.update(stored, sample("hline")), () => drawingsApi.remove(stored), () => drawingsApi.lock(stored, true)]) {
      const err = await call().catch((e: unknown) => e);
      expect(err).toBeInstanceOf(DrawingConflict);
      expect((err as DrawingConflict).current).toBeNull();
    }
  });
});

function fakes() {
  const priceLines: { price: number; title: string }[] = [];
  const primitives: DrawingPrimitive[] = [];
  const handlers: Record<string, ((p: unknown) => void)[]> = { cross: [], click: [], range: [], data: [] };
  const candles: CandleSeriesLike & { bars: { time: unknown }[]; updates: unknown[] } = {
    bars: [], updates: [],
    setData(d) { this.bars = d as { time: unknown }[]; for (const h of handlers.data) h(undefined); },
    update(b) { this.updates.push(b); },
    data() { return this.bars; },
    createPriceLine(o) { const l = { price: o.price, title: o.title }; priceLines.push(l); return l as never; },
    removePriceLine(l) { priceLines.splice(priceLines.indexOf(l as never), 1); },
    priceToCoordinate: (p) => 1000 - p * 5,
    subscribeDataChanged(h) { handlers.data.push(h as (p: unknown) => void); },
    unsubscribeDataChanged(h) { handlers.data = handlers.data.filter((x) => x !== h); },
    attachPrimitive(p) { primitives.push(p); },
    detachPrimitive(p) { primitives.splice(primitives.indexOf(p), 1); },
  };
  const chart: ChartLike = {
    subscribeCrosshairMove(h) { handlers.cross.push(h as (p: unknown) => void); },
    unsubscribeCrosshairMove(h) { handlers.cross = handlers.cross.filter((x) => x !== h); },
    subscribeClick(h) { handlers.click.push(h as (p: unknown) => void); },
    unsubscribeClick(h) { handlers.click = handlers.click.filter((x) => x !== h); },
    timeScale: () => ({
      subscribeVisibleTimeRangeChange(h) { handlers.range.push(h as (p: unknown) => void); },
      unsubscribeVisibleTimeRangeChange(h) { handlers.range = handlers.range.filter((x) => x !== h); },
      logicalToCoordinate: (l: number) => l * 8,
    }),
  };
  return { chart, candles, priceLines, primitives, handlers };
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
    expect(f.primitives.map((p) => p.drawing.kind)).toEqual(["trendline", "fib_retracement"]);   // CH2b: v5 primitives
    for (const kind of Object.keys(ANCHOR_RULES) as DrawingKind[]) expect(engine.supports(kind), kind).toBe(true);
    const layout = engine.serialize();
    expect(Object.keys(layout.drawings).sort()).toEqual(["a", "b", "c"]);
    const g = fakes();
    const other = new LightweightEngine(g.chart, g.candles);
    other.deserialize(layout);
    expect(other.serialize()).toEqual(layout);
    expect(g.priceLines.length).toBe(1);
    expect(g.primitives.length).toBe(2);
  });

  it("places primitives by bar time and price, and re-reads the bar times only when the data changes", () => {
    const f = fakes();
    const engine = new LightweightEngine(f.chart, f.candles);
    engine.addDrawing("r", { kind: "rectangle", anchors: [{ t: T1, p: 100 }, { t: T2, p: 104 }] });
    const prim = f.primitives[0];
    expect(prim.converters(800, 600)).toBeNull();                                        // no bars yet: nothing to place
    const t1 = Date.parse(T1) / 1000;
    const t2 = Date.parse(T2) / 1000;
    engine.setBars([{ time: t1, open: 1, high: 1, low: 1, close: 1 }, { time: t2, open: 1, high: 1, low: 1, close: 1 }]);
    const c = prim.converters(800, 600);
    expect(c && [c.x(t1), c.x(t2), c.x((t1 + t2) / 2), c.y(100), c.width]).toEqual([0, 8, 4, 500, 800]);
    const spy = vi.spyOn(f.candles, "data");
    prim.converters(800, 600);
    prim.converters(800, 600);
    expect(spy).not.toHaveBeenCalled();                                                  // cached between paints
    f.candles.setData([{ time: t2 }, { time: t2 + 900 }]);
    expect(prim.converters(800, 600)?.x(t2)).toBe(0);                                    // new data: re-read once
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("updates and removes drawings and layers without leaking chart objects", () => {
    const f = fakes();
    const engine = new LightweightEngine(f.chart, f.candles);
    const events: EngineEvent[] = [];
    engine.on((e) => events.push(e));
    engine.addDrawing("a", sample("trendline"));
    const first = f.primitives[0];
    engine.updateDrawing("a", { ...sample("trendline"), anchors: [{ t: T1, p: 90 }, { t: T3, p: 95 }] });
    expect(f.primitives).toEqual([first]);                                                // edited in place, not re-attached
    expect(first.drawing.anchors.map((a) => a.p)).toEqual([90, 95]);
    engine.updateDrawing("a", sample("hline"));                                           // kind change: primitive -> price line
    expect(f.primitives).toEqual([]);
    expect(f.priceLines.map((l) => l.price)).toEqual([101.5]);
    engine.removeDrawing("a");
    expect(events[events.length - 1]).toMatchObject({ type: "drawingChanged", id: "a" });
    engine.setLayer("pa", { asOf: T1, lines: [{ price: 100, title: "swing high" }, { price: Number.NaN }] });
    expect(f.priceLines.map((l) => l.title)).toEqual(["swing high"]);
    engine.setLayer("pa", null);
    expect(f.priceLines).toEqual([]);
    engine.addDrawing("h", sample("hline"));
    engine.addDrawing("z", sample("channel"));
    engine.dispose();
    expect(f.priceLines).toEqual([]);
    expect(f.primitives).toEqual([]);
    expect(f.handlers.cross.length + f.handlers.click.length + f.handlers.range.length + f.handlers.data.length).toBe(0);
  });

  it("passes bars through and reports crosshair, click and range events with data values", () => {
    const f = fakes();
    const engine = new LightweightEngine(f.chart, f.candles, (y) => 500 - y);
    const events: EngineEvent[] = [];
    engine.on((e) => events.push(e));
    engine.setBars([{ time: 1, open: 1, high: 2, low: 0.5, close: 1.5 }]);
    engine.appendBar({ time: 2, open: 1.5, high: 2, low: 1, close: 1.8 });
    expect(f.candles.bars.length).toBe(1);
    expect(f.candles.updates.length).toBe(1);
    f.handlers.cross[0]({ time: 2, point: { x: 10, y: 100 } });
    f.handlers.click[0]({ time: undefined });
    f.handlers.range[0]({ from: 1, to: 2 });
    expect(events).toEqual([{ type: "crosshair", time: 2, price: 400 }, { type: "click", time: null, price: null }, { type: "visibleRangeChanged", from: 1, to: 2 }]);
  });
});
