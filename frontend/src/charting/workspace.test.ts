import { afterEach, describe, expect, it, vi } from "vitest";
import { CrosshairLink } from "./crosshairLink";
import type { ChartEngine, EngineEvent } from "./engine";
import { LightweightEngine, type CandleSeriesLike, type ChartLike } from "./lightweight";
import { SHORTCUTS, nextChartType, shortcutFor, type ShortcutKey } from "./shortcuts";
import {
  CHART_TYPES, DEFAULT_SYNC, GRIDS, LayoutConflict, cell, defaultLayout, gridShape, layoutProblem, layoutsApi, linkedPeers, moveActive,
  nextGroup, setActive, setChartType, setGrid, setGroup, setGroupSync, setSymbol, setTimeframe, stepTimeframe, type LayoutV1, type StoredLayout,
} from "./workspace";

/** Four charts: 0 and 1 in group A (symbol + crosshair), 2 in B (interval), 3 alone. */
function four(): LayoutV1 {
  let l = setGrid(defaultLayout("NIFTY 50"), 4);
  l = setSymbol(l, 1, "BANKNIFTY");
  l = setSymbol(l, 2, "RELIANCE");
  l = setSymbol(l, 3, "TCS");
  l = setGroup(l, 0, "A");
  l = setGroup(l, 1, "A");
  l = setGroup(l, 2, "B");
  return setGroupSync(l, "B", { symbol: false, interval: true });
}

describe("layout/1 model (CH5)", () => {
  it("lays out 1, 2, 4, 6 and 8 charts and keeps the backend's rules", () => {
    expect(GRIDS.map((g) => gridShape(g))).toEqual([{ cols: 1, rows: 1 }, { cols: 2, rows: 1 }, { cols: 2, rows: 2 }, { cols: 3, rows: 2 }, { cols: 4, rows: 2 }]);
    for (const g of GRIDS) {
      const l = setGrid(defaultLayout("NIFTY 50", "NSE", "15min"), g);
      expect(l.cells).toHaveLength(g);
      expect(layoutProblem(l)).toBeNull();
      expect(l.cells.every((c) => c.symbol === "NIFTY 50" && c.timeframe === "15min" && c.group === null)).toBe(true);
    }
    expect(layoutProblem({ ...defaultLayout(), grid: 3 })).toMatch(/grid/);
    expect(layoutProblem({ ...defaultLayout(), grid: 2 })).toMatch(/needs 2 cells/);
    expect(layoutProblem({ ...defaultLayout(), cells: [{ ...cell("X"), symbol: "<b>" }] })).toMatch(/symbol/);
    expect(layoutProblem({ ...defaultLayout(), cells: [{ ...cell("X"), group: "E" }] })).toMatch(/link group/);
    expect(layoutProblem({ ...defaultLayout(), cells: [{ ...cell("X"), chart_type: "kagi" }] })).toMatch(/chart type/);
    expect(layoutProblem({ ...defaultLayout(), active: 1 })).toMatch(/active/);
    expect(layoutProblem(null)).toMatch(/not a layout/);
  });

  it("shrinking keeps the first charts and the active one in range; growing copies the active chart, unlinked", () => {
    const l = moveActive(four(), 3);
    expect(l.active).toBe(3);
    const two = setGrid(l, 2);
    expect(two.cells.map((c) => c.symbol)).toEqual(["NIFTY 50", "NIFTY 50"]);     // 0 and 1 are linked by symbol in A
    expect(two.active).toBe(1);
    const six = setGrid(setActive(four(), 2), 6);
    expect(six.cells.slice(4).map((c) => [c.symbol, c.group])).toEqual([["RELIANCE", null], ["RELIANCE", null]]);
  });

  it("a symbol change travels only through symbol links, a timeframe only through interval links", () => {
    let l = four();
    expect(l.cells[1].symbol).toBe("NIFTY 50");                                     // joining A took A's symbol
    l = setSymbol(l, 0, "sbin");
    expect(l.cells.map((c) => c.symbol)).toEqual(["SBIN", "SBIN", "RELIANCE", "TCS"]);
    l = setTimeframe(l, 0, "60min");                                                // A links symbol, not interval
    expect(l.cells.map((c) => c.timeframe)).toEqual(["60min", "5min", "5min", "5min"]);
    l = setGroup(l, 3, "B");                                                        // B links the interval only
    l = setTimeframe(l, 2, "day");
    expect(l.cells.map((c) => c.timeframe)).toEqual(["60min", "5min", "day", "day"]);
    expect(l.cells[3].symbol).toBe("TCS");
    expect(setSymbol(l, 0, "BAD<SYM>")).toBe(l);                                    // the server's symbol rule
    expect(setChartType(l, 0, "renko").cells.map((c) => c.chart_type)).toEqual(["renko", "candles", "candles", "candles"]);
  });

  it("crosshair peers follow each group's own switch; turning a link on aligns the group to its first chart", () => {
    const l = four();
    expect(linkedPeers(l, 0, "crosshair")).toEqual([1]);
    expect(linkedPeers(l, 2, "crosshair")).toEqual([]);                             // B alone in its group so far
    expect(linkedPeers(l, 3, "symbol")).toEqual([]);                                // unlinked chart
    const off = setGroupSync(l, "A", { crosshair: false });
    expect(linkedPeers(off, 0, "crosshair")).toEqual([]);
    let m = setGroup(setSymbol(four(), 3, "INFY"), 3, "B");
    m = setTimeframe(m, 3, "15min");
    expect(m.cells[2].timeframe).toBe("15min");
    m = setGroupSync(m, "B", { symbol: true });
    expect(m.cells[3].symbol).toBe(m.cells[2].symbol);
    expect(DEFAULT_SYNC).toEqual({ symbol: true, interval: false, crosshair: true });
    expect([nextGroup(null), nextGroup("A"), nextGroup("D")]).toEqual(["A", "B", null]);
  });

  it("moves the active chart round the grid and steps timeframes without running off either end", () => {
    const l = four();
    expect(moveActive(l, -1).active).toBe(3);
    expect(moveActive(l, 5).active).toBe(1);
    expect(stepTimeframe("5min", 1)).toBe("15min");
    expect(stepTimeframe("day", 1)).toBe("day");
    expect(stepTimeframe("1min", -1)).toBe("1min");
    expect(setActive(l, 9)).toBe(l);
  });
});

describe("workspace shortcuts", () => {
  const key = (k: Partial<ShortcutKey>): ShortcutKey => ({ key: "", ...k });

  it("maps Alt+keys (by physical key for digits) and ignores keys typed into a field", () => {
    expect(shortcutFor(key({ key: "¡", code: "Digit1", altKey: true }))).toEqual({ type: "grid", grid: 1 });     // Option+1 on a Mac
    expect(shortcutFor(key({ key: "8", code: "Digit8", altKey: true }))).toEqual({ type: "grid", grid: 8 });
    expect(shortcutFor(key({ key: "3", code: "Digit3", altKey: true }))).toBeNull();                          // no 3-chart grid
    expect(shortcutFor(key({ key: "ArrowRight", altKey: true }))).toEqual({ type: "focus", delta: 1 });
    expect(shortcutFor(key({ key: "ArrowDown", altKey: true }))).toEqual({ type: "timeframe", delta: -1 });
    expect(shortcutFor(key({ key: "©", code: "KeyG", altKey: true }))).toEqual({ type: "cycleGroup" });
    expect(shortcutFor(key({ key: "s", altKey: true }))).toEqual({ type: "save" });
    expect(shortcutFor(key({ key: "Enter", altKey: true }))).toEqual({ type: "maximize" });
    expect(shortcutFor(key({ key: "?" }))).toEqual({ type: "help" });
    expect(shortcutFor(key({ key: "z", ctrlKey: true }))).toBeNull();                                         // the drawing tools' undo
    expect(shortcutFor(key({ key: "Delete" }))).toBeNull();
    expect(shortcutFor(key({ key: "s", code: "KeyS", altKey: true, target: { tagName: "INPUT" } }))).toBeNull();
    expect(SHORTCUTS.length).toBeGreaterThanOrEqual(8);
    let t: (typeof CHART_TYPES)[number] = "candles";
    const seen: string[] = [];
    for (let i = 0; i < CHART_TYPES.length; i++) { t = nextChartType(t); seen.push(t); }
    expect(seen).toEqual([...CHART_TYPES.slice(1), "candles"]);                     // Alt+T cycles every type once
  });
});

class FakeEngine {
  listeners = new Set<(e: EngineEvent) => void>();
  shown: (number | null)[] = [];
  constructor(private readonly echo: "sync" | "none" = "sync") {}
  on(l: (e: EngineEvent) => void) { this.listeners.add(l); return () => { this.listeners.delete(l); }; }
  move(time: number | null) { for (const l of this.listeners) l({ type: "crosshair", time, price: null }); }
  setCrosshair(time: number | null) { this.shown.push(time); if (this.echo === "sync") this.move(time); }
}

describe("crosshair link", () => {
  it("moves the linked charts' crosshair to the same time, never loops on echoes, and lets go on unregister", () => {
    const l = four();
    const link = new CrosshairLink((i) => linkedPeers(l, i, "crosshair"));
    const e = [new FakeEngine(), new FakeEngine(), new FakeEngine(), new FakeEngine()];
    e.forEach((x, i) => link.register(i, x as unknown as ChartEngine));
    e[0].move(1000);
    expect(e[1].shown).toEqual([1000]);
    expect(e[0].shown).toEqual([]);                                                 // the echo from 1 did not bounce back
    expect(e[2].shown.concat(e[3].shown)).toEqual([]);
    e[1].move(2000);                                                                // a real move on chart 1 still passes
    expect(e[0].shown).toEqual([2000]);
    e[0].move(null);
    expect(e[1].shown[e[1].shown.length - 1]).toBeNull();                                           // leaving hides the linked crosshair
    link.register(1, null);
    e[0].move(3000);
    expect(e[1].shown[e[1].shown.length - 1]).toBeNull();
    expect(e[1].listeners.size).toBe(0);
    link.dispose();
    expect(e[0].listeners.size).toBe(0);
  });

  it("drops an asynchronous echo once, then follows real moves again", () => {
    const l = four();
    const link = new CrosshairLink((i) => linkedPeers(l, i, "crosshair"));
    const a = new FakeEngine("none"), b = new FakeEngine("none");
    link.register(0, a as unknown as ChartEngine);
    link.register(1, b as unknown as ChartEngine);
    a.move(500);
    b.move(500);                                                                    // b's late echo of what the link set
    expect(a.shown).toEqual([]);
    b.move(600);
    expect(a.shown).toEqual([600]);
  });
});

describe("LightweightEngine.setCrosshair", () => {
  it("puts the crosshair on its own bar at or before the time, at that bar's close, and hides it outside the data", () => {
    const calls: unknown[][] = [];
    let cleared = 0;
    const bars = [{ time: 100, close: 10 }, { time: 400, close: 11 }, { time: 700, close: 12 }];
    const candles = {
      data: () => bars, setData() {}, update() {}, createPriceLine: () => ({}), removePriceLine() {}, priceToCoordinate: () => 0,
      coordinateToPrice: () => 0, subscribeDataChanged() {}, unsubscribeDataChanged() {}, attachPrimitive() {}, detachPrimitive() {},
    } as unknown as CandleSeriesLike;
    const chart = {
      applyOptions() {}, paneSize: () => ({ width: 1, height: 1 }), priceScale: () => ({ width: () => 0 }),
      subscribeCrosshairMove() {}, unsubscribeCrosshairMove() {}, subscribeClick() {}, unsubscribeClick() {},
      timeScale: () => ({ subscribeVisibleTimeRangeChange() {}, unsubscribeVisibleTimeRangeChange() {}, logicalToCoordinate: () => 0, coordinateToLogical: () => 0 }),
      setCrosshairPosition: (...a: unknown[]) => { calls.push(a); },
      clearCrosshairPosition: () => { cleared += 1; },
    } as unknown as ChartLike;
    const engine = new LightweightEngine(chart, candles);
    engine.setCrosshair(550);                                                       // a 1h bar for a 5m time: the bar it is in
    expect(calls[0].slice(0, 2)).toEqual([11, 400]);
    engine.setCrosshair(700);
    expect(calls[1].slice(0, 2)).toEqual([12, 700]);
    engine.setCrosshair(50);
    engine.setCrosshair(null);
    expect(cleared).toBe(2);
    engine.dispose();
    engine.setCrosshair(400);
    expect(calls).toHaveLength(2);
  });
});

describe("layouts API client", () => {
  afterEach(() => vi.unstubAllGlobals());
  const stored: StoredLayout = { id: 5, name: "Morning", layout: defaultLayout(), version: 2, updated_at: null };

  it("updates with its version and turns a stale edit into a LayoutConflict with the current layout", async () => {
    const current = { ...stored, version: 3 };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ detail: { message: "changed", current } }), { status: 409, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const err = await layoutsApi.update(stored, defaultLayout()).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(LayoutConflict);
    expect((err as LayoutConflict).current?.version).toBe(3);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/charts/layouts/5");
    expect(JSON.parse(String(init.body))).toMatchObject({ version: 2, layout: { schema: "layout/1", grid: 1 } });
  });

  it("a duplicate name (409 without a layout) stays an ordinary error; a delete names its version; 404 is a conflict", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "you already have a layout named 'X'" }), { status: 409, headers: { "content-type": "application/json" } })));
    const dup = await layoutsApi.create("X", defaultLayout()).catch((e: unknown) => e);
    expect(dup).not.toBeInstanceOf(LayoutConflict);
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ deleted: 5 }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await layoutsApi.remove(stored);
    expect(String((fetchMock.mock.calls[0] as unknown as [string])[0])).toContain("/charts/layouts/5?version=2");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Layout not found" }), { status: 404, headers: { "content-type": "application/json" } })));
    const gone = await layoutsApi.remove(stored).catch((e: unknown) => e);
    expect(gone).toBeInstanceOf(LayoutConflict);
    expect((gone as LayoutConflict).current).toBeNull();
  });
});
