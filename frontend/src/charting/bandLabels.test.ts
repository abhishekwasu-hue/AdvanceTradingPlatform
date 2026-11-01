/**
 * M75 W-1 (review W2 / W3): band labels never sit on the price axis, the floating view controls or each other. The
 * pane is the plot without the price axis (the axis starts at the pane's right edge), so "inside the pane" is "off
 * the axis"; the controls' rectangle comes in as `avoid`, as ChartV2 measures it.
 */
import { describe, expect, it } from "vitest";
import type { LayerBand } from "./engine";
import { bandShapes, overlaps, placeBandLabels, textRect, withAlpha, type Converters, type Rect, type Shape } from "./geometry";

const texts = (list: Shape[]) => list.filter((s): s is Extract<Shape, { type: "text" }> => s.type === "text");

/** A pane `width` x `height`; price 100 at the bottom, `pxPerRupee` up. */
const pane = (width: number, height: number, avoid: Rect[] = [], pxPerRupee = 4): Converters =>
  ({ x: (t) => t, y: (p) => height - (p - 100) * pxPerRupee, width, height, avoid });

/** The view controls as ChartV2 reserves them: bottom right of the pane, 44 px tall on a phone, 36 px from md. */
const controls = (width: number, height: number, phone: boolean): Rect => {
  const h = (phone ? 44 : 36) + 8, w = phone ? 170 : 150;
  return { x: width - 8 - w, y: height - 8 - h, w, h };
};

function assertClear(list: Shape[], c: Converters) {
  const rects = texts(list).map((t) => textRect(t));
  for (const r of rects) {
    expect(r.x).toBeGreaterThanOrEqual(0);
    expect(r.x + r.w).toBeLessThanOrEqual(c.width);              // never over the price axis (right of the pane)
    expect(r.y).toBeGreaterThanOrEqual(0);
    expect(r.y + r.h).toBeLessThanOrEqual(c.height);
    for (const a of c.avoid ?? []) expect(overlaps(r, a)).toBe(false);
  }
  rects.forEach((a, i) => rects.slice(i + 1).forEach((b) => expect(overlaps(a, b)).toBe(false)));
}

describe("band labels (M75 W-1)", () => {
  for (const [name, width, height, phone] of [["phone 390", 330, 192, true], ["desktop 1440", 1300, 480, false]] as const) {
    it(`${name}: a seller area at the bottom right - the label goes inside the band, clear of the controls and the axis`, () => {
      const c = pane(width, height, [controls(width, height, phone)]);
      // a band right where the controls are (low prices, bottom of the pane)
      const list = bandShapes([{ low: 100, high: 112, role: "supply", color: "#dc2626", label: "Supply area" }], c);
      assertClear(list, c);
      expect(list[0]).toMatchObject({ type: "rect", x: 0, w: width });                     // a FILLED band across the pane
      expect((list[0] as Extract<Shape, { type: "rect" }>).fill).toBe("#dc262626");       // translucent (the 0.15 default)
      expect(texts(list)).toHaveLength(1);
      expect(texts(list)[0]).toMatchObject({ x: 4, align: "left", text: "Supply area" });
    });

    it(`${name}: many stacked areas - labels never overlap each other, the controls or the axis; one with no room is left out`, () => {
      const c = pane(width, height, [controls(width, height, phone), { x: 0, y: 0, w: 120, h: 30 }]);
      const bands: LayerBand[] = Array.from({ length: 14 }, (_, i) => ({
        low: 100 + i * 3, high: 100 + i * 3 + (i % 3 === 0 ? 0 : 2), label: `demand area · 15m · tested ${i}`,
      }));
      const list = bandShapes(bands, c);
      assertClear(list, c);
      expect(texts(list).length).toBeGreaterThan(0);
      expect(texts(list).length).toBeLessThanOrEqual(bands.length);
    });
  }

  it("a label that would run onto the axis moves inside the pane; one wider than the pane is not drawn", () => {
    const c = pane(200, 300, [{ x: 0, y: 0, w: 200, h: 200 }]);          // the left spots are taken: only the right is free
    const [label] = placeBandLabels([{ top: 250, bottom: 280, text: "Supply area", color: "#000" }], c);
    expect(label).toBeDefined();
    const r = textRect(label as Extract<Shape, { type: "text" }>);
    expect(r.x + r.w).toBeLessThanOrEqual(196);
    expect(placeBandLabels([{ top: 250, bottom: 280, text: "x".repeat(40), color: "#000" }], c)).toEqual([]);
  });

  it("a band taller than the pane still shows its label inside, at the top of what is visible", () => {
    const c = pane(400, 300);
    const [label] = placeBandLabels([{ top: -500, bottom: 900, text: "Demand area", color: "#000" }], c);
    expect(label).toMatchObject({ x: 4, y: 2, baseline: "top" });
  });

  it("theme colours come as rgb: the band fill keeps its alpha", () => {
    expect(withAlpha("rgb(21, 128, 61)", 0.18)).toBe("rgba(21, 128, 61, 0.18)");
    expect(withAlpha("rgba(21, 128, 61, 1)", 0.5)).toBe("rgba(21, 128, 61, 0.5)");
  });
});
