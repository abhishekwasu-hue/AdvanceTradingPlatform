import { describe, expect, it } from "vitest";
import type { DrawingV1 } from "./drawings";
import { extendSegment, fibLevels, positionStats, shapes, timeToLogical, withAlpha, type Converters, type Shape } from "./geometry";
import { paint, type PaintContext } from "./primitives";

// A linear chart: one bar every 15 minutes from T0, 10 px per bar; price 100 at y=500, 10 px per rupee (up is smaller y).
const T0 = Date.parse("2026-03-02T03:45:00Z") / 1000;
const BAR = 900;
const times = Array.from({ length: 40 }, (_, i) => T0 + i * BAR);
const iso = (bar: number) => new Date((T0 + bar * BAR) * 1000).toISOString();
const conv: Converters = {
  x: (t) => { const l = timeToLogical(times, t); return l == null ? null : l * 10; },
  y: (p) => 500 - (p - 100) * 10,
  width: 400, height: 600,
};
const of = <K extends Shape["type"]>(list: Shape[], type: K) => list.filter((s): s is Extract<Shape, { type: K }> => s.type === type);

describe("timeToLogical", () => {
  it("interpolates between bars and extrapolates beyond either end", () => {
    expect(timeToLogical(times, T0 + 3 * BAR)).toBe(3);
    expect(timeToLogical(times, T0 + 3 * BAR + BAR / 3)).toBeCloseTo(3 + 1 / 3);    // a 5m anchor on a 15m chart
    expect(timeToLogical(times, T0 + 45 * BAR)).toBe(45);                            // a future anchor still has a place
    expect(timeToLogical(times, T0 - 2 * BAR)).toBe(-2);
    expect(timeToLogical([], T0)).toBeNull();
    expect(timeToLogical(times, Number.NaN)).toBeNull();
    expect(timeToLogical([T0], T0)).toBe(0);
  });

  it("uses the real (uneven) bar times, so an overnight gap is one step, not hours of empty space", () => {
    const gap = [T0, T0 + BAR, T0 + 18 * 3600, T0 + 18 * 3600 + BAR];
    expect(timeToLogical(gap, T0 + 18 * 3600)).toBe(2);
    expect(timeToLogical(gap, T0 + BAR + (18 * 3600 - BAR) / 2)).toBeCloseTo(1.5);
  });
});

describe("drawing geometry", () => {
  it("rectangle: a filled box between the two anchors, whichever corner came first", () => {
    const rect = of(shapes({ kind: "rectangle", anchors: [{ t: iso(10), p: 102 }, { t: iso(4), p: 99 }], style: { color: "#112233" } }, conv), "rect");
    expect(rect).toEqual([{ type: "rect", x: 40, y: 480, w: 60, h: 30, fill: "#11223326", stroke: "#112233" }]);
  });

  it("vertical line spans the pane at its time; text sits at its anchor", () => {
    expect(of(shapes({ kind: "vline", anchors: [{ t: iso(7) }] }, conv), "segment")[0]).toMatchObject({ x1: 70, y1: 0, x2: 70, y2: 600 });
    expect(shapes({ kind: "text", anchors: [{ t: iso(2), p: 101 }], text: "gap fill" }, conv)).toEqual([
      { type: "text", x: 20, y: 490, text: "gap fill", color: "#2962ff", align: "left", baseline: "middle" },
    ]);
  });

  it("parallel channel: the third anchor sets the offset of a line parallel to the first two", () => {
    const d: DrawingV1 = { kind: "channel", anchors: [{ t: iso(0), p: 100 }, { t: iso(10), p: 105 }, { t: iso(5), p: 99.5 }] };
    const segs = of(shapes(d, conv), "segment");
    expect(segs[0]).toMatchObject({ x1: 0, y1: 500, x2: 100, y2: 450 });
    // base line at bar 5 is 102.5 (y=475); the third anchor is 99.5 (y=505): offset +30 px, same slope
    expect(segs[1]).toMatchObject({ x1: 0, y1: 530, x2: 100, y2: 480 });
    expect(segs[2]).toMatchObject({ y1: 515, y2: 465, dash: "dashed" });                      // the midline
    expect(of(shapes(d, conv), "polygon")).toHaveLength(1);
  });

  it("Fibonacci retracement: 0 at the move's end, 1 at its start, labels from prices; custom levels respected", () => {
    const d: DrawingV1 = { kind: "fib_retracement", anchors: [{ t: iso(2), p: 100 }, { t: iso(12), p: 110 }] };
    expect(fibLevels(d).map((l) => [l.level, +l.price.toFixed(3)])).toEqual([[0, 110], [0.236, 107.64], [0.382, 106.18], [0.5, 105], [0.618, 103.82], [0.786, 102.14], [1, 100]]);
    const out = shapes(d, conv);
    expect(of(out, "segment").map((s) => [s.x1, s.x2])).toEqual(Array(7).fill([20, 120]));
    expect(of(out, "text").map((t) => t.text)).toContain("0.618 (103.82)");
    expect(fibLevels({ ...d, levels: [0.5] })).toEqual([{ level: 0.5, price: 105 }]);
    expect(of(shapes({ ...d, style: { extend: "right" } }, conv), "segment")[0].x2).toBe(400);
  });

  it("Fibonacci extension: the first move projected from the third anchor", () => {
    const d: DrawingV1 = { kind: "fib_extension", anchors: [{ t: iso(0), p: 100 }, { t: iso(5), p: 110 }, { t: iso(8), p: 104 }] };
    expect(fibLevels(d).map((l) => +l.price.toFixed(2))).toEqual([104, 110.18, 114, 120.18, 130.18]);
    expect(of(shapes(d, conv), "segment")[0]).toMatchObject({ x1: 80, x2: 130 });
  });

  it("ray extends to the right edge, trendline extends only when asked, measure labels the change", () => {
    const two = [{ t: iso(0), p: 100 }, { t: iso(10), p: 101 }];
    expect(of(shapes({ kind: "trendline", anchors: two }, conv), "segment")[0]).toMatchObject({ x1: 0, y1: 500, x2: 100, y2: 490 });
    expect(of(shapes({ kind: "ray", anchors: two }, conv), "segment")[0]).toMatchObject({ x2: 400, y2: 460 });
    // drawn right-to-left: starts at bar 10 and runs left through bar 0 to the left edge, never to the right
    expect(of(shapes({ kind: "ray", anchors: [two[1], two[0]] }, conv), "segment")[0]).toMatchObject({ x1: 0, y1: 500, x2: 100, y2: 490 });
    expect(of(shapes({ kind: "ray", anchors: [{ t: iso(5), p: 100 }, { t: iso(5), p: 102 }] }, conv), "segment")[0]).toMatchObject({ x1: 50, y1: 500, x2: 50, y2: 0 });
    expect(extendSegment(10, 0, 20, 10, 100, "both")).toEqual({ x1: 0, y1: -10, x2: 100, y2: 90 });
    const measure = of(shapes({ kind: "measure", anchors: [{ t: iso(0), p: 200 }, { t: iso(4), p: 190 }] }, conv), "text");
    expect(measure[0].text).toBe("-10.00 (-5.00%)");
  });

  it("long and short positions: risk, reward and R:R from prices; a wrong-side stop is reported, not flipped", () => {
    const long: DrawingV1 = { kind: "long_position", anchors: [{ t: iso(20), p: 100 }, { p: 95 }, { p: 112 }] };
    expect(positionStats(long)).toMatchObject({ risk: 5, reward: 12, rr: 2.4, problem: null });
    const out = shapes(long, conv);
    const [profit, loss] = of(out, "rect");
    expect(profit).toMatchObject({ x: 200, y: 380, w: 200, h: 120 });                           // entry 100 -> target 112
    expect(loss).toMatchObject({ x: 200, y: 500, w: 200, h: 50 });                              // entry 100 -> stop 95
    expect(of(out, "text").map((t) => t.text)).toEqual(["Target 112.00 (+12.00%)", "Stop 95.00 (-5.00%)", "R:R 2.40"]);
    const short: DrawingV1 = { kind: "short_position", anchors: [{ t: iso(20), p: 100 }, { p: 106 }, { p: 91 }] };
    expect(positionStats(short)).toMatchObject({ risk: 6, reward: 9, rr: 1.5 });
    const wrong = positionStats({ ...long, anchors: [{ t: iso(20), p: 100 }, { p: 105 }, { p: 112 }] });
    expect(wrong).toMatchObject({ rr: null, problem: "stop must be below the entry" });
    expect(positionStats({ ...short, anchors: [{ t: iso(20), p: 100 }, { p: 106 }, { p: 101 }] })?.problem).toBe("target must be below the entry");
  });

  it("draws nothing when an anchor cannot be placed", () => {
    const none: Converters = { ...conv, x: () => null };
    expect(shapes({ kind: "rectangle", anchors: [{ t: iso(1), p: 1 }, { t: iso(2), p: 2 }] }, none)).toEqual([]);
    expect(shapes({ kind: "channel", anchors: [{ t: iso(1), p: 1 }, { t: iso(2), p: 2 }] }, conv)).toEqual([]);
    expect(shapes({ kind: "text", anchors: [{ t: "not a time", p: 1 }], text: "x" }, conv)).toEqual([]);
    expect(withAlpha("red", 0.5)).toBe("red");
  });
});

describe("painter", () => {
  it("paints each shape with its own style and always restores the context", () => {
    const calls: string[] = [];
    const ctx = new Proxy({} as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : (...args: unknown[]) => { calls.push(`${key}(${args.join(",")})`); }),
      set: (target, key: string, value) => { target[key] = value; calls.push(`${key}=${String(value)}`); return true; },
    }) as unknown as PaintContext;
    paint(ctx, [
      { type: "segment", x1: 0, y1: 1, x2: 2, y2: 3, color: "#000000", width: 2, dash: "dashed" },
      { type: "rect", x: 1, y: 2, w: 3, h: 4, fill: "#ff000026" },
      { type: "polygon", points: [[0, 0], [1, 0]], fill: "#00ff0026" },
      { type: "text", x: 5, y: 6, text: "R:R 2.40", color: "#16a34a", align: "left", baseline: "bottom" },
    ]);
    expect(calls[0]).toBe("save()");
    expect(calls).toContain("setLineDash(6,4)");
    expect(calls).toContain("fillRect(1,2,3,4)");
    expect(calls.some((c) => c.startsWith("fill(") || c === "closePath()")).toBe(false);     // a 2-point polygon is skipped
    expect(calls).toContain("fillText(R:R 2.40,5,6)");
    expect(calls[calls.length - 1]).toBe("restore()");
  });
});
