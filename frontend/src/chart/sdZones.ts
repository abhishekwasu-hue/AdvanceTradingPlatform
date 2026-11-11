/**
 * M97 PR-4 (docs/design/M97_SUPPLY_DEMAND.md, Z3): the "Supply & Demand zones" layer on the v2 chart - a Layers item in
 * the Indicators menu (flag `sd_pack`), off until the user turns it on.
 *
 * The server (`GET /api/charts/layers/sd-zones`) finds the zones on each chosen timeframe's closed bars (a higher
 * timeframe on a lower-timeframe chart too) and sends every number; this file only draws them:
 * - each zone a TIME-ANCHORED filled band from its base start, extended right, in the theme's buyer / seller colour;
 * - its proximal (solid) and distal (dashed) lines as segments from the base start, extended right;
 * - a label "Demand · DBR · Fresh · D" through the W-1 label placer;
 * - a tap opens the band tooltip (formation, state, proximal / distal, the leg / base numbers) and highlights the
 *   leg-in, base and leg-out candles (a second layer, `SD_FOCUS_ID`).
 * Reference areas from past prices only: nothing here places, changes or proposes an order.
 */
import { useEffect, useMemo, useState } from "react";
import { request } from "../api/client";
import type { BandRef } from "../charting/geometry";
import type { ChartEngine, LayerBand, LayerData, LayerMark, LayerSegment } from "../charting/engine";
import { formatNumber } from "../ui/format";
import { SD_MAX_TIMEFRAMES, SD_TIMEFRAMES, type SdOverlayPrefs, type SdTimeframe } from "./sdStyles";
import type { TimeframeId } from "./timeframes";

export { SD_MAX_TIMEFRAMES, SD_TIMEFRAMES, type SdTimeframe };

export const SD_LAYER_ID = "sd_zones";
export const SD_FOCUS_ID = "sd_zones_focus";
export const SD_LABEL = "Supply & Demand zones";

/** The chips' short names of the layer's timeframes (the server's names, finest first: sdStyles.ts SD_TIMEFRAMES). */
export const SD_TF_SHORT: Record<SdTimeframe, string> = {
  "1m": "1m", "3m": "3m", "5m": "5m", "10m": "10m", "15m": "15m", "30m": "30m", "1h": "1H", "2h": "2H", "1d": "D", "1w": "W", "1M": "M",
};

const OF_CHART: Record<TimeframeId, SdTimeframe> = {
  "1min": "1m", "3min": "3m", "5min": "5m", "10min": "10m", "15min": "15m", "30min": "30m", "60min": "1h", "120min": "2h",
  day: "1d", week: "1w", month: "1M",
};
/** The chart's timeframe as the layer names it. */
export const sdTimeframeOf = (tf: TimeframeId): SdTimeframe => OF_CHART[tf];

/** M98 T1: the chips a chart at `tf` offers - only the timeframes HIGHER than the chart's. The chart's own timeframe is
 * always drawn (its zones follow the chart: 5m -> 15m -> 1H recomputes them) and is not a chip. */
export function sdTimeframesFor(tf: TimeframeId): SdTimeframe[] {
  const own = SD_TIMEFRAMES.indexOf(sdTimeframeOf(tf));
  return SD_TIMEFRAMES.filter((_, i) => i > own);
}

/** M98 T1: what is drawn - the chart's own timeframe, then the user's extra chips that are higher than it (a stored
 * lower one, e.g. the chart was switched up, is not drawn), finest first, at most SD_MAX_TIMEFRAMES in all. */
export function shownSdTimeframes(chosen: readonly string[] | null | undefined, tf: TimeframeId): SdTimeframe[] {
  const higher = sdTimeframesFor(tf).filter((t) => chosen?.includes(t));
  return [sdTimeframeOf(tf), ...higher].slice(0, SD_MAX_TIMEFRAMES);
}

/** M98 T1: a band's fill per timeframe - the chart's own strongest, each higher one fainter (rank in the drawn list). */
export const SD_TF_OPACITY = [0.16, 0.1, 0.07, 0.05] as const;
export const sdOpacity = (rank: number, violated: boolean) => (SD_TF_OPACITY[Math.min(rank, SD_TF_OPACITY.length - 1)] ?? 0.05) * (violated ? 0.4 : 1);

export interface SdCandle { time: number; end?: number | null; high: number | null; low: number | null; range: number | null; body_pct: number | null; mult: number | null }
export interface SdZone {
  id: string; timeframe: SdTimeframe; side: "demand" | "supply"; formation: "DBR" | "RBR" | "RBD" | "DBD"; state: "fresh" | "tested" | "violated";
  tests: number; proximal: number; distal: number; base_start: number; base_end: number; formed: number; violated_at: number | null;
  leg_in: SdCandle;
  base: { candles: number; times: number[]; high: number | null; low: number | null; max_body_pct: number | null; max_mult: number | null; bars: SdCandle[] };
  leg_out: SdCandle & { of: string };
  label: string;
}
export interface SdTimeframeRows {
  timeframe: SdTimeframe; bars: number; as_of_bar: number | null; zones: SdZone[]; more: number; hidden_violated: number;
  source: string | null; reason: string | null;
}
export interface SdZonesResponse {
  schema: "sd-layer/1"; symbol: string; at: string; note: string; include_violated: boolean; settings: Record<string, number | string | boolean>;
  settings_note: string; timeframes: SdTimeframeRows[];
}

export const sdZonesApi = {
  layer: (symbol: string, timeframes: readonly SdTimeframe[], includeViolated: boolean, at: string | null = null) => {
    const q = new URLSearchParams({ symbol, timeframes: timeframes.join(",") });
    if (includeViolated) q.set("include_violated", "1");
    if (at) q.set("at", at);
    return request<SdZonesResponse>(`/charts/layers/sd-zones?${q.toString()}`);
  },
};

const FORMATION_WORDS: Record<SdZone["formation"], string> = { DBR: "drop, base, rally", RBR: "rally, base, rally", RBD: "rally, base, drop", DBD: "drop, base, drop" };
const price = (v: number | null | undefined) => (v == null ? "–" : formatNumber(v, 2));
const times = (v: number | null | undefined) => (v == null ? "–" : `${formatNumber(v, 1)}×`);
const pct = (v: number | null | undefined) => (v == null ? "–" : `${formatNumber(v, 0)}%`);
export const stateText = (z: Pick<SdZone, "state" | "tests">) => (z.state === "tested" ? `Tested ${z.tests}` : z.state === "fresh" ? "Fresh" : "Violated");

/** The tooltip rows of a zone: what it is, its lines, then the numbers each candle was judged by. */
export function sdTipRows(z: SdZone): [string, string][] {
  return [
    ["Formation", `${z.formation} (${FORMATION_WORDS[z.formation]})`],
    ["State", stateText(z)],
    ["Proximal", price(z.proximal)],
    ["Distal", price(z.distal)],
    // range · multiple of the average range before it · body % of the range (short enough for one line on a phone)
    ["Leg-in", `${price(z.leg_in.range)} · ${times(z.leg_in.mult)} avg · body ${pct(z.leg_in.body_pct)}`],
    ["Base", `${z.base.candles} ${z.base.candles === 1 ? "candle" : "candles"} · ≤ ${times(z.base.max_mult)} avg · body ≤ ${pct(z.base.max_body_pct)}`],
    ["Leg-out", `${price(z.leg_out.range)} · ${times(z.leg_out.mult)} ${z.leg_out.of === "ATR" ? "ATR" : "avg"} · body ${pct(z.leg_out.body_pct)}`],
  ];
}

/** The layer: per zone a time-anchored band (violated: faint and dashed), its proximal / distal lines, its label. */
export function toSdLayer(resp: SdZonesResponse): LayerData | null {
  const bands: LayerBand[] = [];
  const segments: LayerSegment[] = [];
  const order = [...resp.timeframes].sort((a, b) => SD_TIMEFRAMES.indexOf(a.timeframe) - SD_TIMEFRAMES.indexOf(b.timeframe));
  for (const rows of resp.timeframes) {
    const rank = order.indexOf(rows);                      // M98 T1: 0 = the chart's own (the finest drawn)
    for (const z of rows.zones) {
      if (!Number.isFinite(z.proximal) || !Number.isFinite(z.distal)) continue;
      const violated = z.state === "violated";
      bands.push({ low: Math.min(z.proximal, z.distal), high: Math.max(z.proximal, z.distal), start: z.base_start, role: z.side,
                   opacity: sdOpacity(rank, violated), style: violated ? "dashed" : "solid", label: z.label, id: z.id, tip: sdTipRows(z) });
      segments.push({ t1: z.base_start, p1: z.proximal, t2: z.base_start, p2: z.proximal, extend: "right", role: z.side, width: 1, style: violated ? "dashed" : "solid" },
                    { t1: z.base_start, p1: z.distal, t2: z.base_start, p2: z.distal, extend: "right", role: z.side, width: 1, style: "dashed" });
    }
  }
  return bands.length ? { asOf: resp.at, bands, segments } : null;
}

/** M98 F1: a tap's highlight - one outlined box per part around exactly its candles: the leg-in candle, the base (all
 * its candles in one box, from the first one's start to the last one's end) and the leg-out candle, each named once.
 * Times are the candles' own bar starts from the server, so the box sits on the chart bars of that candle (one bar on
 * the zone's own timeframe, every lower-timeframe bar inside a higher-timeframe candle). A leg's name goes on its far
 * side from the base (a drop into a demand base: above it); the base's under a demand base, over a supply base. */
export function sdFocusLayer(z: SdZone): LayerData {
  const marks: LayerMark[] = [];
  const bars = z.base.bars.filter((b) => b.high != null && b.low != null);
  const baseHigh = bars.length ? Math.max(...bars.map((b) => b.high as number)) : null;
  const baseLow = bars.length ? Math.min(...bars.map((b) => b.low as number)) : null;
  const leg = (c: SdCandle, text: string) => {
    if (c.high == null || c.low == null) return;
    const up = baseHigh == null || baseLow == null ? z.side === "demand" : c.high - baseHigh >= baseLow - c.low;
    marks.push({ time: c.time, ...(c.end != null ? { end: c.end } : {}), low: c.low, high: c.high, text, position: up ? "above" : "below", role: z.side });
  };
  leg(z.leg_in, "Leg-in");
  if (bars.length && baseHigh != null && baseLow != null) {
    const lastEnd = bars[bars.length - 1].end;
    marks.push({ time: bars[0].time, ...(lastEnd != null ? { end: lastEnd } : {}), low: baseLow, high: baseHigh, text: "Base",
                 position: z.side === "demand" ? "below" : "above", role: z.side });
  }
  leg(z.leg_out, "Leg-out");
  return { marks };
}

/** The highlight for the band whose tooltip is open: that zone's candles when the band is one of these zones (by its
 * id), else nothing (another layer's band, or no tooltip). */
export function sdFocusFor(zones: readonly SdZone[], focus: Pick<BandRef, "id"> | null): LayerData | null {
  const z = focus?.id ? zones.find((x) => x.id === focus.id) : undefined;
  return z ? sdFocusLayer(z) : null;
}

/** The line under the chart while the layer is on: a read problem, or how many zones are hidden (never zone numbers). */
export function sdNote(resp: SdZonesResponse | null, failed: boolean): string | null {
  if (failed) return `${SD_LABEL} could not be loaded. Turn it off and on again to retry.`;
  if (!resp) return null;
  const empty = resp.timeframes.filter((t) => t.reason).map((t) => SD_TF_SHORT[t.timeframe] ?? t.timeframe);
  const hidden = resp.timeframes.reduce((n, t) => n + t.hidden_violated, 0);
  const zones = resp.timeframes.reduce((n, t) => n + t.zones.length, 0);
  const parts: string[] = [];
  if (empty.length) parts.push(`no closed bars yet on ${empty.join(", ")}`);
  if (!zones && !empty.length) parts.push("no zones on the chosen timeframes");
  if (hidden && !resp.include_violated) parts.push(`${hidden} violated hidden`);
  return parts.length ? `${SD_LABEL}: ${parts.join(" · ")}.` : null;
}

// --- the user's options (M97 PR-5: the server prefs, `ChartPrefs.sd_overlay`; sdStyles.ts) ----------------------------------

/** M98 T1: one extra chip on or off. Works on the user's stored choice (`chosen`), so a chip that is not drawn on this
 * chart (lower than or equal to its timeframe) stays stored and comes back on a lower chart; the chart's own timeframe
 * is never toggled (it is always drawn). Adding a chip when the chart already draws SD_MAX_TIMEFRAMES timeframes
 * replaces the highest one drawn; the stored list keeps at most SD_MAX_TIMEFRAMES, dropping a not-drawn one first.
 * Finest first. Pure. */
export function toggleSdTimeframe(chosen: readonly SdTimeframe[] | null | undefined, t: SdTimeframe, tf: TimeframeId): SdTimeframe[] {
  const own = sdTimeframeOf(tf);
  const base = SD_TIMEFRAMES.filter((x) => chosen?.includes(x));
  const shown = shownSdTimeframes(base, tf);
  if (t === own) return base;
  if (shown.includes(t)) return base.filter((x) => x !== t);
  const extras = shown.filter((x) => x !== own);
  const full = extras.length >= SD_MAX_TIMEFRAMES - 1 ? extras[extras.length - 1] : null;
  let next = SD_TIMEFRAMES.filter((x) => x === t || (base.includes(x) && x !== full));
  while (next.length > SD_MAX_TIMEFRAMES) {
    const drop = next.find((x) => x !== t && !shown.includes(x)) ?? next.find((x) => x !== t);
    next = next.filter((x) => x !== drop);
  }
  return next;
}

export interface SdZonesState { note: string | null; shown: SdTimeframe[]; zones: SdZone[] }

/** Reads and draws the layer while `on` (again when a new bar arrives) with the user's overlay options (his server
 * prefs); `focus` is the tapped band (its candles are highlighted while its tooltip is open). */
export function useSdZones(engine: ChartEngine | null, symbol: string, tf: TimeframeId, on: boolean, newestBar: number | null,
                           focus: BandRef | null, options: SdOverlayPrefs): SdZonesState {
  const [resp, setResp] = useState<SdZonesResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const shown = useMemo(() => shownSdTimeframes(options.timeframes, tf), [options.timeframes, tf]);
  const key = shown.join(",");
  useEffect(() => {
    let live = true;
    setFailed(false);
    if (!on || !symbol) { setResp(null); return () => { live = false; }; }
    sdZonesApi.layer(symbol, shown, options.show_violated).then((r) => { if (live) setResp(r); },
      () => { if (live) { setResp(null); setFailed(true); } });
    return () => { live = false; };
  }, [on, symbol, key, options.show_violated, newestBar]);  // eslint-disable-line react-hooks/exhaustive-deps
  const data = useMemo(() => (on && resp ? toSdLayer(resp) : null), [on, resp]);
  useEffect(() => {
    if (!engine) return undefined;
    engine.setLayer(SD_LAYER_ID, data);
    return () => engine.setLayer(SD_LAYER_ID, null);
  }, [engine, data]);
  const zones = useMemo(() => (on && resp ? resp.timeframes.flatMap((t) => t.zones) : []), [on, resp]);
  const focused = focus?.id ? zones.find((z) => z.id === focus.id) ?? null : null;
  useEffect(() => {
    if (!engine) return undefined;
    engine.setLayer(SD_FOCUS_ID, sdFocusFor(focused ? [focused] : [], focus));
    return () => engine.setLayer(SD_FOCUS_ID, null);
  }, [engine, focused]);  // eslint-disable-line react-hooks/exhaustive-deps
  return { note: on ? sdNote(resp, failed) : null, shown, zones };
}
