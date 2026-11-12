import { EMPTY, formatNumber } from "../ui/format";
import type { AnalysesResponse, AnalysisChart, Tone } from "./analyses";

/**
 * M96 N1 (owner 11 Nov): the option chain analytics - build-up at every strike and the futures OI build-up, OI walls and
 * their shift, OI / change-in-OI / volume / per-strike PCR, ATM IV percentile / rank and IV change per strike, DEX / GEX
 * with the flip level, volume / OI with the user's own highlight threshold. Every number is computed once on the server
 * (backend app/option_chain/analyses.py `chain_analytics`) and served as the `analytics` key of
 * GET /api/option-chain/analyses; this module only types, windows and formats it. Nothing here can place an order.
 */

export type BuildUpClass = "LONG_BUILDUP" | "SHORT_BUILDUP" | "SHORT_COVERING" | "LONG_UNWINDING" | "NO_CLEAR_CHANGE" | "NOT_AVAILABLE";
export type SectionState = "ok" | "no_data";
type N = number | null;

interface Section { state: SectionState; method: string; message: string | null }
export interface BuildUpRow {
  strike: number;
  call_price_change: N; call_oi_change: N; call_class: BuildUpClass; call_label: string;
  put_price_change: N; put_oi_change: N; put_class: BuildUpClass; put_label: string;
}
export interface FuturesPoint { time: string; price: N; oi: N; price_change: N; oi_change: N; class: BuildUpClass; label: string }
export interface BuildUpSection extends Section {
  rows: BuildUpRow[]; counts: Partial<Record<"call" | "put", Partial<Record<BuildUpClass, number>>>>; reference_time: string | null;
  futures: Section & { points: FuturesPoint[]; latest: FuturesPoint | null; since?: string };
}
export interface WallsNow {
  call_oi_strike: N; call_oi: N; put_oi_strike: N; put_oi: N; call_add_strike: N; call_add: N; put_add_strike: N; put_add: N;
}
export interface WallsPoint { time: string; spot: N; call_oi_strike: N; put_oi_strike: N; call_add_strike: N; put_add_strike: N }
export interface WallsSection extends Section { now?: WallsNow; series?: WallsPoint[]; strikes_in_every_snapshot?: number }
export interface Ratio { put: N; call: N; pcr: N }
export interface PcrSection extends Section {
  oi?: Ratio; change_oi?: Ratio & { basis: "broker" | "session" | null }; volume?: Ratio; strikes?: number;
  per_strike?: { strike: number; call_oi: N; put_oi: N; pcr: N }[];
}
export interface IvRank {
  atm_iv: N; sessions: number; min_sessions: number; percentile: N; rank: N; low: N; high: N;
  first_day: string | null; last_day: string | null; enough: boolean;
}
export interface IvSection extends Section {
  rank?: IvRank; reference_time?: string | null;
  per_strike?: { strike: number; call_iv: N; call_iv_change: N; put_iv: N; put_iv_change: N }[];
}
export interface ExposureRow {
  strike: number; call_delta: N; put_delta: N; call_gamma: N; put_gamma: N; call_dex: N; put_dex: N; call_gex: N; put_gex: N;
  net_dex: N; net_gex: N; cumulative_gex?: N;
}
export interface ExposureSection extends Section {
  units?: { dex: string; gex: string }; lot_size?: number; spot?: number; rows?: ExposureRow[];
  net_dex?: number; net_gex?: number; flip?: N; flips?: number[];
}
export interface VolOiRow {
  strike: number; call_volume: N; call_oi: N; call_ratio: N; call_unusual: boolean; put_volume: N; put_oi: N; put_ratio: N; put_unusual: boolean;
}
export interface VolOiSection extends Section { threshold?: N; flagged?: number; rows?: VolOiRow[] }

export interface ChainAnalytics {
  note: string; snapshots: number; since: string | null;
  buildup: BuildUpSection; walls: WallsSection; pcr: PcrSection; iv: IvSection; exposure: ExposureSection; volume_oi: VolOiSection;
}
export type AnalyticsKey = "buildup" | "walls" | "pcr" | "iv" | "exposure" | "volume_oi";

export const SECTIONS: readonly { key: AnalyticsKey; label: string; short: string }[] = [
  { key: "buildup", label: "Build-up", short: "Build-up" },
  { key: "walls", label: "OI walls", short: "Walls" },
  { key: "pcr", label: "PCR", short: "PCR" },
  { key: "iv", label: "IV rank", short: "IV" },
  { key: "exposure", label: "DEX / GEX", short: "GEX" },
  { key: "volume_oi", label: "Volume / OI", short: "Vol/OI" },
];

/** The strikes either side of ATM a per-strike table shows; null = every strike. */
export const WINDOWS: readonly { each: number | null; label: string }[] = [
  { each: 5, label: "±5" }, { each: 10, label: "±10" }, { each: null, label: "All" },
];

/** The analytics in a response, or null (an older server, or a state without analyses). */
export function analyticsOf(data: AnalysesResponse | null): ChainAnalytics | null {
  const a = (data as (AnalysesResponse & { analytics?: ChainAnalytics }) | null)?.analytics;
  return data?.state === "ok" && a && typeof a === "object" ? a : null;
}

/** ATM = the listed strike nearest the spot (a tie goes to the lower strike), as the server reads it. */
export function atmIndex(strikes: readonly number[], spot: number | null | undefined): number | null {
  if (!strikes.length || spot == null || !Number.isFinite(spot)) return null;
  let best = 0;
  strikes.forEach((k, i) => {
    const d = Math.abs(k - spot), b = Math.abs(strikes[best] - spot);
    if (d < b || (d === b && k < strikes[best])) best = i;
  });
  return best;
}

/** The rows within `each` listed strikes of ATM (rows sorted by strike); every row when `each` is null or there is no spot. */
export function windowRows<T extends { strike: number }>(rows: readonly T[], spot: number | null | undefined, each: number | null): T[] {
  if (each === null) return [...rows];
  const i = atmIndex(rows.map((r) => r.strike), spot);
  return i === null ? [...rows] : rows.slice(Math.max(0, i - each), i + each + 1);
}

/** OI and volume always in lakhs (owner order): 1250000 -> "12.50 L", 50000 -> "0.50 L". */
export function lakhs(v: N | undefined): string {
  return v == null || !Number.isFinite(v) ? EMPTY : `${formatNumber(v / 1e5, 2)} L`;
}

/** A change in lakhs, always signed: "+0.30 L", "−0.05 L", "0.00 L". */
export function lakhsChange(v: N | undefined): string {
  if (v == null || !Number.isFinite(v)) return EMPTY;
  const text = lakhs(v);
  return v > 0 && text !== "0.00 L" ? `+${text}` : text;
}

export function signed(v: N | undefined, decimals = 2): string {
  if (v == null || !Number.isFinite(v)) return EMPTY;
  const text = formatNumber(v, decimals);
  return v > 0 && Number(text.replace(/,/g, "")) !== 0 ? `+${text}` : text;
}

export function plain(v: N | undefined, decimals = 2): string {
  return formatNumber(v ?? null, decimals);
}

export function strikeText(v: N | undefined): string {
  return v == null ? EMPTY : formatNumber(v, Number.isInteger(v) ? 0 : 2);
}

/** The colour of a build-up tag by the option's own price move: up (long build-up, short covering) green, down red. */
export function buildUpTone(c: BuildUpClass): "up" | "down" | "muted" {
  if (c === "LONG_BUILDUP" || c === "SHORT_COVERING") return "up";
  if (c === "SHORT_BUILDUP" || c === "LONG_UNWINDING") return "down";
  return "muted";
}

/** Green for a rise, red for a fall, muted for zero / missing. */
export function signTone(v: N | undefined): "up" | "down" | "muted" {
  return v == null || v === 0 ? "muted" : v > 0 ? "up" : "down";
}

export const TONE_TEXT: Record<"up" | "down" | "muted", string> = { up: "text-up", down: "text-down", muted: "text-fg-muted" };

/** A bar chart by strike for MiniChart (AnalysisTabsV2) from server numbers. */
export function strikeBars<T extends { strike: number }>(rows: readonly T[], series: { key: string; label: string; tone: Tone; value: (r: T) => N }[],
  spot: number | null | undefined, yLabel: string): AnalysisChart {
  return {
    type: "bar", x_kind: "strike", x_label: "Strike", y_label: yLabel,
    series: series.map((s) => ({ key: s.key, label: s.label, tone: s.tone, points: rows.map((r) => ({ x: r.strike, y: s.value(r) })) })),
    marker: spot != null && Number.isFinite(spot) ? { x: spot, label: "Spot" } : null,
  };
}

/** A line chart through the session for MiniChart from server numbers. */
export function timeLines<T extends { time: string }>(points: readonly T[], series: { key: string; label: string; tone: Tone; value: (p: T) => N }[],
  yLabel: string): AnalysisChart {
  return {
    type: "line", x_kind: "time", x_label: "Time", y_label: yLabel,
    series: series.map((s) => ({ key: s.key, label: s.label, tone: s.tone, points: points.map((p) => ({ x: p.time, y: s.value(p) })) })),
    marker: null,
  };
}

/** The user's own threshold from the form text: a number above 0 up to 1000, or a plain problem sentence. */
export function thresholdOf(text: string): { value: number | null; problem: string | null } {
  const t = text.replace(/,/g, "").trim();
  if (!t) return { value: null, problem: null };
  const v = Number(t);
  if (!Number.isFinite(v) || v <= 0 || v > 1000) return { value: null, problem: "Enter a number above 0 and up to 1000." };
  return { value: v, problem: null };
}

export const PREFS_NAMESPACE = "chain_analytics";
