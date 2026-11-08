import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChartBar } from "./timeframes";

vi.mock("lightweight-charts", () => import("./lightweightFake.fixture"));
const fake = await import("./lightweightFake.fixture");
const { ChartCore } = await import("./chartCore");
const { readChartPalette } = await import("./chartTheme");
const { MAX_VIEWS, SAVE_DELAY_MS, VIEW_MEMORY_KEY, ViewMemory } = await import("./viewMemory");

type Fake = InstanceType<typeof fake.FakeChart>;
const T0 = Date.parse("2026-03-02T03:45:00Z");
const bars = (n: number): ChartBar[] => Array.from({ length: n }, (_, i) => ({
  timestamp: new Date(T0 + i * 300_000).toISOString(), open: 100 + i, high: 102 + i, low: 99 + i, close: 101 + i, volume: 1000 + i,
}));
let store: Map<string, string>;

beforeEach(() => {
  fake.made.length = 0;
  store = new Map();
  vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); },
                                  removeItem: (k: string) => { store.delete(k); } });
  vi.useFakeTimers();
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function core(user: string | null = "7") {
  const c = new ChartCore({} as HTMLElement, { palette: readChartPalette(null), grid: false, panes: { volume: false, rsi: false }, type: "candles",
    overlays: [], zone: "Asia/Kolkata" });
  if (user) c.setViewMemory(new ViewMemory(user));
  return { c, chart: fake.made[fake.made.length - 1] as Fake };
}

describe("P1-4 (2.5): the zoom is remembered per user, symbol, exchange and timeframe", () => {
  it("nothing remembered: the chart fits its bars; a broken or blocked store reads as nothing", () => {
    store.set(VIEW_MEMORY_KEY, "{not json");
    const { c, chart } = core();
    c.setData(bars(100), "NIFTY 50|NSE|5min");
    expect(chart.calls).toContain("fitContent");
    vi.stubGlobal("localStorage", { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } });
    expect(new ViewMemory("7").load("x")).toBeNull();
    expect(() => new ViewMemory("7").save("x", { barSpacing: 9, position: -20, autoScale: false })).not.toThrow();
  });

  it("only his own move is saved, debounced; the next open restores it instead of fitting", () => {
    const first = core();
    first.c.setData(bars(100), "NIFTY 50|NSE|5min");
    first.c.syncView();
    vi.advanceTimersByTime(SAVE_DELAY_MS * 2);
    expect(store.size).toBe(0);                                                   // the chart's own fit is not his view
    first.c.pressPlot(100, 100);                                                  // he drags
    first.c.releasePointer();
    first.chart.timeOptions.barSpacing = 11;
    first.chart.position = -30;
    first.chart.priceScale("right").setAutoScale(false);
    first.c.syncView();
    first.c.syncView();
    expect(store.size).toBe(0);                                                   // not on every frame
    vi.advanceTimersByTime(SAVE_DELAY_MS);
    expect(new ViewMemory("7").load("NIFTY 50|NSE|5min")).toMatchObject({ barSpacing: 11, position: -30, autoScale: false });
    const again = core();
    again.c.setData(bars(120), "NIFTY 50|NSE|5min");
    expect(again.chart.calls).not.toContain("fitContent");
    expect(again.chart.timeOptions.barSpacing).toBe(11);
    expect(again.chart.position).toBe(-30);
    expect(again.chart.priceScale("right").options().autoScale).toBe(false);
    const other = core("8");                                                       // another user on this device
    other.c.setData(bars(120), "NIFTY 50|NSE|5min");
    expect(other.chart.calls).toContain("fitContent");
    const tf = core();                                                             // another timeframe of the same symbol
    tf.c.setData(bars(120), "NIFTY 50|NSE|15min");
    expect(tf.chart.calls).toContain("fitContent");
  });

  it("Reset forgets it; a chart without a user remembers nothing; the list is capped", () => {
    const m = new ViewMemory("7");
    m.save("A|NSE|5min", { barSpacing: 9, position: -5, autoScale: true });
    const { c } = core();
    c.setData(bars(50), "A|NSE|5min");
    c.resetView();
    vi.advanceTimersByTime(SAVE_DELAY_MS * 2);
    expect(m.load("A|NSE|5min")).toBeNull();
    const none = core(null);
    none.c.setData(bars(50), "B|NSE|5min");
    none.c.pressPlot(100, 100);
    none.c.releasePointer();
    none.c.syncView();
    vi.advanceTimersByTime(SAVE_DELAY_MS * 2);
    expect(store.size === 0 || !JSON.parse(store.get(VIEW_MEMORY_KEY) ?? "{}")["7"]?.["B|NSE|5min"]).toBe(true);
    for (let i = 0; i < MAX_VIEWS + 5; i++) m.save(`S${i}|NSE|5min`, { barSpacing: 6, position: 0, autoScale: true }, 1000 + i);
    const kept = Object.keys(JSON.parse(store.get(VIEW_MEMORY_KEY) ?? "{}")["7"]);
    expect(kept).toHaveLength(MAX_VIEWS);
    expect(kept).not.toContain("S0|NSE|5min");                                   // the oldest went
  });
});
