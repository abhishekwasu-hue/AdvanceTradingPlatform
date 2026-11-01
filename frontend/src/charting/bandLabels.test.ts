/**
 * M75 W-1 (review W2 / W3): band labels never sit on the price axis, the floating view controls or each other. The
 * pane is the plot without the price axis (the axis starts at the pane's right edge), so "inside the pane" is "off
 * the axis"; the controls' rectangle comes in as `avoid`, as ChartV2 measures it.
 */
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { SeriesAttachedParameter, Time } from "lightweight-charts";
import { LayerPrimitive, onTop, paint, type PaintContext } from "./primitives";
import type { LayerBand } from "./engine";
import {
  AA_CONTRAST, BAND_LABEL_FONT_PX, CHIP_ALPHA, CHIP_PAD_X, LABEL_PAD, areaLabelText, bandShapes, chipHeight, contrastRatio, labelInk, overlaps,
  placeBandLabels, withAlpha, type BandLabelStyle, type Converters, type Rect, type Shape,
} from "./geometry";
import { LightweightEngine } from "./lightweight";
import { readLayer, type ReadChart } from "../chart/readChart";
import { headlineAreas, type AnswerHeadline } from "../copilot/chat/model";
import { BandTip } from "../chart/ChartV2";

type Text = Extract<Shape, { type: "text" }>;
type Chip = Extract<Shape, { type: "chip" }>;
const texts = (list: Shape[]) => list.filter((s): s is Text => s.type === "text");
const chips = (list: Shape[]) => list.filter((s): s is Chip => s.type === "chip");
const box = (c: Chip): Rect => ({ x: c.x, y: c.y, w: c.w, h: c.h });

/** A pane `width` x `height`; price 100 at the bottom, `pxPerRupee` up. */
const pane = (width: number, height: number, avoid: Rect[] = [], pxPerRupee = 4): Converters =>
  ({ x: (t) => t, y: (p) => height - (p - 100) * pxPerRupee, width, height, avoid });

/** The view controls as ChartV2 reserves them: bottom right of the pane, 44 px tall on a phone, 36 px from md. */
const controls = (width: number, height: number, phone: boolean): Rect => {
  const h = (phone ? 44 : 36) + 8, w = phone ? 170 : 150;
  return { x: width - 8 - w, y: height - 8 - h, w, h };
};

function assertClear(list: Shape[], c: Converters) {
  const rects = chips(list).map(box);
  expect(rects).toHaveLength(texts(list).length);                // every label sits on its own chip
  for (const r of rects) {
    expect(r.x).toBeGreaterThanOrEqual(LABEL_PAD);                // M78 F4: a few px inside the pane, left...
    expect(r.x + r.w).toBeLessThanOrEqual(c.width - LABEL_PAD);   // ...and right: never over the price axis
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
      const list = bandShapes([{ low: 100, high: 112, role: "supply", color: "#dc2626", label: "Seller area" }], c);
      assertClear(list, c);
      expect(list[0]).toMatchObject({ type: "rect", x: 0, w: width });                     // a FILLED band across the pane
      expect((list[0] as Extract<Shape, { type: "rect" }>).fill).toBe("#dc262626");       // translucent (the 0.15 default)
      expect(texts(list)).toHaveLength(1);
      expect(chips(list)[0]).toMatchObject({ x: LABEL_PAD, top: true, band: { low: 100, high: 112, label: "Seller area" } });
      expect(texts(list)[0]).toMatchObject({ x: LABEL_PAD + CHIP_PAD_X, align: "left", text: "Seller area", top: true });
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
    const [label] = placeBandLabels([{ top: 250, bottom: 280, text: "Seller area", color: "#000" }], c);
    expect(label).toBeDefined();
    const r = box(label as Chip);
    expect(r.x + r.w).toBeLessThanOrEqual(196);
    expect(placeBandLabels([{ top: 250, bottom: 280, text: "x".repeat(40), color: "#000" }], c)).toEqual([]);
  });

  it("a low band under the controls with a long label: the label goes just above the controls, near the band", () => {
    const c = pane(330, 192, [controls(330, 192, true)]);
    const ctl = (c.avoid ?? [])[0];
    const top = ctl.y + 10, bottom = ctl.y + 24;                        // the band runs behind the controls
    const [label] = placeBandLabels([{ top, bottom, text: "Buyer area · 15m · fresh", color: "#000" }], c);
    expect(label).toBeDefined();
    const r = box(label as Chip);
    expect(overlaps(r, ctl)).toBe(false);
    expect(top - (r.y + r.h)).toBeLessThanOrEqual(28);
  });

  it("a band taller than the pane still shows its label inside, at the top of what is visible", () => {
    const c = pane(400, 300);
    const [label] = placeBandLabels([{ top: -500, bottom: 900, text: "Buyer area", color: "#000" }], c);
    expect(label).toMatchObject({ type: "chip", x: LABEL_PAD, y: 2 });
  });

  it("theme colours come as rgb: the band fill keeps its alpha", () => {
    expect(withAlpha("rgb(21, 128, 61)", 0.18)).toBe("rgba(21, 128, 61, 0.18)");
    expect(withAlpha("rgba(21, 128, 61, 1)", 0.5)).toBe("rgba(21, 128, 61, 0.5)");
  });

  it("a layer paints as soon as it is attached (a reading that answers while the chart is idle shows at once)", () => {
    const requestUpdate = vi.fn();
    const layer = new LayerPrimitive(() => [], () => null);
    layer.attached({ requestUpdate } as unknown as SeriesAttachedParameter<Time>);
    expect(requestUpdate).toHaveBeenCalledTimes(1);
    layer.refresh();
    expect(requestUpdate).toHaveBeenCalledTimes(2);
  });
});

/** The chart tokens as tokens.css holds them (space-separated rgb), read from the file so the check follows it. */
const css = readFileSync(new URL("../styles/tokens.css", import.meta.url), "utf8");
function token(block: RegExp, name: string): string {
  const m = block.exec(css);
  if (!m) throw new Error(`no block ${block}`);
  const v = new RegExp(`--${name}:\\s*(\\d+ \\d+ \\d+)`).exec(m[1]);
  if (!v) throw new Error(`no --${name}`);
  return `rgb(${v[1].split(" ").join(", ")})`;
}
const THEMES = {
  dark: /\[data-chart-theme="dark"\]\s*\{([^}]*)\}/,
  light: /\[data-chart-theme="light"\]\s*\{([^}]*)\}/,
};

describe("band label legibility (M78)", () => {
  const style: BandLabelStyle = { fontPx: 12, chip: "rgb(9, 12, 18)", ink: "rgb(194, 202, 216)" };

  it("F1: each label is a rounded chip in the chart background at ~85 % with the text on it, both above the series", () => {
    const c = { ...pane(330, 192), labelStyle: style };
    const list = bandShapes([{ low: 120, high: 130, color: "rgb(239, 68, 68)", label: "Seller area · 15m · fresh" }], c);
    const [chip] = chips(list), [text] = texts(list);
    expect(chip).toMatchObject({ fill: withAlpha(style.chip as string, CHIP_ALPHA), h: chipHeight(12) });
    expect(CHIP_ALPHA).toBeCloseTo(0.85);
    expect(chip.r).toBeGreaterThan(0);
    expect(list.indexOf(chip)).toBeLessThan(list.indexOf(text));        // the chip first, the text over it
    expect(text.x).toBeGreaterThan(chip.x);
    expect(text.y).toBeCloseTo(chip.y + chip.h / 2);                    // centred in the chip
    expect(list.filter(onTop)).toEqual([chip, text]);                   // only the label goes above the candles
    expect(list.filter((s) => !onTop(s)).map((s) => s.type)).toEqual(["rect"]);
  });

  it("F1: the layer paints its bands under the series and its labels in a second view on top", () => {
    const layer = new LayerPrimitive(() => [], () => null);
    expect(layer.paneViews().map((v) => v.zOrder?.())).toEqual(["bottom", "top"]);
    const calls: string[] = [];
    const ctx = new Proxy({}, { get: (_t, k) => (typeof k === "string" && !["fillStyle", "strokeStyle", "lineWidth", "font", "textAlign", "textBaseline"].includes(k)
      ? (...a: unknown[]) => calls.push(`${k}${k === "fillText" ? `:${String(a[0])}` : ""}`) : undefined), set: () => true }) as unknown as PaintContext;
    const c = { ...pane(330, 192), labelStyle: style };
    paint(ctx, bandShapes([{ low: 120, high: 130, color: "rgb(239, 68, 68)", label: "Seller area" }], c).filter(onTop));
    expect(calls.filter((k) => k === "arcTo")).toHaveLength(4);         // the rounded chip
    expect(calls.filter((k) => k.startsWith("fillText")).slice(-1)[0]).toBe("fillText:Seller area");
    expect(calls.lastIndexOf("fill")).toBeLessThan(calls.indexOf("fillText:Seller area"));   // the chip under the text
  });

  for (const [name, width, height] of [["phone 390", 330, 192], ["desktop 1440", 1300, 480]] as const) {
    it(`F4 ${name}: the chip box stays inside the pane with LABEL_PAD on the left and the right, the text further in`, () => {
      const c = { ...pane(width, height), labelStyle: style };
      const bands: LayerBand[] = [
        { low: 100, high: 108, label: "Buyer area · 15m · fresh" }, { low: 120, high: 126, label: "Buyer area · 15m · tested" },
        { low: 140, high: 146, label: "Seller area · 15m · fresh" },
      ];
      const list = bandShapes(bands, c);
      expect(chips(list)).toHaveLength(3);
      for (const ch of chips(list)) {
        expect(ch.x).toBeGreaterThanOrEqual(LABEL_PAD);
        expect(ch.x + ch.w).toBeLessThanOrEqual(width - LABEL_PAD);
        expect(ch.y).toBeGreaterThanOrEqual(0);
        expect(ch.y + ch.h).toBeLessThanOrEqual(height);
      }
      for (const t of texts(list)) expect(t.x).toBeGreaterThanOrEqual(LABEL_PAD + CHIP_PAD_X);
      // pushed to the right edge, the chip still keeps LABEL_PAD from the axis
      const right = placeBandLabels([{ top: 50, bottom: 80, text: "Seller area · 15m · fresh", color: "#000" }],
                                    { ...c, avoid: [{ x: 0, y: 0, w: 60, h: height }] });
      const ch = chips(right)[0];
      expect(ch.x + ch.w).toBeLessThanOrEqual(width - LABEL_PAD);
      expect(ch.x + ch.w).toBeGreaterThan(width - LABEL_PAD - 1);
    });
  }

  for (const theme of ["dark", "light"] as const) {
    it(`F2 ${theme}: the buyer / seller label text on the chip is WCAG AA (>= 4.5 : 1), computed from the token values`, () => {
      const chip = token(THEMES[theme], "chart-bg");
      const ink = token(THEMES[theme], "chart-text");
      const s: BandLabelStyle = { fontPx: 12, chip, ink };
      for (const role of ["chart-up", "chart-down"]) {
        const color = token(THEMES[theme], role);
        const used = labelInk(color, s);
        expect(used, `${theme} ${role}`).toBe(color);                    // the band's own token reads at AA
        expect(contrastRatio(used, chip), `${theme} ${role}`).toBeGreaterThanOrEqual(AA_CONTRAST);
        const [text] = texts(bandShapes([{ low: 120, high: 130, color, label: "Seller area" }], { ...pane(330, 192), labelStyle: s }));
        expect(text.color).toBe(color);
      }
      expect(contrastRatio(ink, chip)).toBeGreaterThanOrEqual(AA_CONTRAST);   // the fallback ink is AA too
    });
  }

  it("F2: a band colour too faint for the chip (a trader's own candle colour) falls back to the chart text token", () => {
    const light: BandLabelStyle = { fontPx: 12, chip: "rgb(255, 255, 255)", ink: "rgb(71, 85, 105)" };
    expect(contrastRatio("#0d9488", "rgb(255, 255, 255)")).toBeLessThan(AA_CONTRAST);
    expect(labelInk("#0d9488", light)).toBe("rgb(71, 85, 105)");
    expect(labelInk("#0d9488", undefined)).toBe("#0d9488");             // no chip (the v1 chart): unchanged
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21);
  });

  it("F2: the label font is the chart's axis-label size (never smaller) and the chip grows with it", () => {
    expect(BAND_LABEL_FONT_PX).toBe(12);                                // theme.ts chartFontSize() at Normal: Math.round(12 * 1)
    for (const fontPx of [12, 14, 15]) {
      const [chip, text] = bandShapes([{ low: 100, high: 140, label: "Buyer area" }], { ...pane(400, 300), labelStyle: { ...style, fontPx } })
        .filter(onTop) as [Chip, Text];
      expect(text.size).toBe(fontPx);
      expect(chip.h).toBe(chipHeight(fontPx));
      expect(chip.h).toBeGreaterThan(fontPx);
    }
  });

  it("F3: one label text for every band layer - the Copilot answer chart and the Charts tab read the same", () => {
    expect(areaLabelText({ role: "supply", timeframe: "15m", state: "fresh" })).toBe("Seller area · 15m · fresh");
    expect(areaLabelText({ role: "demand", timeframe: null, state: "tested" })).toBe("Buyer area · tested");
    expect(areaLabelText({ role: "supply" })).toBe("Seller area");
    expect(areaLabelText({ role: null, timeframe: "1d" })).toBe("Area · 1d");
    const area = { key: "z", role: "supply" as const, low: 24700, high: 24780, low_text: "", high_text: "", timeframe: "15m", state: "fresh",
                   state_words: "", touches: 0, words: "" };
    const charts = readLayer({ areas: [area], structure: null, commitment: null } as unknown as ReadChart).bands?.[0].label;
    const head = { area: { side: "supply", from: 24700, to: 24780, where: "above", distance: 1, distance_pct: null, timeframe: "15m", state: "fresh" } };
    const copilot = headlineAreas(head as unknown as AnswerHeadline)[0].label;
    expect(copilot).toBe(charts);
    expect(copilot).toBe("Seller area · 15m · fresh");
    // the data lacks timeframe / state: only those parts drop
    expect(headlineAreas({ area: { ...head.area, timeframe: undefined, state: undefined } } as unknown as AnswerHeadline)[0].label).toBe("Seller area");
  });

  it("F5: the band under a point - its label chip or its price range - gives its bottom / top prices", () => {
    const y = (p: number) => 300 - (p - 100) * 4;
    const candles = { setData: vi.fn(), update: vi.fn(), createPriceLine: vi.fn(), removePriceLine: vi.fn(), priceToCoordinate: y,
      coordinateToPrice: (v: number) => 100 + (300 - v) / 4, attachPrimitive: vi.fn(), detachPrimitive: vi.fn(), data: () => [{ time: 1 }, { time: 2 }],
      subscribeDataChanged: vi.fn(), unsubscribeDataChanged: vi.fn() };
    const ts = { logicalToCoordinate: (l: number) => l * 10, coordinateToLogical: (x: number) => x / 10, subscribeVisibleTimeRangeChange: vi.fn(),
      unsubscribeVisibleTimeRangeChange: vi.fn() };
    const chart = { timeScale: () => ts, subscribeCrosshairMove: vi.fn(), unsubscribeCrosshairMove: vi.fn(), subscribeClick: vi.fn(), unsubscribeClick: vi.fn(),
      applyOptions: vi.fn(), priceScale: () => ({ width: () => 0 }) };
    const engine = new LightweightEngine(chart as never, candles as never);
    engine.setLabelStyle(style);
    engine.setLayer("areas", { bands: [{ low: 120, high: 130, role: "supply", label: "Seller area · 15m · fresh" }, { low: 150, high: 150, label: "Line" }] });
    const layer = candles.attachPrimitive.mock.calls[0][0] as LayerPrimitive;
    // paint the top view once so the chips are known
    const ctx = { save() {}, restore() {}, measureText: (t: string) => ({ width: t.length * 7 }), beginPath() {}, moveTo() {}, lineTo() {}, arcTo() {},
      closePath() {}, fill() {}, stroke() {}, fillRect() {}, strokeRect() {}, fillText() {}, setLineDash() {} };
    const target = { useMediaCoordinateSpace: (f: (s: unknown) => void) => f({ context: ctx, mediaSize: { width: 330, height: 300 } }) };
    layer.paneViews()[1].renderer()?.draw(target as never);
    expect(engine.bandAt(200, y(125))).toEqual({ low: 120, high: 130, label: "Seller area · 15m · fresh" });   // inside the band
    expect(engine.bandAt(200, y(150) + 2)).toMatchObject({ low: 150, high: 150 });                          // a line, within a few px
    expect(engine.bandAt(200, y(140))).toBeNull();                                                          // between bands
    expect(engine.bandAt(200, 400)).toBeNull();                                                             // below the price pane
    const chip = layer.labelAt(LABEL_PAD + 2, y(130) + 4);
    expect(chip).toEqual({ low: 120, high: 130, label: "Seller area · 15m · fresh" });                       // on the label's chip
  });

  it("F5: the tooltip names the band and shows its top and bottom (formatNumber), turned towards the middle", () => {
    const tip = { band: { low: 24700, high: 24780.5, label: "Seller area · 15m · fresh" }, x: 40, y: 60 };
    const html = renderToStaticMarkup(BandTip({ tip, width: 330 }));
    expect(html).toContain('role="tooltip"');
    expect(html).toContain("Seller area · 15m · fresh");
    expect(html).toMatch(/Top<\/dt><dd[^>]*>24,780.50</);
    expect(html).toMatch(/Bottom<\/dt><dd[^>]*>24,700.00</);
    expect(html).toContain("left:52px");
    expect(renderToStaticMarkup(BandTip({ tip: { ...tip, x: 300 }, width: 330 }))).toContain("right:42px");
  });
});
