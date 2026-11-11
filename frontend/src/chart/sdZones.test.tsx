/**
 * M97 PR-4: the "Supply & Demand zones" layer - time-anchored bands and right-extended lines (pure geometry, existing
 * bands unchanged), the labels through the W-1 placer, the tap (hit-test with the band's id and rows, the tooltip rows,
 * the highlighted candles), the timeframe choice and the 10 min / 2 hours chart timeframes.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { LABEL_PAD, bandLeft, bandShapes, segmentShapes, type Converters, type Shape } from "../charting/geometry";
import { LightweightEngine } from "../charting/lightweight";
import type { LayerPrimitive } from "../charting/primitives";
import { BandTip } from "./ChartV2";
import { MENU_ITEMS } from "./indicatorsModel";
import { offeredTimeframes } from "./chartsPage";
import {
  SD_FOCUS_ID, SD_LAYER_ID, sdFocusFor, sdFocusLayer, sdNote, sdTimeframesFor, sdTipRows, shownSdTimeframes, toSdLayer, toggleSdTimeframe,
  type SdZone, type SdZonesResponse,
} from "./sdZones";
import { SdZonesBar } from "./SdZonesBar";
import { TIMEFRAMES, parseCustomInterval, servedText, timeframeShort } from "./timeframes";

/** A pane: x = time (seconds) - 1000 px, price 100 at the bottom, 4 px per rupee. */
const pane = (width = 400, height = 300): Converters => ({ x: (t) => t - 1000, y: (p) => height - (p - 100) * 4, width, height });
const rects = (list: Shape[]) => list.filter((s): s is Extract<Shape, { type: "rect" }> => s.type === "rect");
const chips = (list: Shape[]) => list.filter((s): s is Extract<Shape, { type: "chip" }> => s.type === "chip");

const candle = (time: number, low: number, high: number) => ({ time, low, high, range: high - low, body_pct: 80, mult: 1.8 });
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

  it("a tap on a zone highlights its leg-in, base and leg-out candles; another band highlights nothing", () => {
    const z = zone();
    const focus = sdFocusFor([z], { id: z.id });
    expect(focus?.segments?.map((s) => s.t1)).toEqual([1090, 1100, 1110, 1120]);
    expect(focus?.segments?.[0]).toMatchObject({ p1: 125, p2: 140, role: "demand", width: 4 });
    expect(focus?.labels?.map((l) => l.text)).toEqual(["Leg-in", "Base", "Leg-out"]);
    expect(sdFocusFor([z], { id: "other" })).toBeNull();
    expect(sdFocusFor([z], null)).toBeNull();
    expect(sdFocusLayer({ ...z, side: "supply" }).labels?.[0].position).toBe("above");
    expect([SD_LAYER_ID, SD_FOCUS_ID]).toEqual(["sd_zones", "sd_zones_focus"]);
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
  it("a chart offers its own timeframe and higher ones; the choice is cut to them, at most four", () => {
    expect(sdTimeframesFor("60min")).toEqual(["1h", "2h", "1d", "1w", "1M"]);
    expect(sdTimeframesFor("10min")[0]).toBe("10m");
    expect(shownSdTimeframes(null, "15min")).toEqual(["15m"]);
    expect(shownSdTimeframes(["5m", "1h", "1d"], "15min")).toEqual(["1h", "1d"]);          // a lower one is not shown
    expect(shownSdTimeframes(["5m"], "15min")).toEqual(["15m"]);
    expect(shownSdTimeframes(["15m", "30m", "1h", "2h", "1d"], "15min")).toHaveLength(4);
    expect(toggleSdTimeframe(["15m"], "1d")).toEqual(["15m", "1d"]);
    expect(toggleSdTimeframe(["15m", "1d"], "15m")).toEqual(["1d"]);
    expect(toggleSdTimeframe(["1d"], "1d")).toEqual(["1d"]);                                  // never none
    expect(toggleSdTimeframe(["15m", "30m", "1h", "1d"], "1w")).toHaveLength(4);
  });

  it("the options bar: one chip per offered timeframe (pressed when shown) and Show violated off by default", () => {
    const html = renderToStaticMarkup(<SdZonesBar chartTf="60min" shown={["1h", "1d"]} options={{ timeframes: ["1h", "1d"], showViolated: false }} onChange={() => {}} note={null} />);
    expect(html.match(/data-sd-tf=/g)).toHaveLength(5);
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
