import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import golden from "./__fixtures__/pa_layer_golden.json";
import { labelShapes, layerShapes, segmentShapes, type Converters } from "./geometry";
import { PriceActionPanel } from "./PriceActionLayer";
import { ApiError } from "../api/client";
import {
  CONCEPTS, DEFAULT_TOGGLES, LayerLoader, PA_COLORS, barEndIso, layerTf, loadToggles, paApi, saveToggles, toLayerData, trendRows,
  type PaLayerResponse, type Toggles,
} from "./priceAction";
import { REPLAY_OFF, parseCandleTime, replayAt, startReplay, stepReplay, visibleCandles, windowStart } from "./replay";
import { ReplayBar } from "./ReplayBar";

// __fixtures__/pa_layer_golden.json is a byte-identical copy of backend/tests/fixtures/charts/pa_layer_golden.json
// (checked by backend/tests/test_ch3_price_action_layer.py; rebuild both with `python -m tests.ch3_pa_cases --write`)
const layer = { ...(golden as unknown as { default: PaLayerResponse }).default, instrument: "NSE:X", at: "", start: "", source: "lake:15m",
                note: "reference", reason: null };
const only = (c: keyof Toggles): Toggles => ({ ...Object.fromEntries(CONCEPTS.map((k) => [k, false])), [c]: true } as Toggles);
const CH3A: Toggles = { ...only("swings"), legs: true, breaks: true, levels: true, trend: true };                 // the CH3a concepts
const NONE: Toggles = Object.fromEntries(CONCEPTS.map((k) => [k, false])) as Toggles;

describe("price-action layer data (CH3)", () => {
  it("draws every swing, leg, break and live level of the golden layer, with the backend's numbers and texts", () => {
    const d = toLayerData(layer, CH3A)!;
    expect(layer.swings.length).toBeGreaterThan(3);
    expect(d.asOf).toBe(layer.as_of);
    expect(d.labels!.length).toBe(layer.swings.length + layer.breaks.length);
    expect(d.segments!.length).toBe(layer.legs.length + layer.breaks.length);
    expect(d.lines!.map((l) => [l.price, l.title])).toEqual(layer.levels.map((lv) => [lv.price, lv.text]));
    const swingLabels = d.labels!.slice(0, layer.swings.length);
    expect(swingLabels.map((l) => [l.time, l.price, l.text])).toEqual(layer.swings.map((s) => [s.time, s.price, s.label]));
    expect(swingLabels.every((l, i) => l.position === (layer.swings[i].kind === "H" ? "above" : "below"))).toBe(true);
    const legs = d.segments!.slice(0, layer.legs.length);
    expect(legs.map((g) => [g.t1, g.p1, g.t2, g.p2])).toEqual(layer.legs.map((g) => [g.from.time, g.from.price, g.to.time, g.to.price]));
    expect(legs.every((g, i) => g.color === PA_COLORS[layer.legs[i].direction]
                                && g.label === (layer.legs[i].role === "impulse" ? layer.legs[i].text : undefined)
                                && g.width === (layer.legs[i].role === "impulse" ? 2 : 1))).toBe(true);
    const b = layer.breaks[0];
    expect(d.segments).toContainEqual({ t1: b.pivot_time, p1: b.level, t2: b.time, p2: b.level, color: b.side === "above" ? PA_COLORS.up : PA_COLORS.down, style: "dotted" });
    expect(d.labels!.some((l) => l.text.endsWith(b.level_text) && l.time === b.time)).toBe(true);
  });

  it("each toggle shows only its own concept; all off draws nothing; trend is the panel's", () => {
    expect(toLayerData(layer, only("swings"))).toMatchObject({ segments: [], lines: [] });
    expect(toLayerData(layer, only("legs"))!.labels).toEqual([]);
    expect(toLayerData(layer, only("levels"))!.segments).toEqual([]);
    expect(toLayerData(layer, only("breaks"))!.lines).toEqual([]);
    expect(toLayerData(layer, only("trend"))).toBeNull();
    expect(toLayerData(layer, NONE)).toBeNull();
  });

  it("debug: degree tags, unconfirmed swings marked, other degrees fainter, leg texts, broken levels dotted", () => {
    const dbg: PaLayerResponse = {
      ...layer, debug: true,
      swings: [{ ...layer.swings[0], degree: 0 }, { ...layer.swings[1], status: "tentative", confirmed_time: null, confirmed_at: null }],
      legs: [{ ...layer.legs[0], status: "tentative" }],
      levels: [{ ...layer.levels[0], status: "broken" }],
    };
    const d = toLayerData(dbg, CH3A)!;
    expect(d.labels![0].text).toBe(`D0 ${layer.swings[0].label}`);
    expect(d.labels![0].color).toMatch(/^#[0-9a-f]{6}99$/);                      // degree 0 is not the chosen degree
    expect(d.labels![1].text).toBe(`D${layer.degree} ${layer.swings[1].label}?`);
    expect(d.segments![0]).toMatchObject({ style: "dashed", label: layer.legs[0].text });
    expect(d.lines![0].style).toBe("dotted");
  });

  it("the trend panel lists the top degree first and marks the chosen one", () => {
    expect(trendRows(layer).map((t) => t.degree)).toEqual([3, 2, 1, 0]);
    const set = vi.fn();
    const html = renderToStaticMarkup(createElement(PriceActionPanel, { layer: { ...layer, debug: true }, toggles: { ...DEFAULT_TOGGLES, legs: false }, debug: true,
                                                                     onToggle: set, onDebug: set }));
    expect(html).toMatch(/aria-pressed="false"[^>]*>Legs</);
    expect(html).toMatch(/aria-pressed="true"[^>]*>Debug</);
    expect(html.indexOf(">D3 ")).toBeLessThan(html.indexOf(">D0 "));
    expect(html).toContain(`${layer.bars} bars · lake:15m`);
    expect(html).toContain("price-action-debug");
    const noTrend = renderToStaticMarkup(createElement(PriceActionPanel, { layer, toggles: { ...DEFAULT_TOGGLES, trend: false }, debug: false, onToggle: set, onDebug: set }));
    expect(noTrend).not.toContain("data-trend");
  });
});

describe("replay as of each bar (CH3)", () => {
  it("a bar's as-of time is its end - the backend golden's as_of is the end of its last bar", () => {
    expect(layerTf("5min")).toBe("5m");
    expect(layerTf("60min")).toBe("1h");
    expect(layerTf("day")).toBe("1d");
    expect(layerTf("week")).toBeNull();
    expect(barEndIso(layer.last_bar!, "15m")).toBe(new Date(layer.as_of!).toISOString());
  });

  it("the chart shows no bar after the replay bar, and steps stay inside the loaded bars", () => {
    const candles = Array.from({ length: 10 }, (_, i) => ({ timestamp: new Date((1_000_000 + i * 900) * 1000).toISOString(), close: i }));
    expect(visibleCandles(candles, REPLAY_OFF)).toBe(candles);
    let s = startReplay(candles.length, 3);
    expect(s).toEqual({ active: true, index: 6, playing: false, start: null });
    expect(visibleCandles(candles, s).map((c) => c.close)).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(replayAt(candles, s, "15m")).toBe(barEndIso(1_000_000 + 6 * 900, "15m"));
    expect(replayAt(candles, REPLAY_OFF, "15m")).toBeNull();
    s = stepReplay({ ...s, playing: true }, 5, candles.length);
    expect(s).toMatchObject({ index: 9, playing: false });                       // the newest bar stops playing
    expect(stepReplay(s, -20, candles.length).index).toBe(0);
    expect(startReplay(0).active).toBe(false);
    for (let i = 0; i < candles.length; i++) {
      const st = { ...s, index: i };
      const shown = visibleCandles(candles, st);
      expect(shown[shown.length - 1]).toBe(candles[i]);
      expect(Date.parse(shown[shown.length - 1].timestamp)).toBeLessThan(Date.parse(replayAt(candles, st, "15m")!));
    }
  });

  it("the replay bar shows its position and the as-of time; off it offers Replay", () => {
    const on = renderToStaticMarkup(createElement(ReplayBar, { state: { active: true, index: 4, playing: false, start: null }, count: 10,
                                                              at: "2026-03-02T04:00:00.000Z", onChange: () => {} }));
    expect(on).toContain("bar 5 / 10 · layers as of 02 Mar, 09:30 IST");
    expect(on).toContain("Exit replay");
    expect(renderToStaticMarkup(createElement(ReplayBar, { state: REPLAY_OFF, count: 10, at: null, onChange: () => {} }))).toContain(">Replay<");
  });
});

describe("layer geometry and plumbing (CH3)", () => {
  const c: Converters = { x: (t) => (t >= 0 ? t / 10 : null), y: (p) => 500 - p, width: 400, height: 300 };

  it("segments and labels are placed by data; an unplaceable end drops the shape", () => {
    expect(segmentShapes([{ t1: 0, p1: 100, t2: 100, p2: 200, label: "+1.00% · 3 bars", width: 9 }], c)).toEqual([
      { type: "segment", x1: 0, y1: 400, x2: 10, y2: 300, color: "#2962ff", width: 4, dash: "solid" },
      { type: "text", x: 5, y: 347, text: "+1.00% · 3 bars", color: "#2962ff", align: "center", baseline: "bottom" },
    ]);
    expect(segmentShapes([{ t1: -1, p1: 1, t2: 1, p2: 1 }], c)).toEqual([]);
    expect(labelShapes([{ time: 50, price: 100, text: "LL", position: "below", color: "#0369a1" }, { time: -5, price: 1, text: "x" }, { time: 5, price: 1, text: "" }], c))
      .toEqual([{ type: "text", x: 5, y: 406, text: "LL", color: "#0369a1", align: "center", baseline: "top" }]);
    const all = layerShapes({ bands: [{ low: 100, high: 100 }], segments: [{ t1: 0, p1: 1, t2: 10, p2: 2 }], labels: [{ time: 0, price: 1, text: "H" }] }, c);
    expect(all.map((s) => s.type)).toEqual(["segment", "segment", "text"]);       // band first (underneath), labels last
  });

  it("toggles are remembered per browser, and a broken storage gives the defaults", () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
    saveToggles({ ...DEFAULT_TOGGLES, legs: false }, storage);
    expect(loadToggles(storage)).toEqual({ ...DEFAULT_TOGGLES, legs: false });
    store.set("atp.chart.priceAction.toggles", "{not json");
    expect(loadToggles(storage)).toEqual(DEFAULT_TOGGLES);
    expect(loadToggles({ getItem: () => { throw new Error("blocked"); } })).toEqual(DEFAULT_TOGGLES);
    expect(() => saveToggles(DEFAULT_TOGGLES, { setItem: () => { throw new Error("quota"); } })).not.toThrow();
  });

  afterEach(() => vi.unstubAllGlobals());

  it("asks for the layer as of a time with a fixed start, and debug only when on", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(layer), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await paApi.layer("NSE:BANKNIFTY", { tf: "15m", at: "2026-03-02T04:00:00.000Z", start: "2026-02-01T00:00:00+00:00", debug: true });
    await paApi.layer("NSE:BANKNIFTY", { tf: "5m" });
    const urls = (fetchMock.mock.calls as unknown as [string][]).map(([u]) => decodeURIComponent(String(u)));
    expect(urls[0]).toContain("/charts/layers/price-action?instrument=NSE:BANKNIFTY&tf=15m&at=2026-03-02T04:00:00.000Z&start=2026-02-01T00:00:00+00:00&debug=true");
    expect(urls[1]).toMatch(/price-action\?instrument=NSE:BANKNIFTY&tf=5m$/);
  });
});

describe("no stale layer in replay (CH3a review MED-2)", () => {
  const deferred = () => {
    let resolve!: (r: PaLayerResponse) => void;
    let reject!: (e: unknown) => void;
    const promise = new Promise<PaLayerResponse>((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  };
  const q = (at: string) => ({ instrument: "NSE:X", tf: "15m" as const, at, start: null, debug: false });

  it("each new step clears the layer at once; an older answer arriving late is dropped; a failure leaves no layer", async () => {
    const pending: ReturnType<typeof deferred>[] = [];
    const states: { layer: PaLayerResponse | null; failed: string | null; available: boolean | null }[] = [];
    const last = () => states[states.length - 1];
    const loader = new LayerLoader(() => { const d = deferred(); pending.push(d); return d.promise; }, (s) => states.push(s));
    loader.load(q("2026-03-02T05:00:00Z"));
    pending[0].resolve({ ...layer, as_of: "2026-03-02T05:00:00Z" });
    await Promise.resolve(); await Promise.resolve();
    expect(last().layer?.as_of).toBe("2026-03-02T05:00:00Z");
    loader.load(q("2026-03-02T04:45:00Z"));                                    // step back
    expect(last().layer).toBeNull();                                    // the bar-N layer is gone at once
    loader.load(q("2026-03-02T04:30:00Z"));                                    // and back again before the answer
    pending[1].resolve({ ...layer, as_of: "2026-03-02T04:45:00Z" });            // the superseded answer arrives
    await Promise.resolve(); await Promise.resolve();
    expect(last().layer).toBeNull();
    pending[2].reject(new ApiError(422, "`start` must be before `at`"));
    await Promise.resolve(); await Promise.resolve();
    expect(last()).toMatchObject({ layer: null, available: true });
    expect(last().failed).toContain("start");
    const cancel = loader.load(q("2026-03-02T04:15:00Z"));
    cancel();                                                                   // unmounted / superseded
    pending[3].resolve(layer);
    await Promise.resolve(); await Promise.resolve();
    expect(last().layer).toBeNull();
    loader.load(q("2026-03-02T04:00:00Z"));
    pending[4].reject(new ApiError(503, "off"));
    await Promise.resolve(); await Promise.resolve();
    expect(last()).toEqual({ layer: null, failed: null, available: false });
  });

  it("a replay never sends a start at or after its as-of time, and candle times without an offset are IST", () => {
    expect(windowStart("2026-03-02T05:00:00Z", "2026-02-01T00:00:00+00:00")).toBe("2026-02-01T00:00:00+00:00");
    expect(windowStart("2026-01-31T05:00:00Z", "2026-02-01T00:00:00+00:00")).toBeNull();     // stepped back before start
    expect(windowStart("2026-02-01T00:00:00Z", "2026-02-01T00:00:00+00:00")).toBeNull();
    expect(windowStart(null, "2026-02-01T00:00:00+00:00")).toBeNull();                       // live: no start
    expect(parseCandleTime("2026-03-02T09:15:00")).toBe(Date.parse("2026-03-02T03:45:00Z"));
    expect(parseCandleTime("2026-03-02 09:15:00")).toBe(Date.parse("2026-03-02T03:45:00Z"));
    expect(parseCandleTime("2026-03-02T09:15:00+05:30")).toBe(Date.parse("2026-03-02T03:45:00Z"));
    expect(parseCandleTime("2026-03-02T03:45:00Z")).toBe(Date.parse("2026-03-02T03:45:00Z"));
    expect(parseCandleTime("2026-03-02")).toBe(Date.parse("2026-03-01T18:30:00Z"));
    expect(replayAt([{ timestamp: "2026-03-02T09:15:00" }], { active: true, index: 0, playing: false, start: null }, "15m"))
      .toBe("2026-03-02T04:00:00.000Z");
  });
});

describe("CH3b concepts on the chart", () => {
  it("the golden layer carries classified legs, BOS / CHoCH, liquidity, trendlines with channels (from the backend)", () => {
    expect(layer.legs.every((g) => g.class && (g.role === "impulse" || g.role === "pullback") && g.text.startsWith(g.class_text!))).toBe(true);
    expect((layer.structure ?? []).length).toBeGreaterThan(0);
    expect(layer.structure_state?.state).toBeTruthy();
    expect((layer.trendlines ?? []).length).toBeGreaterThan(0);
    expect((layer.liquidity ?? []).length).toBeGreaterThan(0);
  });

  it("structure: a label per event at its price, a dotted run from the broken swing, the protected level as a line", () => {
    const d = toLayerData(layer, only("structure"))!;
    const ev = layer.structure!;
    expect(d.labels!.map((l) => [l.time, l.price, l.text])).toEqual(ev.map((e) => [e.time, e.price, e.text]));
    const runs = ev.filter((e) => e.swing_time != null && e.swing_time < e.time);
    expect(d.segments!.map((g) => [g.t1, g.t2, g.p1, g.style])).toEqual(runs.map((e) => [e.swing_time, e.time, e.price, "dotted"]));
    const st = layer.structure_state!;
    expect(d.lines!).toEqual(st.protected == null ? [] : [{ price: st.protected, title: st.protected_label, color: PA_COLORS.structure, style: "dashed" }]);
  });

  it("liquidity: pools as dotted lines labelled BSL / SSL, sweeps marked; trendlines: the line and its dashed channel", () => {
    const withSweep: PaLayerResponse = { ...layer, sweeps: [{ side: "buy", src: "PDH", price: 100, price_text: "100.00", time: 5, extreme: 101, text: "sweep of previous day high 100.00" }] };
    const liq = toLayerData(withSweep, only("liquidity"))!;
    expect(liq.lines!.map((l) => [l.price, l.title, l.style])).toEqual(layer.liquidity!.map((p) => [p.price, p.label, "dotted"]));
    expect(liq.lines!.every((l) => /BSL|SSL/.test(l.title ?? ""))).toBe(true);
    expect(liq.labels).toEqual([{ time: 5, price: 100, text: "⚡ sweep of previous day high 100.00", color: PA_COLORS.buySide, position: "above" }]);
    const tl = toLayerData(layer, only("trendlines"))!;
    const t = layer.trendlines![0];
    expect(tl.segments![0]).toMatchObject({ t1: t.a.time, p1: t.a.price, t2: t.end.time, p2: t.end.price, width: 2, label: t.text });
    expect(tl.segments![1]).toMatchObject({ t1: t.channel.from.time, p1: t.channel.from.price, t2: t.channel.to.time, style: "dashed" });
    expect(t.channel.from.price - t.a.price).toBeCloseTo(t.channel.offset, 4);
  });

  it("the top-down panel lists W, D, 1H, 15m with each timeframe's own state", () => {
    const mtf = [
      { tf: "1w", state: "UPTREND", direction: "up", protected: 1, protected_text: "1.00", last_event: null, bars: 50, as_of: "x", source: "lake:1d", text: "1w up" },
      { tf: "1d", state: "DOWNTREND_WEAK", direction: "down (weak)", protected: 2, protected_text: "2.00", last_event: null, bars: 300, as_of: "x", source: "lake:1d", text: "1d" },
      { tf: "1h", state: null, direction: "none", protected: null, protected_text: null, last_event: null, bars: 0, as_of: null, source: null, text: "1h no bars" },
      { tf: "15m", state: "RANGE", direction: "range", protected: null, protected_text: null, last_event: null, bars: 800, as_of: "x", source: "lake:15m", text: "15m range" },
    ];
    const html = renderToStaticMarkup(createElement(PriceActionPanel, { layer: { ...layer, mtf }, toggles: DEFAULT_TOGGLES, debug: false, onToggle: () => {}, onDebug: () => {} }));
    expect(html).toContain("price-action-topdown");
    for (const want of [">W up<", ">D down (weak)<", ">1H –<", ">15m range<"]) expect(html).toContain(want);
    expect(html.indexOf(">W up<")).toBeLessThan(html.indexOf(">15m range<"));
    expect(DEFAULT_TOGGLES.breaks).toBe(false);                                   // BOS / CHoCH name the breaks that matter
  });
});
