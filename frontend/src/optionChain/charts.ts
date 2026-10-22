import { request } from "../api/client";
import { MINUS, formatNumber, formatTimeIST } from "../ui/format";
import { formatStrike, toneClasses, yTick, type Tone } from "./analyses";

/**
 * OC5 (owner's order, handover 2c): the Option chain "Charts" view's data - `GET /api/option-chain/charts` (read-only;
 * backend app/option_chain/charts.py) - and the pure geometry the charts are drawn with. Every number and sentence comes
 * from the server (the user's own broker chain now and the session's stored readings); this module only lays them out.
 * Nothing here can place an order.
 */

export type ChartKey =
  | "oi_by_strike" | "oi_change_by_strike" | "multi_strike_oi" | "cumulative_oi" | "pcr_session" | "max_pain_session"
  | "straddle_session" | "atm_iv_session" | "iv_smile" | "futures";

export interface SeriesPoint { x: number | string; y: number | null; d?: number | null }
export interface ChartSeriesV2 {
  key: string;
  label: string;
  tone: Tone;
  axis: "left" | "right";
  dashed: boolean;
  points: SeriesPoint[];
  strike?: number;
}
export interface ChainChart {
  key: ChartKey;
  title: string;
  caption: string;
  state: "ok" | "building";
  message: string | null;
  type: "bar" | "line";
  x_kind: "strike" | "time";
  x_label: string;
  y_label: string;
  y2_label: string | null;
  series: ChartSeriesV2[];
  marker: { x: number; label: string } | null;
  change_basis?: "first_reading" | "previous_session" | null;
  strikes?: number[];
  default_strikes?: number[];
}
export interface ChartFacts {
  expiry: string | null;
  dte: number | null;
  pcr: number | null;
  pcr_band: string | null;
  pcr_zone: string;
  atm_iv: number | null;
  iv_change: { atm_iv: number; average: number; change_pct: number | null; sessions: { day: string; atm_iv: number }[] } | null;
  sentences: string[];
}
export type ChartsState = "ok" | "no_broker" | "unavailable" | "no_data";
export interface ChartsResponse {
  symbol: string;
  expiry: string | null;
  generated_at: string;
  strikes: number;
  interval: number;
  state: ChartsState;
  message: string | null;
  charts: ChainChart[];
  facts: ChartFacts | null;
  spot?: number | null;
  data_as_of?: string;
  source?: { broker: string; account_label: string };
  window?: { each_side: number; strikes: number[]; atm: number | null };
  intervals?: number[];
  session?: { readings: number; since: string | null; note: string | null };
}

/** The strike range choices (ATM and this many listed strikes either side); the owner's default is about ± 10. */
export const RANGES = [5, 10, 15, 20] as const;
export const DEFAULT_RANGE = 10;
export const INTERVALS = [5, 10, 15] as const;
/** The multi-strike chart shows at most this many strikes at once (calls solid, puts dashed). */
export const MAX_PICKED = 3;
/** One colour per picked strike in the multi-strike chart (v2 tokens only). */
export const PICK_TONES: readonly Tone[] = ["brand", "warn", "muted"];

export function chartsQuery(symbol: string, expiry: string | null | undefined, strikes: number, interval: number): string {
  const q = new URLSearchParams({ symbol: symbol.trim().toUpperCase(), strikes: String(strikes), interval: String(interval) });
  if (expiry) q.set("expiry", expiry);
  return q.toString();
}

export function fetchCharts(symbol: string, expiry: string | null | undefined, strikes: number, interval: number): Promise<ChartsResponse> {
  return request<ChartsResponse>(`/option-chain/charts?${chartsQuery(symbol, expiry, strikes, interval)}`);
}

/** A plain sentence for a response that carries no charts (the server's own words first). */
export function chartsStateMessage(data: ChartsResponse): string | null {
  if (data.state === "ok") return null;
  if (data.message) return data.message;
  return {
    no_broker: "Connect your broker and log in to it today to see these charts.",
    unavailable: "Your broker did not return the option chain just now. Try again in a moment.",
    no_data: "Your broker returned no strikes for this symbol and expiry.",
  }[data.state];
}

/** "Data as of 10:00 IST · 12 readings since 09:20 IST" - what the charts stand on. */
export function chartsProvenance(data: ChartsResponse): string {
  const parts: string[] = [];
  if (data.data_as_of) parts.push(`Data as of ${formatTimeIST(data.data_as_of)}`);
  const s = data.session;
  if (s && s.readings > 0 && s.since) parts.push(`${s.readings} stored ${s.readings === 1 ? "reading" : "readings"} since ${formatTimeIST(s.since)}`);
  return parts.join(" · ");
}

/** The series of the multi-strike chart for the picked strikes, each strike in its own colour (calls solid, puts dashed). */
export function pickedSeries(chart: ChainChart, picked: number[]): ChartSeriesV2[] {
  return chart.series
    .filter((s) => s.strike !== undefined && picked.includes(s.strike))
    .map((s) => ({ ...s, tone: PICK_TONES[picked.indexOf(s.strike as number) % PICK_TONES.length] }));
}

/** The strikes picked after a tap: a picked one is dropped (never the last one), a new one is added while under the limit. */
export function togglePick(picked: number[], strike: number, max = MAX_PICKED): number[] {
  if (picked.includes(strike)) return picked.length > 1 ? picked.filter((s) => s !== strike) : picked;
  if (picked.length >= max) return picked;
  return [...picked, strike].sort((a, b) => a - b);
}

/** The strikes the multi-strike chart opens on: the server's default (ATM and one either side) that are still listed. */
export function initialPicks(chart: ChainChart | undefined): number[] {
  const listed = new Set(chart?.strikes ?? []);
  const picks = (chart?.default_strikes ?? []).filter((s) => listed.has(s)).slice(0, MAX_PICKED);
  return picks.length ? picks : (chart?.strikes ?? []).slice(0, 1);
}

// ---------------------------------------------------------------- geometry (pure)

export interface Layout { width: number; height: number; left: number; right: number; top: number; bottom: number }
export interface Scale { min: number; max: number }

/** The plot box for a chart `width` px wide; a right axis takes room on the right. */
export function layout(width: number, twoAxes: boolean, height = 220): Layout {
  return { width: Math.max(160, width), height, left: 48, right: twoAxes ? 52 : 12, top: 10, bottom: 26 };
}

/** The y range of one axis's values (holding 0 for bars), padded 5 % for lines so a line never sits on the frame. */
export function axisScale(series: ChartSeriesV2[], kind: "bar" | "line"): Scale | null {
  const ys = series.flatMap((s) => s.points.map((p) => p.y)).filter((y): y is number => typeof y === "number" && Number.isFinite(y));
  if (!ys.length) return null;
  let min = Math.min(...ys), max = Math.max(...ys);
  if (kind === "bar") { min = Math.min(0, min); max = Math.max(0, max); }
  if (min === max) { const d = Math.abs(min) || 1; min -= d * 0.1; max += d * 0.1; }
  const pad = kind === "line" ? (max - min) * 0.05 : 0;
  return { min: min - pad, max: max + pad };
}

/** The x values in the order the server sends them (every series of a chart shares them). */
export function xValuesOf(series: ChartSeriesV2[]): (number | string)[] {
  const seen = new Map<string, number | string>();
  for (const s of series) for (const p of s.points) if (!seen.has(String(p.x))) seen.set(String(p.x), p.x);
  return [...seen.values()];
}

export function yAt(y: number, scale: Scale, l: Layout): number {
  const span = scale.max - scale.min || 1;
  return l.top + (1 - (y - scale.min) / span) * (l.height - l.top - l.bottom);
}

/** The centre of slot `i` of `n` across the plot. */
export function xAt(i: number, n: number, l: Layout): number {
  const slot = (l.width - l.left - l.right) / Math.max(n, 1);
  return l.left + slot * (i + 0.5);
}

/** Where a price falls between the listed strikes (linear between neighbours); null outside them. */
export function markerAt(value: number, xs: (number | string)[], l: Layout): number | null {
  const nums = xs.map(Number);
  if (nums.length === 0 || nums.some((n) => !Number.isFinite(n))) return null;
  if (value < nums[0] || value > nums[nums.length - 1]) return null;
  for (let i = 0; i < nums.length; i++) {
    if (nums[i] === value) return xAt(i, nums.length, l);
    if (i + 1 < nums.length && nums[i] < value && value < nums[i + 1]) {
      const t = (value - nums[i]) / (nums[i + 1] - nums[i]);
      return xAt(i, nums.length, l) + t * (xAt(i + 1, nums.length, l) - xAt(i, nums.length, l));
    }
  }
  return null;
}

/** How many x labels fit: about one per 64 px. */
export function labelEvery(n: number, l: Layout): number {
  const room = Math.max(1, Math.floor((l.width - l.left - l.right) / 64));
  return Math.max(1, Math.ceil(n / room));
}

export function xText(x: number | string, kind: ChainChart["x_kind"]): string {
  return kind === "time" ? formatTimeIST(String(x), { suffix: false }) : formatStrike(typeof x === "number" ? x : Number(x));
}

/** A bar's or a dot's hover note: "Call OI at 24500: 4L (today +50K)". */
export function pointNote(s: ChartSeriesV2, p: SeriesPoint, kind: ChainChart["x_kind"]): string {
  const base = `${s.label} at ${xText(p.x, kind)}: ${p.y === null ? "–" : yTick(p.y)}`;
  if (typeof p.d !== "number" || !Number.isFinite(p.d)) return base;
  return `${base} (today ${p.d > 0 ? "+" : ""}${yTick(p.d)})`;
}

/** A y-axis tick that stays readable on a narrow range: a price path (24,420 to 24,520) gets its full figures and a
 * large OI on a narrow range ("1.15Cr", "1.18Cr") two decimals, where the compact tick would print one value three times. */
export function axisTick(v: number, scale: Scale): string {
  const a = Math.max(Math.abs(scale.min), Math.abs(scale.max));
  const span = scale.max - scale.min;
  if (a < 1e3 || span >= a / 10) return yTick(v);
  if (a < 1e5) return formatNumber(v, span < 10 ? 2 : 0);
  const sign = v < 0 ? MINUS : "";
  const x = Math.abs(v);
  return x >= 1e7 ? `${sign}${(x / 1e7).toFixed(2)}Cr` : `${sign}${(x / 1e5).toFixed(2)}L`;
}

export { toneClasses, yTick };
