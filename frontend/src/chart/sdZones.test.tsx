/**
 * M97 PR-4: the "Supply & Demand zones" layer - time-anchored bands and right-extended lines (pure geometry, existing
 * bands unchanged), the labels through the W-1 placer, the tap (hit-test with the band's id and rows, the tooltip rows,
 * the highlighted candles), the timeframe choice and the 10 min / 2 hours chart timeframes.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { LABEL_PAD, bandLeft, bandShapes, barSpan, markShapes, overlaps, placeBandLabels, segmentShapes, type Converters, type Rect, type Shape } from "../charting/geometry";
import { LightweightEngine } from "../charting/lightweight";
import type { LayerPrimitive } from "../charting/primitives";
import { BandSheet, BandTip } from "./ChartV2";
import proof from "../../e2e/fixtures/sd-zones-proof.json";
import { MENU_ITEMS } from "./indicatorsModel";
import { offeredTimeframes } from "./chartsPage";
import {
  SD_FOCUS_ID, SD_LAYER_ID, SD_TF_OPACITY, sdFocusFor, sdFocusLayer, sdNote, sdTimeframesFor, sdTipRows, shownSdTimeframes, toSdLayer, toggleSdTimeframe,
  type SdZone, type SdZonesResponse,
} from "./sdZones";
import { SdZonesBar } from "./SdZonesBar";
import { TIMEFRAMES, parseCustomInterval, servedText, timeframeShort } from "./timeframes";

/** A pane: x = time (seconds) - 1000 px, price 100 at the bottom, 4 px per rupee. */
const pane = (width = 400, height = 300): Converters => ({ x: (t) => t - 1000, y: (p) => height - (p - 100) * 4, width, height });
const rects = (list: Shape[]) => list.filter((s): s is Extract<Shape, { type: "rect" }> => s.type === "rect");
const chips = (list: Shape[]) => list.filter((s): s is Extract<Shape, { type: "chip" }> => s.type === "chip");

const candle = (time: number, low: number, high: number) => ({ time, end: time + 10, low, high, range: high - low, body_pct: 80, mult: 1.8 });
const zone = (over: Partial<SdZone> = {}): SdZone => ({
  id: "sd-15m-abc", timeframe: "15m", side: "demand", formation: "DBR", state: "fresh", tests: 0, proximal: 130, distal: 120,
  base_start: 1100, base_end: 1110, formed: 1120, violated_at: null, leg_in: candle(1090, 125, 140),
  base: { candles: 2, times: [1100, 1110], high: 130, low: 121, max_body_pct: 30, max_mult: 0.5, bars: [candle(1100, 121, 130), candle(1110, 122, 129)] },
  leg_out: { ...candle(1120, 120, 145), of: "average range" }, label: "Demand · DBR · Fresh · 15m", ...over,
});
const resp = (zones: SdZone[], over: Partial<SdZonesResponse> = {}): SdZonesResponse => ({
  schema: "sd-layer/1", symbol: "NSE:NIFTY 50", at: "2026-11-11T10:00:00+00:00", note: "", include_violated: false, settings: {}, settings_note: "",
  timeframes: [{ timeframe: "15m", bars: 300, as_of_bar: 1200, zones, more: 0, hidden_violated: 0, source: "lake:15m", reason: null }], ...over,
});

describe("time-anchored bands (geometry)", () => {
  it("a band with a start begins at its time and runs to the right edge; one without is full width as before", () => {
    const c = pane();
    const [anchored] = rects(bandShapes([{ low: 120, high: 130, start: 1100 }], c));
    expect(anchored).toMatchObject({ x: 100, w: 300 });
    const [full] = rects(bandShapes([{ low: 120, high: 130 }], c));
    expect(full).toMatchObject({ x: 0, w: 400 });
    const before = rects(bandShapes([{ low: 120, high: 130, start: 900 }], c))[0];           // began before the first bar shown
    expect(before).toMatchObject({ x: 0, w: 400 });
    expect(bandShapes([{ low: 120, high: 130, start: 1500 }], c)).toEqual([]);                // starts past the right edge
    expect(bandLeft({ start: Number.NaN }, c)).toBeNull();
    const line = bandShapes([{ low: 125, high: 125, start: 1100 }], c)[0];
    expect(line).toMatchObject({ type: "segment", x1: 100, x2: 400 });
  });

  it("its label sits just inside its left edge (the W-1 placer), carrying the band's id and rows", () => {
    const tip = [["Formation", "DBR"]] as const;
    const list = bandShapes([{ low: 120, high: 130, start: 1100, label: "Demand · DBR · Fresh · 15m", id: "z1", tip }], pane());
    const [chip] = chips(list);
    expect(chip.x).toBe(100 + LABEL_PAD);
    expect(chip.band).toMatchObject({ id: "z1", tip, low: 120, high: 130 });
    // a full-width band's label stays at the pane's left as before
    expect(chips(bandShapes([{ low: 120, high: 130, label: "Seller area" }], pane()))[0].x).toBe(LABEL_PAD);
  });

  it("segments with extend right are horizontal lines from their start to the right edge; others unchanged", () => {
    const c = pane();
    const [s] = segmentShapes([{ t1: 1100, p1: 130, t2: 1100, p2: 999, extend: "right" }], c);
    expect(s).toMatchObject({ type: "segment", x1: 100, x2: 400, y1: c.y(130), y2: c.y(130) });
    expect(segmentShapes([{ t1: 1500, p1: 130, t2: 1500, p2: 130, extend: "right" }], c)).toEqual([]);
    const [plain] = segmentShapes([{ t1: 1100, p1: 120, t2: 1200, p2: 130 }], c);
    expect(plain).toMatchObject({ x1: 100, x2: 200, y1: c.y(120), y2: c.y(130) });
  });
});

describe("the layer", () => {
  it("each zone is a band from its base start with its proximal / distal lines and its label", () => {
    const data = toSdLayer(resp([zone(), zone({ id: "s", side: "supply", formation: "RBD", proximal: 150, distal: 160, label: "Supply · RBD · Tested 2 · 15m", state: "tested", tests: 2 })]));
    expect(data?.bands).toHaveLength(2);
    expect(data?.bands?.[0]).toMatchObject({ low: 120, high: 130, start: 1100, role: "demand", id: "sd-15m-abc", label: "Demand · DBR · Fresh · 15m" });
    expect(data?.segments?.filter((s) => s.extend === "right")).toHaveLength(4);
    expect(data?.segments?.[0]).toMatchObject({ t1: 1100, p1: 130, style: "solid", role: "demand" });
    expect(data?.segments?.[1]).toMatchObject({ t1: 1100, p1: 120, style: "dashed" });
    expect(toSdLayer(resp([]))).toBeNull();
    const violated = toSdLayer(resp([zone({ state: "violated" })]))?.bands?.[0];
    expect(violated).toMatchObject({ style: "dashed" });
    expect(violated?.opacity).toBeLessThan(0.1);
  });

  it("the tooltip rows name the formation, state, lines and the leg / base numbers - structure words only", () => {
    const rows = sdTipRows(zone({ state: "tested", tests: 2 }));
    expect(rows.map(([k]) => k)).toEqual(["Formation", "State", "Proximal", "Distal", "Leg-in", "Base", "Leg-out"]);
    expect(rows[1][1]).toBe("Tested 2");
    expect(rows[2][1]).toBe("130.00");
    expect(rows[4][1]).toContain("1.8× avg");
    const text = JSON.stringify(rows).toLowerCase();
    for (const w of ["buy", "sell", "target", "stop"]) expect(text).not.toContain(w);
    const html = renderToStaticMarkup(BandTip({ tip: { band: { low: 120, high: 130, label: "Demand · DBR · Fresh · 15m", id: "z", tip: rows }, x: 40, y: 40 }, width: 390 }));
    expect(html).toContain("data-band-rows");
    expect(html).toContain("Proximal</dt><dd");
    expect(html).not.toContain("data-band-top");
    expect(html).toContain("left:8px");                         // a phone-width chart: the wide tooltip spans it
    expect(html).toContain("whitespace-normal");
  });

  it("a tap on a zone boxes its leg-in, its base (one box) and its leg-out candles; another band highlights nothing", () => {
    const z = zone();
    const focus = sdFocusFor([z], { id: z.id });
    expect(focus?.marks?.map((m) => [m.text, m.time, m.end, m.low, m.high])).toEqual([
      ["Leg-in", 1090, 1100, 125, 140], ["Base", 1100, 1120, 121, 130], ["Leg-out", 1120, 1130, 120, 145]]);
    // each name on its own far side: the drop into the demand base above it, the base under it, the rally out above it
    expect(focus?.marks?.map((m) => m.position)).toEqual(["above", "below", "above"]);
    expect(focus?.marks?.every((m) => m.role === "demand")).toBe(true);
    expect(sdFocusFor([z], { id: "other" })).toBeNull();
    expect(sdFocusFor([z], null)).toBeNull();
    expect(sdFocusLayer({ ...z, side: "supply" }).marks?.[1].position).toBe("above");
    expect([SD_LAYER_ID, SD_FOCUS_ID]).toEqual(["sd_zones", "sd_zones_focus"]);
  });

  it("M98 F1: a candle's box covers exactly the chart bars of that candle - same timeframe, higher on lower, lower on higher", () => {
    const quarter = [0, 900, 1800, 2700, 3600, 4500];                       // 15m bar starts
    expect(barSpan(quarter, 1800, 2700)).toEqual([2, 2]);                   // the same timeframe: that one bar
    expect(barSpan(quarter, 0, 3600)).toEqual([0, 3]);                      // a 1H candle on a 15m chart: its four bars
    const hours = [0, 3600, 7200];
    expect(barSpan(hours, 4500, 5400)).toEqual([1, 1]);                     // a 15m candle on a 1H chart: the hour holding it
    expect(barSpan(hours, -900, 0)).toBeNull();                             // before the first bar
    // a day's last bar (15:15 IST start, 15:30 close) and the next session: the box never runs into the next day
    const day = [1000, 1900, 80000];
    expect(barSpan(day, 1900, 2800)).toEqual([1, 1]);
    // the box in pixels: from the first bar's left edge to the last bar's right edge (10 px a bar here)
    const c: Converters = { ...pane(), span: (t, end) => { const b = barSpan(quarter, t, end); return b ? { x1: b[0] * 10 - 5, x2: b[1] * 10 + 5 } : null; } };
    const [box, text] = markShapes([{ time: 2700, end: 3600, low: 120, high: 130, text: "Leg-out", position: "above" }], c);
    expect(box).toMatchObject({ type: "rect", x: 25, w: 10 });
    expect(text).toMatchObject({ type: "text", x: 30, text: "Leg-out", align: "center", top: true });
    expect((text as { y: number }).y).toBeLessThan((box as { y: number }).y);          // above the box
    // the view controls over the asked side: the name goes to the other side of the box
    const blocked = markShapes([{ time: 2700, end: 3600, low: 120, high: 130, text: "Base", position: "below" }],
      { ...c, avoid: [{ x: 0, y: (c.y(120) ?? 0) + 2, w: 100, h: 30 }] });
    expect((blocked[1] as { y: number }).y).toBeLessThan((blocked[0] as { y: number }).y);
  });

  it("M98 F1 end to end: the proof fixture's zones (the detector's own rows) box the very bars it names", () => {
    type Bar = { timestamp: string; open: number; high: number; low: number; close: number };
    for (const [name, f] of Object.entries(proof.formations as Record<string, { bars: Bar[]; rows: { zones: SdZone[] } }>)) {
      const times = f.bars.map((b) => Math.floor(Date.parse(b.timestamp) / 1000));      // the chart's candle times (chartCore toTime)
      const z = f.rows.zones[0];
      const marks = sdFocusLayer(z).marks ?? [];
      const at = (m: { time: number; end?: number }) => barSpan(times, m.time, m.end ?? m.time + 1);
      const legIn = times.indexOf(z.leg_in.time), out = times.indexOf(z.leg_out.time);
      expect(legIn, name).toBeGreaterThan(0);
      expect(at(marks[0])).toEqual([legIn, legIn]);
      expect(at(marks[1])).toEqual([legIn + 1, out - 1]);                                // the base: the candles between
      expect(at(marks[2])).toEqual([out, out]);
      // the boxes hold those bars' own prices
      expect(marks[0].high).toBe(f.bars[legIn].high);
      expect(marks[2].low).toBe(f.bars[out].low);
      expect(marks[1].high).toBe(Math.max(...f.bars.slice(legIn + 1, out).map((b) => b.high)));
      // every candle is a real candle (low <= open, close <= high)
      for (const b of f.bars) expect(b.low <= Math.min(b.open, b.close) && b.high >= Math.max(b.open, b.close), `${name} ${b.timestamp}`).toBe(true);
    }
  });

  it("M98 F3: a zone's label sits inside its band clear of the candle bodies on the pane", () => {
    // bodies over the band's left part; the label goes right of them, inside the band
    const bodies: Rect[] = [100, 110, 120, 130, 140, 150].map((x) => ({ x, y: 170, w: 8, h: 40 }));
    const c: Converters = { ...pane(), bodies: () => bodies, measure: (t) => t.length * 7 };
    const list = placeBandLabels([{ top: 160, bottom: 220, text: "Demand · DBR · Fresh · 15m", color: "#0a0", left: 100, clearOfCandles: true }], c);
    const [chip] = chips(list);
    expect(chip).toBeTruthy();
    expect(bodies.some((b) => overlaps(chip, b))).toBe(false);
    expect(chip.y).toBeGreaterThanOrEqual(160);
    expect(chip.y + chip.h).toBeLessThanOrEqual(220);
    // the same band through the layer: the time-anchored band asks for it
    const fromLayer = chips(bandShapes([{ low: 115, high: 135, start: 1100, label: "Demand · DBR · Fresh · 15m" }], c));
    expect(fromLayer).toHaveLength(1);
    expect(bodies.some((b) => overlaps(fromLayer[0], b))).toBe(false);
  });

  it("M98 F3: on a phone a tapped zone opens a small bottom sheet with a close button (closed until tapped)", () => {
    const rows = sdTipRows(zone());
    const onClose = vi.fn();
    const html = renderToStaticMarkup(<BandSheet band={{ low: 120, high: 130, label: "Demand · DBR · Fresh · 15m", id: "z", tip: rows }} onClose={onClose} />);
    expect(html).toContain("data-band-sheet");
    expect(html).toContain("fixed inset-x-0 bottom-0");
    expect(html).toContain("max-h-[35vh]");
    expect(html).toContain('aria-label="Close zone details"');
    expect(html).toContain("Proximal</dt><dd");
    const el = BandSheet({ band: { low: 1, high: 2, label: "x", tip: rows }, onClose });
    const close = (el.props.children as React.ReactElement[])[0].props.children[1] as React.ReactElement<{ onClick: () => void }>;
    close.props.onClick();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("the engine hit-test finds a time-anchored band only right of its start, with its id and rows", () => {
    const y = (p: number) => 300 - (p - 100) * 4;
    const candles = { setData: vi.fn(), update: vi.fn(), createPriceLine: vi.fn(), removePriceLine: vi.fn(), priceToCoordinate: y,
      coordinateToPrice: (v: number) => 100 + (300 - v) / 4, attachPrimitive: vi.fn(), detachPrimitive: vi.fn(),
      data: () => [{ time: 1000 }, { time: 1010 }, { time: 1020 }], subscribeDataChanged: vi.fn(), unsubscribeDataChanged: vi.fn() };
    const ts = { logicalToCoordinate: (l: number) => l * 10, coordinateToLogical: (x: number) => x / 10, subscribeVisibleTimeRangeChange: vi.fn(),
      unsubscribeVisibleTimeRangeChange: vi.fn() };
    const chart = { timeScale: () => ts, subscribeCrosshairMove: vi.fn(), unsubscribeCrosshairMove: vi.fn(), subscribeClick: vi.fn(), unsubscribeClick: vi.fn(),
      applyOptions: vi.fn(), priceScale: () => ({ width: () => 0 }), paneSize: () => ({ width: 330, height: 300 }) };
    const engine = new LightweightEngine(chart as never, candles as never);
    const data = toSdLayer(resp([zone({ base_start: 1010 })]));
    engine.setLayer(SD_LAYER_ID, data);
    const layer = candles.attachPrimitive.mock.calls[0][0] as LayerPrimitive;
    const ctx = { save() {}, restore() {}, measureText: (t: string) => ({ width: t.length * 7 }), beginPath() {}, moveTo() {}, lineTo() {}, arcTo() {},
      closePath() {}, fill() {}, stroke() {}, fillRect() {}, strokeRect() {}, fillText() {}, setLineDash() {} };
    layer.paneViews()[1].renderer()?.draw({ useMediaCoordinateSpace: (f: (s: unknown) => void) => f({ context: ctx, mediaSize: { width: 330, height: 300 } }) } as never);
    expect(engine.bandAt(200, y(125))).toMatchObject({ id: "sd-15m-abc", low: 120, high: 130 });
    expect(engine.bandAt(200, y(125))?.tip?.[0]).toEqual(["Formation", "DBR (drop, base, rally)"]);
    expect(engine.bandAt(5, y(125))).toBeNull();                                 // left of the base start (x = 10)
  });

  it("the line under the chart: hidden violated zones and timeframes without bars, never a price", () => {
    const r = resp([zone()]);
    r.timeframes[0].hidden_violated = 2;
    expect(sdNote(r, false)).toBe("Supply & Demand zones: 2 violated hidden.");
    expect(sdNote(resp([], { timeframes: [{ ...r.timeframes[0], zones: [], hidden_violated: 0, reason: "no closed bars" }] }), false)).toContain("no closed bars yet on 15m");
    expect(sdNote(null, true)).toContain("could not be loaded");
    expect(sdNote(resp([zone()]), false)).toBeNull();
  });
});

describe("timeframe choice", () => {
  it("M98 T1: the zones follow the chart's timeframe; chips add only higher ones; the chart's own is always drawn", () => {
    expect(sdTimeframesFor("60min")).toEqual(["2h", "1d", "1w", "1M"]);
    expect(sdTimeframesFor("15min")).toEqual(["30m", "1h", "2h", "1d", "1w", "1M"]);
    expect(sdTimeframesFor("month")).toEqual([]);
    // nothing chosen: the chart's own; switching the chart 5m -> 15m -> 1H switches the zones with it
    expect(["5min", "15min", "60min"].map((tf) => shownSdTimeframes(null, tf as never))).toEqual([["5m"], ["15m"], ["1h"]]);
    // 1H + D chips on a 15m chart; a stored lower one (10m) or the old chart timeframe is not drawn
    expect(shownSdTimeframes(["1d", "10m", "1h"], "15min")).toEqual(["15m", "1h", "1d"]);
    expect(shownSdTimeframes(["15m", "1h"], "5min")).toEqual(["5m", "15m", "1h"]);
    expect(shownSdTimeframes(["15m", "1h"], "60min")).toEqual(["1h"]);
    expect(shownSdTimeframes(["7m"], "15min")).toEqual(["15m"]);
    expect(shownSdTimeframes(["30m", "1h", "2h", "1d"], "15min")).toEqual(["15m", "30m", "1h", "2h"]);   // at most four in all
    // the toggle works on the stored choice: the chart's own is never toggled, a chip not drawn here stays stored
    expect(toggleSdTimeframe(null, "1d", "15min")).toEqual(["1d"]);
    expect(toggleSdTimeframe(["1h", "1d"], "1h", "15min")).toEqual(["1d"]);
    expect(toggleSdTimeframe(["1d"], "15m", "15min")).toEqual(["1d"]);
    expect(toggleSdTimeframe(["1h"], "1d", "60min")).toEqual(["1h", "1d"]);            // 1H is the chart's own now, kept for a lower chart
    expect(shownSdTimeframes(["1h", "1d"], "15min")).toEqual(["15m", "1h", "1d"]);
    expect(toggleSdTimeframe(["30m", "1h", "1d"], "1w", "15min")).toEqual(["30m", "1h", "1w"]);         // four drawn: the newest replaces the highest
    expect(toggleSdTimeframe(["5m", "10m", "1h", "1d"], "1w", "15min")).toEqual(["10m", "1h", "1d", "1w"]);  // a not-drawn one leaves the store first
  });

  it("M98 T1: each timeframe's bands get their own opacity - the chart's own strongest, higher ones fainter", () => {
    const rows = (tf: SdZone["timeframe"], id: string) => ({ timeframe: tf, bars: 9, as_of_bar: 1, zones: [zone({ id, timeframe: tf })], more: 0, hidden_violated: 0, source: null, reason: null });
    const data = toSdLayer(resp([], { timeframes: [rows("15m", "a"), rows("1h", "b"), rows("1d", "c")] }));
    expect(data?.bands?.map((b) => b.opacity)).toEqual([SD_TF_OPACITY[0], SD_TF_OPACITY[1], SD_TF_OPACITY[2]]);
    expect(new Set(SD_TF_OPACITY).size).toBe(SD_TF_OPACITY.length);
  });

  it("the options bar: the chart's own timeframe fixed, one chip per higher timeframe (pressed when shown), Show violated off", () => {
    const html = renderToStaticMarkup(<SdZonesBar chartTf="15min" shown={["15m", "1h", "1d"]} options={{ timeframes: ["1h", "1d"], showViolated: false }} onChange={() => {}} note={null} />);
    expect(html).toContain('data-sd-own="15m"');
    expect(html.match(/data-sd-tf=/g)).toHaveLength(6);
    expect(html).not.toContain('data-sd-tf="15m"');
    expect(html).not.toContain('data-sd-tf="5m"');
    expect(html).toMatch(/aria-pressed="true" data-sd-tf="1h"/);
    expect(html).toMatch(/aria-pressed="false" data-sd-tf="2h"/);
    expect(html).toContain("Show violated");
    expect(html).not.toMatch(/data-sd-violated[^>]*checked/);
  });

  it("the Layers menu lists the layer behind sd_pack", () => {
    expect(MENU_ITEMS.find((i) => i.id === SD_LAYER_ID)).toMatchObject({ group: "layers", label: "Supply & Demand zones", flag: "sd_pack" });
  });
});

describe("10 min and 2 hours on the chart (M97-Q4)", () => {
  it("are chart timeframes, offered only when the server lists them", () => {
    expect(TIMEFRAMES).toEqual(["1min", "3min", "5min", "10min", "15min", "30min", "60min", "120min", "day", "week", "month"]);
    expect(offeredTimeframes(null)).not.toContain("10min");
    expect(offeredTimeframes(null)).not.toContain("120min");
    expect(offeredTimeframes(["10min", "120min", "week"])).toEqual(["1min", "3min", "5min", "10min", "15min", "30min", "60min", "120min", "day", "week"]);
    expect([timeframeShort("10min"), timeframeShort("120min")]).toEqual(["10m", "2H"]);
  });

  it("the custom interval and the served list name them in words", () => {
    const all = offeredTimeframes(["10min", "120min", "week", "month"]);
    expect(parseCustomInterval("2", "hours", all)).toEqual({ ok: true, id: "120min" });
    expect(parseCustomInterval("10", "minutes", all)).toEqual({ ok: true, id: "10min" });
    expect(servedText(all)).toBe("1, 3, 5, 10, 15 and 30 minutes, 1 and 2 hours, daily, weekly and monthly");
    expect(servedText(offeredTimeframes(null))).toBe("1, 3, 5, 15 and 30 minutes, 1 hour and daily");
  });
});
