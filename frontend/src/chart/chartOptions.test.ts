import { beforeEach, describe, expect, it, vi } from "vitest";
import proChartSource from "../components/ProChart.tsx?raw";
import chartCoreSource from "./chartCore.ts?raw";

vi.mock("lightweight-charts", () => import("./lightweightFake.fixture"));
const fake = await import("./lightweightFake.fixture");
const { ChartCore } = await import("./chartCore");
const { readChartPalette } = await import("./chartTheme");
const { DEFAULT_INTERACTION, baseChartOptions, interactionOptions } = await import("./chartOptions");
const { LightweightEngine } = await import("../charting/lightweight");
const { directionMarker } = await import("../charting/markers");

type Fake = InstanceType<typeof fake.FakeChart>;
const palette = readChartPalette(null);
type Gestures = { handleScroll: Record<string, boolean>; handleScale: Record<string, unknown> };

function core() {
  const c = new ChartCore({} as HTMLElement, { palette, grid: false, panes: { volume: false, rsi: false }, type: "candles", overlays: [], zone: "Asia/Kolkata" });
  return { c, chart: fake.made[fake.made.length - 1] as Fake };
}

beforeEach(() => { fake.made.length = 0; });

describe("chart review R1: one options builder", () => {
  it("writes every gesture option as a full object (never the booleans the library expands)", () => {
    const o = interactionOptions(DEFAULT_INTERACTION);
    expect(o.handleScroll).toEqual({ mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false });
    expect(o.handleScale).toEqual({ mouseWheel: true, pinch: true, axisPressedMouseMove: { time: true, price: true },
                                    axisDoubleClickReset: { time: true, price: true } });
  });

  it("suspended turns every scroll and zoom off, the sub-options too", () => {
    const o = interactionOptions({ ...DEFAULT_INTERACTION, vertTouchDrag: true, suspended: true });
    expect(Object.values(o.handleScroll).every((v) => v === false)).toBe(true);
    expect(o.handleScale).toEqual({ mouseWheel: false, pinch: false, axisPressedMouseMove: { time: false, price: false },
                                    axisDoubleClickReset: { time: false, price: false } });
  });

  it("the v2 chart is created with the builder's options (gestures, kinetic scroll, axis basics)", () => {
    const { chart } = core();
    const base = baseChartOptions();
    const o = chart.options as Record<string, Record<string, unknown>>;
    expect(o.handleScroll).toEqual(base.handleScroll);
    expect(o.handleScale).toEqual(base.handleScale);
    expect(o.kineticScroll).toEqual({ touch: true, mouse: false });
    expect(o.rightPriceScale.minimumWidth).toBe(72);
    expect(o.timeScale).toMatchObject({ rightOffset: 4, secondsVisible: false });
  });

  it("ProChart and the core take the gesture options from the builder only (no copy left)", () => {
    expect(proChartSource).toContain("baseChartOptions()");
    expect(chartCoreSource).toContain("baseChartOptions(this.interaction)");
    for (const src of [proChartSource, chartCoreSource]) {
      expect(src).not.toMatch(/handleScroll:|handleScale:|kineticScroll:|axisDoubleClickReset:/);
    }
  });
});

describe("chart review R4: one owner of the gesture options", () => {
  it("after a drawing drag (suspend, then resume) a vertical swipe still scrolls the page and the axis settings stay", () => {
    const { c, chart } = core();
    c.engine.setPanning(false);                                   // what toolsBinding does when a drawing is grabbed
    expect((chart.merged() as Gestures).handleScroll.pressedMouseMove).toBe(false);
    expect(c.interactionState().suspended).toBe(true);
    c.engine.setPanning(true);                                    // ... and when it is let go
    const after = chart.merged() as Gestures;
    expect(after.handleScroll.vertTouchDrag).toBe(false);
    expect(after.handleScroll).toEqual(interactionOptions(DEFAULT_INTERACTION).handleScroll);
    expect(after.handleScale.axisDoubleClickReset).toEqual({ time: true, price: true });
    expect(after.handleScale.axisPressedMouseMove).toEqual({ time: true, price: true });
    for (const applied of chart.applied) {                       // never a bare boolean
      if ("handleScroll" in applied) expect(typeof applied.handleScroll).toBe("object");
      if ("handleScale" in applied) expect(typeof applied.handleScale).toBe("object");
    }
  });

  it("resume gives back the owner's state, whatever it is (a chart that captures touch keeps capturing it)", () => {
    const { c, chart } = core();
    c.setInteraction({ vertTouchDrag: true });
    c.engine.setPanning(false);
    c.engine.setPanning(true);
    expect((chart.merged() as Gestures).handleScroll.vertTouchDrag).toBe(true);
    expect(c.interactionState()).toEqual({ vertTouchDrag: true, suspended: false });
  });

  it("an unchanged state writes nothing", () => {
    const { c, chart } = core();
    const before = chart.applied.length;
    c.setInteraction({ suspended: false });
    expect(chart.applied).toHaveLength(before);
  });

  it("an engine without an owner (ProChart) writes the default gesture options as full objects", () => {
    const chart = new fake.FakeChart({});
    const candles = chart.addSeries({ type: "Candlestick" });
    const engine = new LightweightEngine(chart as never, candles as never);
    engine.setPanning(false);
    engine.setPanning(true);
    expect(chart.applied[chart.applied.length - 1]).toEqual(interactionOptions(DEFAULT_INTERACTION));
    engine.dispose();
    engine.setPanning(false);                                     // after dispose (unmount mid-drag): nothing written
    expect(chart.applied[chart.applied.length - 1]).toEqual(interactionOptions(DEFAULT_INTERACTION));
  });
});

describe("charting/markers (the old CandleChart types)", () => {
  it("directionMarker: below and up for LONG, above and down for SHORT", () => {
    expect(directionMarker("2026-03-02T03:45:00Z", "LONG", "Entry")).toEqual(
      { timestamp: "2026-03-02T03:45:00Z", position: "belowBar", color: "up", shape: "arrowUp", text: "Entry" });
    expect(directionMarker("2026-03-02T03:45:00Z", "SHORT", "Entry")).toMatchObject({ position: "aboveBar", color: "down", shape: "arrowDown" });
  });
});
