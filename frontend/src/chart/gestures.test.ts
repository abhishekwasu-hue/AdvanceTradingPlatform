import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChartBar } from "./timeframes";

vi.mock("lightweight-charts", () => import("./lightweightFake.fixture"));
const fake = await import("./lightweightFake.fixture");
const { ChartCore } = await import("./chartCore");
const { readChartPalette } = await import("./chartTheme");
const { PRICE_PAN_ANY_PX, PRICE_PAN_START_PX, PriceDrag, latestVisible, shiftWheelPosition } = await import("./gestures");
const { ChartReadout, ChartViewControls, formatVolume } = await import("./ChartViewControls");

type Fake = InstanceType<typeof fake.FakeChart>;
const T0 = Date.parse("2026-03-02T03:45:00Z");
const bars = (n: number): ChartBar[] => Array.from({ length: n }, (_, i) => ({
  timestamp: new Date(T0 + i * 300_000).toISOString(), open: 100 + i, high: 102 + i, low: 99 + i, close: 101 + i, volume: i === 0 ? 0 : 1000 + i,
}));
const palette = readChartPalette(null);

function core() {
  const views: unknown[] = [];
  const readouts: unknown[] = [];
  const c = new ChartCore({} as HTMLElement, { palette, grid: false, panes: { volume: false, rsi: false }, type: "candles", overlays: [],
    zone: "Asia/Kolkata", onView: (v) => views.push(v), onReadout: (r) => readouts.push(r) });
  const chart = fake.made[fake.made.length - 1] as Fake;
  return { c, chart, scale: chart.priceScale("right"), views, readouts };
}

beforeEach(() => { fake.made.length = 0; });

describe("PriceDrag (R3): a vertical plot drag pans the price and turns auto-scale off", () => {
  const scale = () => new fake.FakeChart({}).priceScale("right");

  it("a small or mostly sideways move leaves the price alone (a time pan with a wobble)", () => {
    const s = scale();
    const d = new PriceDrag(s, () => 301);
    expect(d.begin(100, 100)).toBe(true);
    expect(d.move(100, 100 + PRICE_PAN_START_PX - 1)).toBe(false);
    expect(d.move(160, 100 + PRICE_PAN_START_PX + 5)).toBe(false);           // 17 down, 60 across
    expect(d.move(400, 100 + PRICE_PAN_ANY_PX - 1)).toBe(false);             // a long sideways pan with a wobble
    expect(s.options().autoScale).toBe(true);
    expect(d.end()).toBe(false);
  });

  it("a diagonal drag takes the price along once it has moved far enough vertically", () => {
    const s = scale();
    const d = new PriceDrag(s, () => 301);
    d.begin(100, 100);
    expect(d.move(600, 100 + PRICE_PAN_ANY_PX)).toBe(true);
    expect(s.options().autoScale).toBe(false);
  });

  it("past the threshold: auto off, the range follows the pointer (down = prices up the axis), from the press point", () => {
    const s = scale();                                                       // range 90..110 over 301 px: 1/15 per px
    const d = new PriceDrag(s, () => 301);
    d.begin(100, 100);
    expect(d.move(100, 130)).toBe(true);
    expect(s.options().autoScale).toBe(false);
    expect(s.getVisibleRange()).toEqual({ from: 92, to: 112 });
    expect(d.move(100, 70)).toBe(true);                                      // back up past the start: below the snapshot
    expect(s.getVisibleRange()).toEqual({ from: 88, to: 108 });
    expect(d.end()).toBe(true);
  });

  it("does nothing when auto-scale is already off (the library pans the price itself) or in percentage mode", () => {
    const s = scale();
    s.setAutoScale(false);
    expect(new PriceDrag(s, () => 301).begin(0, 0)).toBe(false);
    const p = scale();
    p.applyOptions({ mode: 2 });
    const d = new PriceDrag(p, () => 301);
    expect(d.begin(0, 0)).toBe(false);
    expect(d.move(0, 100)).toBe(false);
    expect(p.options().autoScale).toBe(true);
  });
});

describe("latestVisible and Shift + wheel", () => {
  it("the latest bar is visible while the range reaches its index", () => {
    expect(latestVisible({ from: 50, to: 103 }, 100)).toBe(true);
    expect(latestVisible({ from: 50, to: 99 }, 100)).toBe(true);
    expect(latestVisible({ from: 40, to: 98.5 }, 100)).toBe(false);
    expect(latestVisible(null, 100)).toBe(true);
    expect(latestVisible({ from: 0, to: 5 }, 0)).toBe(true);
  });

  it("Shift + a vertical wheel pans by its pixels in bars; a sideways wheel, no Shift or no move is the library's", () => {
    expect(shiftWheelPosition({ shiftKey: true, deltaX: 0, deltaY: 60 }, -10, 6)).toBe(0);
    expect(shiftWheelPosition({ shiftKey: true, deltaX: 0, deltaY: -3, deltaMode: 1 }, 4, 8)).toBe(-2);
    expect(shiftWheelPosition({ shiftKey: true, deltaX: 60, deltaY: 0 }, 0, 6)).toBeNull();
    expect(shiftWheelPosition({ shiftKey: false, deltaX: 0, deltaY: 60 }, 0, 6)).toBeNull();
    expect(shiftWheelPosition({ shiftKey: true, deltaX: 0, deltaY: 0 }, 0, 6)).toBeNull();
  });
});

describe("ChartCore gesture state (PR 2)", () => {
  it("long press: the crosshair ends when the finger lifts", () => {
    const { chart } = core();
    expect((chart.options as { trackingMode: unknown }).trackingMode).toEqual({ exitMode: fake.TrackingModeExitMode.OnTouchEnd });
  });

  it("a vertical drag on the price plot turns Auto off and tells the page once", () => {
    const { c, scale, views } = core();
    c.setData(bars(100), "A");
    expect(c.pressPlot(100, 100)).toBe(true);
    c.movePointer(100, 140);
    c.movePointer(100, 160);
    c.releasePointer();
    expect(scale.options().autoScale).toBe(false);
    expect(c.viewState().autoScale).toBe(false);
    expect(views.filter((v) => (v as { autoScale: boolean }).autoScale === false)).toHaveLength(1);
  });

  it("presses that start no price drag: the price axis, below the price pane, a finger on a page chart, a drawing drag", () => {
    const { c, chart } = core();
    c.setData(bars(100), "A");
    expect(c.pressPlot(405, 100)).toBe(false);                               // the price axis (the plot is 400 wide)
    expect(c.pressPlot(100, 305)).toBe(false);                               // under the price pane (300 high)
    expect(c.pressPlot(100, 100, "touch")).toBe(false);                      // a vertical swipe scrolls the page
    c.setInteraction({ vertTouchDrag: true });
    expect(c.pressPlot(100, 100, "touch", 7)).toBe(true);                     // full screen: the chart takes the finger
    c.releasePointer();
    c.engine.setPanning(false);                                              // a drawing is being dragged
    expect(c.pressPlot(100, 100)).toBe(false);
    c.engine.setPanning(true);
    expect(c.pressPlot(100, 100)).toBe(true);
    c.engine.setPanning(false);                                              // ... or a drawing drag starts meanwhile
    c.movePointer(100, 200);
    expect(chart.priceScale("right").options().autoScale).toBe(true);
  });

  it("a second finger (a pinch) ends the price drag", () => {
    const { c, scale } = core();
    c.setData(bars(100), "A");
    c.setInteraction({ vertTouchDrag: true });
    c.pressPlot(100, 100, "touch", 1);
    expect(c.pressPlot(150, 100, "touch", 2)).toBe(false);
    c.movePointer(100, 200, 1);
    expect(scale.options().autoScale).toBe(true);
  });

  it("Auto toggles; an axis drag that turned it off is read back on the next sync", () => {
    const { c, scale } = core();
    c.setAutoScale(false);
    expect(c.viewState().autoScale).toBe(false);
    c.setAutoScale(true);
    expect(c.viewState().autoScale).toBe(true);
    scale.applyOptions({ autoScale: false });                               // the library's own price-axis drag
    c.syncView();
    expect(c.viewState().autoScale).toBe(false);
  });

  it("Reset restores auto price, the default zoom and the latest bar", () => {
    const { c, chart, scale } = core();
    c.setData(bars(100), "A");
    scale.setVisibleRange({ from: 50, to: 60 });
    chart.scrollTo({ from: 10, to: 40 });                                    // far back in time
    expect(c.viewState()).toEqual({ autoScale: false, latestVisible: false });
    chart.calls.length = 0;
    c.resetView();
    expect(scale.options().autoScale).toBe(true);
    expect(chart.calls).toEqual(expect.arrayContaining(["resetTimeScale", "scrollToPosition"]));
    expect(chart.position).toBe(4);                                          // the right offset: the latest bar in view
  });

  it("Latest shows only while the latest bar is off screen; it scrolls back to it", () => {
    const { c, chart, views } = core();
    c.setData(bars(100), "A");
    chart.scrollTo({ from: 60, to: 103 });
    expect(c.viewState().latestVisible).toBe(true);
    chart.scrollTo({ from: 20, to: 70 });
    expect(c.viewState().latestVisible).toBe(false);
    expect(views[views.length - 1]).toEqual({ autoScale: true, latestVisible: false });
    chart.position = -30;
    c.scrollToLatest();
    expect(chart.position).toBe(4);
    chart.scrollTo({ from: 60, to: 103 });
    expect(c.viewState().latestVisible).toBe(true);
  });

  it("Shift + wheel scrolls time through the core; a plain wheel is left to the library", () => {
    const { c, chart } = core();
    c.setData(bars(100), "A");
    chart.position = -20;
    expect(c.wheel({ shiftKey: true, deltaX: 0, deltaY: 60 })).toBe(true);
    expect(chart.position).toBe(-10);                                        // 60 px at 6 px a bar
    expect(c.wheel({ shiftKey: false, deltaX: 0, deltaY: 60 })).toBe(false);
  });

  it("the readout is the bar under the crosshair as it came; leaving the chart clears it", () => {
    const { c, chart, readouts } = core();
    const data = bars(10);
    c.setData(data, "A");
    const t = Math.floor(Date.parse(data[3].timestamp) / 1000);
    chart.moveCrosshair({ time: t, point: { x: 10, y: 10 } });
    chart.moveCrosshair({ time: t, point: { x: 12, y: 30 } });               // same bar: no new readout
    expect(readouts).toEqual([{ time: t, open: 103, high: 105, low: 102, close: 104, volume: 1003 }]);
    chart.moveCrosshair({ time: Math.floor(T0 / 1000), point: { x: 1, y: 1 } });
    expect(readouts[1]).toMatchObject({ volume: null });                     // no volume (an index): no volume shown
    chart.moveCrosshair({});
    expect(readouts[2]).toBeNull();
    expect(c.formatPrice(101.5)).toBe("101.50");
  });

  it("after remove nothing is told any more", () => {
    const { c, views } = core();
    c.remove();
    c.setAutoScale(false);
    expect(views).toHaveLength(0);
  });
});

describe("the floating controls", () => {
  const html = (latestVisible: boolean, autoScale = true) => renderToStaticMarkup(ChartViewControls({
    view: { autoScale, latestVisible }, insets: { right: 72, bottom: 30 }, onAuto() {}, onReset() {}, onLatest() {},
  }));

  it("Auto (pressed while on, with its plain tooltip) and Reset; Latest only while the latest bar is off screen", () => {
    const on = html(true);
    expect(on).toContain('title="Fit prices to the screen"');
    expect(on).toMatch(/aria-pressed="true"[^>]*>.*Auto/);
    expect(on).toContain(">Reset<");
    expect(on).not.toContain("Latest");
    expect(html(false, false)).toMatch(/aria-pressed="false"/);
    expect(html(false)).toContain(">Latest<");
  });

  it("sits at the price pane's corner, 44 px targets on a phone (36 from md)", () => {
    const out = html(false);
    expect(out).toContain("right:80px;bottom:38px");
    expect(out.match(/min-h-11 min-w-11/g)).toHaveLength(3);
    expect(out.match(/md:min-h-9/g)).toHaveLength(3);
  });

  it("the readout names each value in words", () => {
    const out = renderToStaticMarkup(ChartReadout({ readout: { time: 1, open: 1, high: 2, low: 0.5, close: 1.5, volume: 250000 }, format: (p) => p.toFixed(2) }));
    for (const w of ["Open", "High", "Low", "Close", "Volume", "2.00", "2.5L"]) expect(out).toContain(w);
  });

  it("volume in K, L and Cr", () => {
    expect([formatVolume(950), formatVolume(61_100), formatVolume(2_50_000), formatVolume(3_45_00_000)]).toEqual(["950", "61.1K", "2.5L", "3.5Cr"]);
  });
});
