import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("lightweight-charts", () => import("./lightweightFake.fixture"));
const fake = await import("./lightweightFake.fixture");
const { ChartCore } = await import("./chartCore");
const { VOLUME_ALPHA, applyChartTheme, chartThemeOptions, readChartPalette, resolveChartTheme, withAlpha } = await import("./chartTheme");

// The light chart tokens as tokens.css sets them under [data-chart-theme="light"].
const LIGHT: Record<string, string> = {
  "--chart-bg": "255 255 255", "--chart-text": "71 85 105", "--chart-axis": "211 217 227", "--chart-grid": "229 233 240",
  "--chart-crosshair": "100 116 139", "--chart-crosshair-label": "15 23 42", "--chart-up": "21 128 61", "--chart-down": "185 28 28",
  "--chart-drawing": "29 78 216", "--chart-rsi": "2 132 199", "--chart-rsi-band": "100 116 139",
  "--chart-ov-1": "3 105 161", "--chart-ov-2": "180 83 9", "--chart-ov-3": "162 28 175", "--chart-ov-4": "109 40 217",
};
const chartRoot = { id: "chart-root" } as unknown as Element;
const read = vi.fn((el: Element) => ({ getPropertyValue: (name: string) => (el === chartRoot ? LIGHT[name] ?? "" : "") }));

beforeEach(() => { fake.made.length = 0; vi.stubGlobal("getComputedStyle", read); });
afterEach(() => { vi.unstubAllGlobals(); read.mockClear(); });

describe("UI2-4b1 chart theme", () => {
  it("App follows the app theme; Light and Dark are the chart's own", () => {
    expect(resolveChartTheme("app", "light")).toBe("light");
    expect(resolveChartTheme("app", "dark")).toBe("dark");
    expect(resolveChartTheme("light", "dark")).toBe("light");
    expect(resolveChartTheme("dark", "light")).toBe("dark");
  });

  it("reads the colours from the chart's root element (its data-chart-theme), not from :root", () => {
    const p = readChartPalette(chartRoot);
    expect(read).toHaveBeenCalledWith(chartRoot);
    expect(p.bg).toBe("rgb(255, 255, 255)");
    expect(p.up).toBe("rgb(21, 128, 61)");
    expect(p.volUp).toBe(`rgba(21, 128, 61, ${VOLUME_ALPHA})`);
    expect(p.ov).toEqual(["rgb(3, 105, 161)", "rgb(180, 83, 9)", "rgb(162, 28, 175)", "rgb(109, 40, 217)"]);
    // Without a stylesheet (or a root): the dark values.
    expect(readChartPalette(null).bg).toBe("rgb(9, 12, 18)");
  });

  it("the trader's candle colours replace up / down, volume follows them at 28 %", () => {
    const p = readChartPalette(chartRoot, { up: "#0d9488", down: "#ea580c" });
    expect([p.up, p.down]).toEqual(["#0d9488", "#ea580c"]);
    expect(p.volDown).toBe("rgba(234, 88, 12, 0.28)");
    expect(withAlpha("rgb(1, 2, 3)", 0.5)).toBe("rgba(1, 2, 3, 0.5)");
  });

  it("applyOptions gets a solid background and an invisible grid by default", () => {
    const p = readChartPalette(chartRoot);
    const chart = { applyOptions: vi.fn() };
    applyChartTheme(chart, {}, p, false, false);
    expect(chart.applyOptions).toHaveBeenCalledWith(chartThemeOptions(p, false));
    const o = chart.applyOptions.mock.calls[0][0];
    expect(o.layout.background).toEqual({ type: "solid", color: "rgb(255, 255, 255)" });
    expect(o.grid.vertLines.visible).toBe(false);
    expect(o.grid.horzLines.visible).toBe(false);
    expect(o.crosshair.horzLine).toEqual({ color: "rgb(100, 116, 139)", labelBackgroundColor: "rgb(15, 23, 42)" });
  });

  it("a theme change is applied in place: no rebuild, the zoom, data and drawings stay", () => {
    const dark = readChartPalette(null);
    const c = new ChartCore({} as HTMLElement, { palette: dark, grid: false, panes: { volume: true, rsi: true }, type: "candles", overlays: ["ema_fast"], zone: "UTC" });
    const chart = fake.made[0];
    const bars = Array.from({ length: 30 }, (_, i) => ({ timestamp: new Date(Date.UTC(2026, 2, 2, 4, i * 5)).toISOString(),
                                                         open: 100, high: 102, low: 99, close: 101 - (i % 2) * 2, volume: 5 }));
    c.setData(bars, "X|5min");
    c.engine.addDrawing("d:1", { kind: "hline", anchors: [{ p: 101 }] });
    chart.range = { from: 12, to: 28 };
    chart.calls.length = 0;

    const light = readChartPalette(chartRoot);
    c.applyTheme(light, false);
    expect(fake.made).toHaveLength(1);                                        // createChart once
    expect(chart.calls).not.toContain("remove");
    expect(chart.calls.some((x) => x.startsWith("addSeries") || x.startsWith("removeSeries"))).toBe(false);
    expect(chart.calls).not.toContain("setVisibleLogicalRange");
    expect(chart.calls).not.toContain("fitContent");
    expect(chart.range).toEqual({ from: 12, to: 28 });
    expect(chart.applied[chart.applied.length - 1]).toMatchObject({ layout: { background: { color: light.bg } } });
    expect(chart.series("Candlestick")[0].options).toMatchObject({ upColor: light.up, downColor: light.down });
    expect(chart.series("Candlestick")[0].rows).toHaveLength(30);           // data kept
    const [vol] = chart.series("Histogram");
    expect((vol.rows[1] as { color: string }).color).toBe(light.volDown);   // per-bar colours repainted
    const rsi = chart.paneList[2].series[0];
    expect(rsi.options.color).toBe(light.rsi);
    expect(rsi.priceLines.every((l) => l.options.color === light.rsiBand)).toBe(true);
    expect(chart.paneList[0].series[3].options.color).toBe(light.ov[0]);
    // The drawing (saved with the default colour) is shown in the theme's drawing colour; the saved one is unchanged.
    const [line] = chart.series("Candlestick")[0].priceLines;
    expect(line.options.color).toBe(light.drawing);
    expect(c.engine.serialize().drawings["d:1"].style).toBeUndefined();
  });
});
