import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChartBar } from "./timeframes";
import chartV2Source from "./ChartV2.tsx?raw";

vi.mock("lightweight-charts", () => import("./lightweightFake.fixture"));
const fake = await import("./lightweightFake.fixture");
const { ChartCore, snapMarkers } = await import("./chartCore");
const { readChartPalette } = await import("./chartTheme");
const { default: ChartV2 } = await import("./ChartV2");
const { DEFAULT_CHART_PREFS } = await import("./useChartPrefs");


type Fake = InstanceType<typeof fake.FakeChart>;
const T0 = Date.parse("2026-03-02T03:45:00Z");
const bars = (n: number, from = 0): ChartBar[] => Array.from({ length: n }, (_, i) => {
  const c = 100 + Math.sin(i + from) * 3;
  return { timestamp: new Date(T0 + (i + from) * 300_000).toISOString(), open: c - 0.5, high: c + 1, low: c - 1, close: c, volume: 1000 + i };
});
const palette = readChartPalette(null);

function core(opts: Partial<ConstructorParameters<typeof ChartCore>[1]> = {}) {
  const c = new ChartCore({} as HTMLElement, { palette, grid: false, panes: { volume: true, rsi: true }, type: "candles", overlays: [], zone: "Asia/Kolkata", ...opts });
  return { c, chart: fake.made[fake.made.length - 1] as Fake };
}

beforeEach(() => { fake.made.length = 0; });

describe("UI2-4b1 ChartV2: one chart with v5 native panes", () => {
  it("volume and RSI panes are on by default, under the price pane, on one chart", () => {
    const { chart } = core();
    expect(fake.made).toHaveLength(1);
    expect(chart.layout()).toEqual([["Candlestick", "Bar", "Line", "Line", "Line", "Line", "Line"], ["Histogram"], ["Line"]]);
    const [, vol, rsi] = chart.panes();
    expect([vol.stretch, rsi.stretch]).toEqual([0.25, 0.25]);
    const rsiSeries = rsi.getSeries()[0];
    expect(rsiSeries.priceLines.map((l) => l.options.price)).toEqual([70, 50, 30]);
  });

  it("a solid background, no grid by default, a themed crosshair; the attribution logo stays", () => {
    const { chart } = core();
    const o = chart.options as { layout: Record<string, unknown>; grid: Record<string, { visible: boolean }>; crosshair: Record<string, Record<string, string>> };
    expect(o.layout.background).toEqual({ type: "solid", color: palette.bg });
    expect(o.layout.attributionLogo).toBe(true);
    expect(o.grid.vertLines.visible).toBe(false);
    expect(o.grid.horzLines.visible).toBe(false);
    expect(o.crosshair.vertLine).toEqual({ color: palette.crosshair, labelBackgroundColor: palette.crosshairLabel });
  });

  it("the grid toggle shows the grid in place (no rebuild)", () => {
    const { c, chart } = core();
    c.applyTheme(palette, true);
    const last = chart.applied[chart.applied.length - 1] as { grid: Record<string, { visible: boolean; color: string }> };
    expect(last.grid.vertLines).toEqual({ visible: true, color: palette.grid });
    expect(last.grid.horzLines.visible).toBe(true);
    expect(fake.made).toHaveLength(1);
    expect(chart.removed).toBe(false);
  });

  it("the bars type draws OHLC bars; the candles stay (transparent) for drawings and layers", () => {
    const { c, chart } = core();
    c.setData(bars(30), "A");
    c.setType("bars");
    const [candles] = chart.series("Candlestick");
    const [bar] = chart.series("Bar");
    expect(bar.rows).toHaveLength(30);
    expect(candles.rows).toHaveLength(30);
    expect(candles.options).toMatchObject({ upColor: "rgba(0, 0, 0, 0)", lastValueVisible: false });
    c.setType("candles");
    expect(bar.rows).toHaveLength(0);
    expect(candles.options).toMatchObject({ upColor: palette.up, lastValueVisible: true });
  });

  it("Heikin Ashi and line come from the shared chart types", () => {
    const { c, chart } = core({ type: "heikin_ashi" });
    const data = bars(5);
    c.setData(data, "A");
    const [candles] = chart.series("Candlestick");
    expect((candles.rows[0] as { open: number }).open).toBeCloseTo((data[0].open + data[0].close) / 2);
    c.setType("line");
    const line = chart.paneList[0].series[2];
    expect((line.rows as { value: number }[]).map((r) => r.value)).toEqual(data.map((b) => b.close));
  });

  it("removing a pane removes it from the chart; RSI moves up; turning volume back puts it above RSI again", () => {
    const { c, chart } = core();
    c.setData(bars(40), "A");
    c.setPanes({ volume: false, rsi: true });
    expect(chart.layout()).toHaveLength(2);
    expect(chart.layout()[1]).toEqual(["Line"]);
    expect(chart.series("Histogram")).toHaveLength(0);
    c.setPanes({ volume: true, rsi: true });
    expect(chart.layout().slice(1)).toEqual([["Histogram"], ["Line"]]);
    expect(chart.series("Histogram")[0].rows).toHaveLength(40);           // its data is sent again
    c.setPanes({ volume: false, rsi: false });
    expect(chart.layout()).toHaveLength(1);
    expect(fake.made).toHaveLength(1);
  });

  it("a study pane stays under volume and RSI, and later study lines share it", () => {
    const { c, chart } = core();
    c.engine.setStudy("a", { pane: "own", data: [], color: "red", title: "A" });
    c.engine.setStudy("b", { pane: "own", data: [], color: "blue", title: "B" });
    expect(chart.layout().slice(1)).toEqual([["Histogram"], ["Line"], ["Line", "Line"]]);
    c.setPanes({ volume: false, rsi: true });
    c.setPanes({ volume: true, rsi: true });
    expect(chart.layout().slice(1)).toEqual([["Histogram"], ["Line"], ["Line", "Line"]]);
  });

  it("overlays: EMA fast / slow, VWAP and SMA take the overlay colours, off by default", () => {
    const { c, chart } = core();
    c.setData(bars(60), "A");
    const lines = chart.paneList[0].series.slice(3);
    expect(lines.map((s) => s.options.color)).toEqual(palette.ov);
    expect(lines.every((s) => s.rows.length === 0)).toBe(true);
    c.setOverlays(["ema_fast", "sma"]);
    expect(lines.map((s) => s.rows.length)).toEqual([60, 0, 0, 60]);
  });

  it("new data for the same chart keeps the view (shifted by older bars); a new symbol or timeframe fits all", () => {
    const { c, chart } = core();
    c.setData(bars(50, 20), "NIFTY|5min");
    expect(chart.calls.filter((x) => x === "fitContent")).toHaveLength(1);
    chart.range = { from: 10, to: 40 };
    c.setData(bars(70, 0), "NIFTY|5min");                                   // 20 older bars loaded
    expect(chart.range).toEqual({ from: 30, to: 60 });
    c.setData(bars(30), "BANKNIFTY|5min");
    expect(chart.calls.filter((x) => x === "fitContent")).toHaveLength(2);
  });

  it("changing the chart type redraws the same bars in place: the zoom stays (the data key has no chart type)", () => {
    const { c, chart } = core();
    c.setData(bars(50, 20), "NIFTY|NSE|5min");
    chart.range = { from: 10, to: 40 };
    for (const type of ["bars", "heikin_ashi", "line", "candles"] as const) c.setType(type);
    expect(chart.range).toEqual({ from: 10, to: 40 });
    expect(chart.calls.filter((x) => x === "fitContent")).toHaveLength(1);
    const keyLine = chartV2Source.split("\n").find((l) => l.includes("core?.setData("));
    expect(keyLine).toBeDefined();
    expect(keyLine).not.toContain("chartType");
  });

  it("asks for older history when scrolled to within 10 bars of the oldest", () => {
    const onLoadOlder = vi.fn();
    const { chart } = core({ onLoadOlder });
    chart.scrollTo({ from: 40, to: 80 });
    expect(onLoadOlder).not.toHaveBeenCalled();
    chart.scrollTo({ from: 3, to: 43 });
    expect(onLoadOlder).toHaveBeenCalledTimes(1);
  });

  it("the drawing tools see the price pane's height while panes sit under it (pointer below it: not a price)", () => {
    const { c } = core();
    expect(c.engine.pricePaneHeight()).toBe(300);
    c.setPanes({ volume: false, rsi: false });
    expect(c.engine.pricePaneHeight()).toBeNull();
  });

  it("remove() takes the chart down once", () => {
    const { c, chart } = core();
    c.remove();
    expect(chart.removed).toBe(true);
  });
});

describe("Chart review PR 3: series markers on the v2 chart", () => {
  const iso = (i: number, plus = 0) => new Date(T0 + i * 300_000 + plus).toISOString();

  it("snapMarkers: each marker goes to the bar at or before its time; one before the first bar is left out; sorted", () => {
    const times = bars(5).map((b) => Math.floor(Date.parse(b.timestamp) / 1000));
    const out = snapMarkers([
      { timestamp: iso(3, 120_000), position: "aboveBar", color: "down", shape: "circle", text: "Exit" },   // inside bar 3
      { timestamp: iso(1), position: "belowBar", color: "up", shape: "arrowUp", text: "Entry" },
      { timestamp: iso(-1), position: "belowBar", color: "#123456", shape: "square", text: "Too early" },
      { timestamp: "not a time", position: "belowBar", color: "fg", shape: "square", text: "Bad" },
    ], times, palette);
    expect(out.map((m) => [m.time, m.text, m.color])).toEqual([[times[1], "Entry", palette.up], [times[3], "Exit", palette.down]]);
    expect(snapMarkers([{ timestamp: iso(9), position: "aboveBar", color: "fg", shape: "circle", text: "Late" }], times, palette)[0])
      .toMatchObject({ time: times[4], color: palette.text });                         // after the last bar: the last bar
  });

  it("markers sit on the candle series and show for every chart type; new bars re-snap them; no plugin without markers", () => {
    fake.markerPlugins.length = 0;
    const { c, chart } = core();
    c.setData(bars(10), "A");
    c.setMarkers([]);
    expect(fake.markerPlugins).toHaveLength(0);
    c.setMarkers([{ timestamp: iso(2), position: "belowBar", color: "up", shape: "arrowUp", text: "Entry" }]);
    const [plugin] = fake.markerPlugins;
    expect(plugin.series).toBe(chart.series("Candlestick")[0]);
    expect(plugin.list).toHaveLength(1);
    for (const type of ["bars", "line", "heikin_ashi"] as const) { c.setType(type); expect(plugin.list).toHaveLength(1); }
    c.setData(bars(10, 5), "A");                                                       // the bar of the marker scrolled out
    expect(plugin.list).toHaveLength(0);
    c.setMarkers([]);
    expect(plugin.list).toHaveLength(0);
    c.remove();
    expect(plugin.detached).toBe(true);
  });

  it("a meaning colour follows a new palette", () => {
    fake.markerPlugins.length = 0;
    const { c } = core();
    c.setData(bars(10), "A");
    c.setMarkers([{ timestamp: iso(2), position: "belowBar", color: "up", shape: "arrowUp", text: "Entry" }]);
    c.applyTheme({ ...palette, up: "#0d9488" }, false);
    expect((fake.markerPlugins[0].list[0] as { color: string }).color).toBe("#0d9488");
  });

  it("ChartV2 passes its markers to the core", () => {
    expect(chartV2Source).toContain("core?.setMarkers(markers)");
  });
});

describe("UI2-4b1 ChartV2 component", () => {
  it("carries the resolved chart theme on its root, so the scoped chart tokens apply", () => {
    const light = renderToStaticMarkup(<ChartV2 candles={bars(3)} timeframe="5min" prefs={{ ...DEFAULT_CHART_PREFS, theme: "light" }} />);
    expect(light).toContain('data-chart-theme="light"');
    expect(light).toContain('data-chart-grid="off"');
    expect(light).toContain("background-color:rgb(var(--chart-bg))");
    const dark = renderToStaticMarkup(<ChartV2 candles={[]} timeframe="day" prefs={{ ...DEFAULT_CHART_PREFS, theme: "dark", grid: true }} />);
    expect(dark).toContain('data-chart-theme="dark"');
    expect(dark).toContain('data-chart-grid="on"');
    expect(dark).toContain("Prices for this timeframe load as they arrive.");
    // "App" follows the app theme (dark by default without the v2 hint).
    expect(renderToStaticMarkup(<ChartV2 candles={[]} timeframe="day" />)).toContain('data-chart-theme="dark"');
  });
});
