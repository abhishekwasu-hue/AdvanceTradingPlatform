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
import type { ChartEngine, LayerBand, LayerData, LayerLabel, LayerSegment } from "../charting/engine";
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

/** The timeframes a chart at `tf` can show zones of: every one - a higher timeframe's zones on a lower-timeframe chart
 * (the usual case), and a lower one too (a style preset such as intraday 15m / 10m on a 15 min chart); each zone is
 * placed by its own times. The chart's own timeframe first. */
export function sdTimeframesFor(tf: TimeframeId): SdTimeframe[] {
  const own = sdTimeframeOf(tf);
  return [own, ...SD_TIMEFRAMES.filter((t) => t !== own)];
}

/** The user's choice in the list's order, at most SD_MAX_TIMEFRAMES; none chosen: the chart's own. */
export function shownSdTimeframes(chosen: readonly string[] | null | undefined, tf: TimeframeId): SdTimeframe[] {
  const picked = SD_TIMEFRAMES.filter((t) => chosen?.includes(t)).slice(0, SD_MAX_TIMEFRAMES);
  return picked.length ? picked : [sdTimeframeOf(tf)];
}

export interface SdCandle { time: number; high: number | null; low: number | null; range: number | null; body_pct: number | null; mult: number | null }
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
  schema: "sd-layer/1"; symbol: string; at: string; note: string; include_violated: boolean; settings: Record<string, number | string>;
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
  for (const rows of resp.timeframes) {
    for (const z of rows.zones) {
      if (!Number.isFinite(z.proximal) || !Number.isFinite(z.distal)) continue;
      const violated = z.state === "violated";
      bands.push({ low: Math.min(z.proximal, z.distal), high: Math.max(z.proximal, z.distal), start: z.base_start, role: z.side,
                   opacity: violated ? 0.05 : 0.14, style: violated ? "dashed" : "solid", label: z.label, id: z.id, tip: sdTipRows(z) });
      segments.push({ t1: z.base_start, p1: z.proximal, t2: z.base_start, p2: z.proximal, extend: "right", role: z.side, width: 1, style: violated ? "dashed" : "solid" },
                    { t1: z.base_start, p1: z.distal, t2: z.base_start, p2: z.distal, extend: "right", role: z.side, width: 1, style: "dashed" });
    }
  }
  return bands.length ? { asOf: resp.at, bands, segments } : null;
}

/** A tap's highlight: the leg-in, base and leg-out candles as thick bars over their own range, each named once. */
export function sdFocusLayer(z: SdZone): LayerData {
  const segments: LayerSegment[] = [];
  const labels: LayerLabel[] = [];
  const mark = (c: SdCandle, text: string | null, width: number) => {
    if (c.high == null || c.low == null) return;
    segments.push({ t1: c.time, p1: c.low, t2: c.time, p2: c.high, role: z.side, width });
    if (text) labels.push({ time: c.time, price: z.side === "demand" ? c.low : c.high, text, position: z.side === "demand" ? "below" : "above", inside: true });
  };
  mark(z.leg_in, "Leg-in", 4);
  z.base.bars.forEach((b, i) => mark(b, i === 0 ? "Base" : null, 4));
  mark(z.leg_out, "Leg-out", 4);
  return { segments, labels };
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

/** Turns one timeframe on or off in the choice (starting from what the chart shows now); never below one, never above
 * SD_MAX_TIMEFRAMES (the newest pick then replaces the last one). Pure. */
export function toggleSdTimeframe(shown: readonly SdTimeframe[], t: SdTimeframe): SdTimeframe[] {
  if (shown.includes(t)) return shown.length > 1 ? shown.filter((x) => x !== t) : [...shown];
  const next = SD_TIMEFRAMES.filter((x) => x === t || shown.includes(x));
  return next.length > SD_MAX_TIMEFRAMES ? next.filter((x) => x === t || x !== shown[shown.length - 1]).slice(0, SD_MAX_TIMEFRAMES) : next;
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
