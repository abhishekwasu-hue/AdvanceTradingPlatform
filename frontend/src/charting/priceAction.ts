/**
 * N4 CH3: the price-action chart layer in the browser - causal swings, legs, real breaks, swing levels and the Dow trend
 * per degree, from `GET /api/charts/layers/price-action` (flag `chart_price_action`; off -> 503 and nothing is shown).
 *
 * The backend computes everything as of a time from the lake's closed bars only, so in replay the layer is recomputed as
 * of each replayed bar and never sees a later one. This file only turns that response into `LayerData` for the
 * `ChartEngine` (labels, segments, price lines) by the user's toggles: every number and text drawn comes from the
 * response, never from pixels. `__fixtures__/pa_layer_golden.json` is a byte-identical copy of the backend golden
 * (backend/tests/fixtures/charts/pa_layer_golden.json), so the two sides are tested on the same layer.
 *
 * Reference levels from past prices - nothing here places, changes or proposes an order.
 */
import { request } from "../api/client";
import type { LayerData, LayerLabel, LayerLine, LayerSegment } from "./engine";
import { withAlpha } from "./geometry";

export const PA_LAYER_ID = "price_action";
export const CONCEPTS = ["swings", "legs", "breaks", "levels", "trend"] as const;
export type Concept = (typeof CONCEPTS)[number];
export type Toggles = Record<Concept, boolean>;
export const DEFAULT_TOGGLES: Toggles = { swings: true, legs: true, breaks: true, levels: true, trend: true };
export const CONCEPT_LABELS: Record<Concept, string> = { swings: "Swings", legs: "Legs", breaks: "Breaks", levels: "Levels", trend: "Trend" };

export type LayerTf = "1m" | "3m" | "5m" | "15m" | "30m" | "1h" | "1d";
const TF_ALIASES: Record<string, LayerTf> = {
  "1m": "1m", "1min": "1m", "3m": "3m", "3min": "3m", "5m": "5m", "5min": "5m", "15m": "15m", "15min": "15m", "30m": "30m", "30min": "30m",
  "1h": "1h", "60m": "1h", "60min": "1h", "1d": "1d", day: "1d", "1day": "1d",
};
const TF_SECONDS: Record<LayerTf, number> = { "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400 };

/** The layer's timeframe for a chart timeframe ("5min" -> "5m", "day" -> "1d"); null when the layer has none. */
export function layerTf(chartTf: string | null | undefined): LayerTf | null {
  return chartTf ? TF_ALIASES[chartTf.toLowerCase()] ?? null : null;
}

/**
 * The as-of time of a bar that starts at `startSec`: its start plus one bar. The backend reads bars that END by then -
 * this bar and the ones before it, never the next (a session's short last bar ends earlier, still before the next bar).
 */
export function barEndIso(startSec: number, tf: LayerTf): string {
  return new Date((startSec + TF_SECONDS[tf]) * 1000).toISOString();
}

export interface PaPoint { time: number; price: number }
export interface PaSwing {
  degree: number; kind: "H" | "L"; label: string; price: number; price_text: string; time: number; status: "confirmed" | "tentative";
  confirmed_time: number | null; confirmed_at: string | null; text: string;
}
export interface PaLeg {
  degree: number; direction: "up" | "down" | "flat"; status: "confirmed" | "tentative"; from: PaPoint; to: PaPoint; change: number;
  change_pct: number; bars: number; text: string;
}
export interface PaBreak {
  degree: number; side: "above" | "below"; level: number; level_text: string; pivot_time: number; time: number; confirmed_time: number;
  close: number; text: string;
}
export interface PaLevel {
  degree: number; kind: "resistance" | "support"; price: number; price_text: string; pivot_time: number; status: "live" | "broken";
  broken_time: number | null; text: string;
}
export interface PaTrend { degree: number; tf: string; trend: "up" | "down" | "range" | "none"; basis: string; confirmed: number; text: string }
export interface PaLayerResponse {
  schema: string; instrument: string; tf: LayerTf; at: string; start: string; source: string | null; as_of: string | null; bars: number;
  degree: number; debug: boolean; first_bar: number | null; last_bar: number | null; last_close: number | null;
  swings: PaSwing[]; legs: PaLeg[]; breaks: PaBreak[]; levels: PaLevel[]; trend: PaTrend[];
  debug_info?: Record<string, unknown>; fingerprint: string; note: string; reason: string | null; concepts?: string[];
}

export const paApi = {
  layer: (instrument: string, opts: { tf: LayerTf; at?: string | null; start?: string | null; degree?: number; debug?: boolean }) => {
    const q = new URLSearchParams({ instrument, tf: opts.tf });
    if (opts.at) q.set("at", opts.at);
    if (opts.start) q.set("start", opts.start);
    if (opts.degree != null) q.set("degree", String(opts.degree));
    if (opts.debug) q.set("debug", "true");
    return request<PaLayerResponse>(`/charts/layers/price-action?${q.toString()}`);
  },
};

export const PA_COLORS = { up: "#16a34a", down: "#dc2626", flat: "#64748b", high: "#b45309", low: "#0369a1", resistance: "#dc2626", support: "#16a34a" } as const;

/** Debug view: a degree other than the chosen one is drawn fainter, so the significant swings still stand out. */
function faint(color: string, degree: number, shown: number): string {
  return degree === shown ? color : withAlpha(color, 0.6);
}

/**
 * The response as engine layer data, by the toggles. Swings are labels ("HH", "LL"; debug adds the degree and marks a
 * tentative pivot with "?"), legs are segments (a tentative leg dashed), a break is its level's run from the swing to
 * the breaking bar plus a label, live levels are price lines (broken ones only in debug, dotted). Trend is not drawn
 * on the chart - it is the panel's text.
 */
export function toLayerData(r: PaLayerResponse, toggles: Toggles): LayerData | null {
  const labels: LayerLabel[] = [];
  const segments: LayerSegment[] = [];
  const lines: LayerLine[] = [];
  const tag = (degree: number, text: string) => (r.debug ? `D${degree} ${text}` : text);
  if (toggles.swings) {
    for (const s of r.swings) {
      const base = s.kind === "H" ? PA_COLORS.high : PA_COLORS.low;
      labels.push({ time: s.time, price: s.price, text: tag(s.degree, s.status === "tentative" ? `${s.label}?` : s.label),
                    color: faint(base, s.degree, r.degree), position: s.kind === "H" ? "above" : "below" });
    }
  }
  if (toggles.legs) {
    for (const g of r.legs) {
      segments.push({ t1: g.from.time, p1: g.from.price, t2: g.to.time, p2: g.to.price, color: faint(PA_COLORS[g.direction], g.degree, r.degree),
                      width: g.degree >= 2 ? 2 : 1, style: g.status === "tentative" ? "dashed" : "solid", label: r.debug ? g.text : undefined });
    }
  }
  if (toggles.breaks) {
    for (const b of r.breaks) {
      const color = faint(b.side === "above" ? PA_COLORS.up : PA_COLORS.down, b.degree, r.degree);
      segments.push({ t1: b.pivot_time, p1: b.level, t2: b.time, p2: b.level, color, style: "dotted" });
      labels.push({ time: b.time, price: b.level, text: tag(b.degree, b.side === "above" ? `break ↑ ${b.level_text}` : `break ↓ ${b.level_text}`),
                    color, position: b.side === "above" ? "below" : "above" });
    }
  }
  if (toggles.levels) {
    for (const lv of r.levels) {
      lines.push({ price: lv.price, title: lv.text, color: lv.kind === "resistance" ? PA_COLORS.resistance : PA_COLORS.support,
                   style: lv.status === "broken" ? "dotted" : "dashed" });
    }
  }
  if (!labels.length && !segments.length && !lines.length) return null;
  return { asOf: r.as_of ?? undefined, labels, segments, lines };
}

/** The trend panel rows, top degree first (the top-down read). */
export function trendRows(r: PaLayerResponse): PaTrend[] {
  return [...r.trend].sort((a, b) => b.degree - a.degree);
}

const TOGGLES_KEY = "atp.chart.priceAction.toggles";

/** The viewer's last toggles (per browser; a convenience only - any read problem gives the defaults). */
export function loadToggles(storage: Pick<Storage, "getItem"> | null = safeStorage()): Toggles {
  try {
    const raw = storage?.getItem(TOGGLES_KEY);
    const parsed = raw ? (JSON.parse(raw) as Partial<Record<string, unknown>>) : {};
    return Object.fromEntries(CONCEPTS.map((c) => [c, typeof parsed[c] === "boolean" ? parsed[c] : DEFAULT_TOGGLES[c]])) as Toggles;
  } catch {
    return { ...DEFAULT_TOGGLES };
  }
}

export function saveToggles(t: Toggles, storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
  try { storage?.setItem(TOGGLES_KEY, JSON.stringify(t)); } catch { /* private window or blocked storage: not kept */ }
}

function safeStorage(): Storage | null {
  try { return typeof window !== "undefined" ? window.localStorage : null; } catch { return null; }
}
