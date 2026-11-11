/**
 * M98-P1 Q3: the candle-box names of a tapped zone (Leg-in / Base / Leg-out, `markShapes`, layer `sd_zones_focus`) and
 * the zone labels (`placeBandLabels`) share one collision check, so they never overlap - on one layer and across the
 * layers of a pane (the engine hands each layer the names the others paint, `Converters.labelBlocks`).
 */
import { describe, expect, it, vi } from "vitest";
import type { LayerBand, LayerMark } from "./engine";
import { bandShapes, layerShapes, markLabelRects, markShapes, overlaps, type BandLabelStyle, type Converters, type Rect, type Shape } from "./geometry";
import { LightweightEngine } from "./lightweight";
import type { LayerPrimitive } from "./primitives";

const style: BandLabelStyle = { fontPx: 12, chip: "#ffffff", ink: "#111827" };
const H = 300;
const y = (p: number) => H - (p - 100) * 4;                       // price 100 at the bottom, 4 px a rupee
const pane = (width = 330): Converters => ({ x: (t) => t, y, width, height: H, labelStyle: style, measure: (t) => t.length * 7 });

/** Every label chip painted: a band label's chip, and a box name's chip (a rect painted on top). */
const labelRects = (list: Shape[]): Rect[] => list.flatMap((s) =>
  s.type === "chip" || (s.type === "rect" && s.top === true) ? [{ x: s.x, y: s.y, w: s.w, h: s.h }] : []);
const names = (list: Shape[]) => list.flatMap((s) => (s.type === "text" ? [s.text] : []));
const anyOverlap = (rects: Rect[]) => rects.some((a, i) => rects.slice(i + 1).some((b) => overlaps(a, b)));

// A buyer area from 120 to 160 (pane y 220 .. 60): its label's first spot is inside the band, top left (y 62).
const BAND: LayerBand = { low: 120, high: 160, role: "demand", label: "Buyer area · 15m · fresh" };
// A base candle box just above the band at the left (161 .. 166: y 56 .. 36), its name asked under the box (y 60):
// exactly where the band label goes first.
const BASE: LayerMark = { time: 12, low: 161, high: 166, text: "Base", position: "below", role: "demand" };

describe("box names and zone labels share one collision check (M98-P1 Q3)", () => {
  it("the precondition: placed on their own, the band label and the box name sit on each other", () => {
    const c = pane();
    const own = [...labelRects(bandShapes([BAND], c)), ...labelRects(markShapes([BASE], c))];
    expect(own).toHaveLength(2);
    expect(anyOverlap(own)).toBe(true);
  });

  it("one layer: the box name keeps its spot and the band label moves clear of it; both are drawn", () => {
    const c = pane();
    const list = layerShapes({ bands: [BAND], marks: [BASE] }, c);
    const rects = labelRects(list);
    expect(names(list).sort()).toEqual(["Base", "Buyer area · 15m · fresh"]);
    expect(rects).toHaveLength(2);
    expect(anyOverlap(rects)).toBe(false);
    expect(markLabelRects([BASE], c)).toEqual(labelRects(markShapes([BASE], c)));            // the same placement
  });

  it("names of neighbouring boxes never overlap each other either", () => {
    const c = pane();
    const legIn: LayerMark = { time: 8, low: 150, high: 155, text: "Leg-in", position: "above", role: "demand" };
    const legOut: LayerMark = { time: 16, low: 150, high: 156, text: "Leg-out", position: "above", role: "demand" };
    const list = markShapes([legIn, BASE, legOut], c);
    expect(names(list)).toEqual(["Leg-in", "Base", "Leg-out"]);
    expect(anyOverlap(labelRects(list))).toBe(false);
  });

  it("a phone pane full of zones: no label sits on a box name, whatever is left out", () => {
    const c = pane(330);
    const bands: LayerBand[] = [120, 128, 136, 144, 152, 160].map((lo, i) => ({ low: lo, high: lo + 6, role: i % 2 ? "supply" : "demand",
                                                                               label: `${i % 2 ? "Seller" : "Buyer"} area · 15m · fresh`, start: 4 }));
    const marks: LayerMark[] = [4, 12, 20].map((t, i) => ({ time: t, low: 124 + 8 * i, high: 127 + 8 * i, text: ["Leg-in", "Base", "Leg-out"][i],
                                                         position: i === 1 ? "below" : "above", role: "demand" }));
    const list = layerShapes({ bands, marks }, c);
    expect(names(list)).toEqual(expect.arrayContaining(["Leg-in", "Base", "Leg-out"]));    // the box names always win
    expect(anyOverlap(labelRects(list))).toBe(false);
  });

  it("two layers (the zones and the tap's highlight): the engine keeps the zone label off the box name", () => {
    const candles = { setData: vi.fn(), update: vi.fn(), createPriceLine: vi.fn(), removePriceLine: vi.fn(), priceToCoordinate: y,
      coordinateToPrice: (v: number) => 100 + (H - v) / 4, attachPrimitive: vi.fn(), detachPrimitive: vi.fn(),
      data: () => Array.from({ length: 30 }, (_, i) => ({ time: i + 1 })), subscribeDataChanged: vi.fn(), unsubscribeDataChanged: vi.fn() };
    const ts = { logicalToCoordinate: (l: number) => l * 10, coordinateToLogical: (x: number) => x / 10, subscribeVisibleTimeRangeChange: vi.fn(),
      unsubscribeVisibleTimeRangeChange: vi.fn() };
    const chart = { timeScale: () => ts, subscribeCrosshairMove: vi.fn(), unsubscribeCrosshairMove: vi.fn(), subscribeClick: vi.fn(), unsubscribeClick: vi.fn(),
      applyOptions: vi.fn(), priceScale: () => ({ width: () => 0 }) };
    const engine = new LightweightEngine(chart as never, candles as never);
    engine.setLabelStyle(style);
    engine.setLayer("sd_zones", { bands: [BAND] });
    const zones = candles.attachPrimitive.mock.calls[0][0] as LayerPrimitive;
    const paint = (layer: LayerPrimitive) => layer.build({ ...layer.converters(330, H)!, measure: (t: string) => t.length * 7 });
    const alone = labelRects(paint(zones));
    engine.setLayer("sd_zones_focus", { marks: [{ ...BASE, time: 2, end: 3 }] });             // the bar at x 20 (logical 1)
    const focus = candles.attachPrimitive.mock.calls[1][0] as LayerPrimitive;
    const zoneChips = labelRects(paint(zones)), nameChips = labelRects(paint(focus));
    expect(nameChips).toHaveLength(1);
    expect(zoneChips).toHaveLength(1);
    expect(anyOverlap([...alone, ...nameChips])).toBe(true);                                  // where the label was
    expect(anyOverlap([...zoneChips, ...nameChips])).toBe(false);                             // it moved clear
    engine.setLayer("sd_zones_focus", null);                                                  // the tooltip closes
    expect(labelRects(paint(zones))).toEqual(alone);                                          // back to its first spot
  });
});
