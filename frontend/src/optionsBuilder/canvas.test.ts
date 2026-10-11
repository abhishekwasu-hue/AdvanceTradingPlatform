import { describe, expect, it } from "vitest";
import {
  basketUnderlying, builderAreas, contractLabel, hitRadius, linear, maxLossY, seriesAt, strikeAtPointer, strikeBounds, strikeForKey,
  type Evaluation,
} from "./model";

/** Z5 / P1-c: the payoff canvas's rendering helpers and the strike-drag math (spec §3.4 "drag-strike updates
 * deterministic"). The numbers drawn come from the engine; these only place them. */

const xs = Array.from({ length: 81 }, (_, i) => 20000 + i * 50);      // 20,000 .. 24,000
const VIEW = 800;
const sx = linear([20000, 24000], [64, 784]);                          // the canvas's margins: 64 left, 16 right

describe("strike drag", () => {
  const bounds = strikeBounds(xs, 50);
  it("takes the chart's own range on the grid", () => {
    expect(bounds).toEqual([20000, 24000]);
    expect(strikeBounds([33.3, 140.2], 50)).toEqual([50, 150]);        // never below one step
  });
  it("maps a pointer to the strike under it, snapped to the grid", () => {
    // the chart drawn 400 px wide from x = 100: the middle of the plot (viewBox 424) is at 100 + 424 / 2
    expect(strikeAtPointer(100 + 212, 100, 400, VIEW, sx, 50, bounds)).toBe(22000);
    expect(strikeAtPointer(100 + 212 + 1, 100, 400, VIEW, sx, 50, bounds)).toBe(22000);   // within half a step
    expect(strikeAtPointer(100 + 212 + 4, 100, 400, VIEW, sx, 50, bounds)).toBe(22050);
  });
  it("holds the strike at the edge when the pointer leaves the chart", () => {
    expect(strikeAtPointer(-500, 100, 400, VIEW, sx, 50, bounds)).toBe(20000);
    expect(strikeAtPointer(5000, 100, 400, VIEW, sx, 50, bounds)).toBe(24000);
  });
  it("is deterministic: the same pointer gives the same strike whatever came before", () => {
    const path = [130, 250, 312, 470, 312, 250, 130];
    const strikes = path.map((x) => strikeAtPointer(x, 100, 400, VIEW, sx, 50, bounds));
    expect(strikes[2]).toBe(strikes[4]);
    expect(strikes[1]).toBe(strikes[5]);
    expect(strikes[0]).toBe(strikes[6]);
    expect(strikes.every((k) => k != null && k % 50 === 0)).toBe(true);
  });
  it("does not move on a chart that has not been laid out", () => {
    expect(strikeAtPointer(300, 100, 0, VIEW, sx, 50, bounds)).toBeNull();
    expect(strikeAtPointer(Number.NaN, 100, 400, VIEW, sx, 50, bounds)).toBeNull();
  });
});

describe("strike keys (the drag without a pointer)", () => {
  const bounds: [number, number] = [21000, 23000];
  it("moves one step with the arrows and five with Page Up / Down", () => {
    expect(strikeForKey("ArrowRight", 22000, 50, bounds)).toBe(22050);
    expect(strikeForKey("ArrowUp", 22000, 50, bounds)).toBe(22050);
    expect(strikeForKey("ArrowLeft", 22000, 50, bounds)).toBe(21950);
    expect(strikeForKey("PageUp", 22000, 50, bounds)).toBe(22250);
    expect(strikeForKey("PageDown", 22000, 50, bounds)).toBe(21750);
  });
  it("jumps to the ends and stays inside them", () => {
    expect(strikeForKey("Home", 22000, 50, bounds)).toBe(21000);
    expect(strikeForKey("End", 22000, 50, bounds)).toBe(23000);
    expect(strikeForKey("PageUp", 22900, 50, bounds)).toBe(23000);
    expect(strikeForKey("ArrowLeft", 21000, 50, bounds)).toBe(21000);
  });
  it("lands an off-grid strike back on the grid and ignores other keys", () => {
    expect(strikeForKey("ArrowRight", 22010, 50, bounds)).toBe(22050);
    expect(strikeForKey("a", 22000, 50, bounds)).toBeNull();
    expect(strikeForKey("Enter", 22000, 50, bounds)).toBeNull();
  });
});

describe("payoff rendering helpers", () => {
  it("reads a curve between grid points, and its ends outside the grid", () => {
    const ys = xs.map((x) => (x - 22000) / 10);
    expect(seriesAt(xs, ys, 22025)).toBeCloseTo(2.5);
    expect(seriesAt(xs, ys, 19000)).toBe(ys[0]);
    expect(seriesAt(xs, ys, 30000)).toBe(ys[ys.length - 1]);               // past the last point: the last value
    expect(seriesAt([1, 1, 2], [5, 7, 9], 1)).toBe(5);
    expect(seriesAt([1, 1, 2], [5, 7, 9], 1.5)).toBe(8);                     // a repeated price takes the later point
    expect(seriesAt(xs, null, 22000)).toBeNull();
  });
  it("draws the max-loss line only for a finite loss inside the chart", () => {
    const sy = linear([-10000, 5000], [316, 16]);
    const ext = (over: Partial<NonNullable<Evaluation["extremes"]>>): Evaluation["extremes"] =>
      ({ max_profit: 3000, max_loss: -6000, unbounded_profit: false, unbounded_loss: false, ...over });
    expect(maxLossY(ext({}), sy)).toBeCloseTo(sy(-6000));
    expect(maxLossY(ext({ max_loss: null, unbounded_loss: true }), sy)).toBeNull();
    expect(maxLossY(ext({ max_loss: 200 }), sy)).toBeNull();                // no loss anywhere
    expect(maxLossY(ext({ max_loss: -50000 }), sy)).toBeNull();             // off the chart
    expect(maxLossY(null, sy)).toBeNull();                                  // a calendar: no single expiry payoff
  });
  it("gives every strike handle a 44 px hit area however small the chart is drawn", () => {
    expect(hitRadius(360, VIEW) * (360 / VIEW) * 2).toBeCloseTo(44);       // a phone: 44 px across on screen
    expect(hitRadius(1600, VIEW)).toBe(11);                                 // a wide screen: 22 px radius = 11 units
    expect(hitRadius(4000, VIEW)).toBe(10);                                 // never smaller than the drawn handle
    expect(hitRadius(0, VIEW)).toBe(10);
  });
});

describe("layout and the basket", () => {
  it("puts the payoff first and the legs below it on a phone once there is a strategy", () => {
    const phone = builderAreas(false, true).split(" ").map((a) => a.replace(/"/g, ""));
    expect(phone.indexOf("pay")).toBe(0);
    expect(phone.indexOf("legs")).toBe(1);
    expect(builderAreas(false, false).startsWith('"inst" "tmpl"')).toBe(true);   // nothing to draw yet: inputs first
    expect(builderAreas(true, true)).toContain('"pay side" "legs side"');      // desktop: legs left, numbers right
  });
  it("names the underlying the way the server's keys do", () => {
    expect(basketUnderlying(" nifty ")).toBe("NIFTY");
    expect(basketUnderlying("M&M")).toBe("M&M");
    expect(basketUnderlying("NIFTY 50")).toBeNull();
    expect(basketUnderlying("")).toBeNull();
  });
  it("says each basket row in plain words", () => {
    expect(contractLabel({ side: "SELL", option_type: "CE", strike: 22200, expiry: "2026-03-16" })).toBe("SELL 22,200 CE 16 Mar");
    expect(contractLabel({ side: "BUY", option_type: "FUT", strike: null, expiry: "2026-03-26" })).toBe("BUY FUT 26 Mar");
  });
});
