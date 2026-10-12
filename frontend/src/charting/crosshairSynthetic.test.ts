import { describe, expect, it } from "vitest";
import { CrosshairLink } from "./crosshairLink";
import type { EngineEvent } from "./engine";
import { LightweightEngine } from "./lightweight";

// lightweight-charts raises no crosshairMove for a position set with setCrosshairPosition (seen in the CH5-a browser
// render check: the linked charts drew the crosshair but their legends stayed put). The engine now says it itself.
function engine(times: number[]) {
  const set: [number, number][] = [];
  let cleared = 0;
  const chart = { applyOptions() {}, paneSize: () => ({ width: 1, height: 1 }), priceScale: () => ({ width: () => 0 }),
                  subscribeCrosshairMove() {}, unsubscribeCrosshairMove() {}, subscribeClick() {}, unsubscribeClick() {},
                  setCrosshairPosition: (price: number, time: never) => { set.push([price, time as unknown as number]); },
                  clearCrosshairPosition: () => { cleared++; },
                  timeScale: () => ({ subscribeVisibleTimeRangeChange() {}, unsubscribeVisibleTimeRangeChange() {}, logicalToCoordinate: () => 0, coordinateToLogical: () => 0 }) };
  const bars = times.map((time, i) => ({ time, close: 100 + i }));
  const candles = { setData() {}, update() {}, createPriceLine: () => ({}), removePriceLine() {}, priceToCoordinate: () => 0, coordinateToPrice: () => 0,
                    data: () => bars, subscribeDataChanged() {}, unsubscribeDataChanged() {}, attachPrimitive() {}, detachPrimitive() {} };
  const e = new LightweightEngine(chart, candles);
  const events: EngineEvent[] = [];
  e.on((ev) => events.push(ev));
  return { e, events, set, cleared: () => cleared };
}

describe("a crosshair set by a linked chart (CH5-a render check)", () => {
  it("is drawn on the chart's own bar and reported as a synthetic crosshair event, so the legend follows", () => {
    const a = engine([100, 200, 300]);
    a.e.setCrosshair?.(250);
    expect(a.set).toEqual([[101, 200]]);
    expect(a.events).toEqual([{ type: "crosshair", time: 200, price: 101, synthetic: true }]);
    a.e.setCrosshair?.(null);
    expect(a.cleared()).toBe(1);
    expect(a.events[1]).toEqual({ type: "crosshair", time: null, price: null, synthetic: true });
  });

  it("the link does not bounce a synthetic event back: one pointer move, one set per peer", () => {
    const a = engine([100, 200, 300]), b = engine([100, 200, 300]);
    const link = new CrosshairLink((i) => (i === 0 ? [1] : [0]));
    link.register(0, a.e);
    link.register(1, b.e);
    // chart A's own pointer move, as lightweight-charts reports it through the engine
    (a.e as unknown as { emit(e: EngineEvent): void }).emit({ type: "crosshair", time: 300, price: 102 });
    expect(b.set).toEqual([[102, 300]]);
    expect(a.set).toEqual([]);                                                     // no echo back to A
  });
});
