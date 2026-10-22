import { request } from "../api/client";
import { EMPTY, MINUS, formatChange, formatNumber, formatPrice, formatTimeIST } from "../ui/format";
import type { GlossaryKey } from "../ui/glossary";

/**
 * OC2 (owner's order, PART C1): the built-in option chain analyses shown as tabs below the v2 option chain - the data
 * from `GET /api/option-chain/analyses` (read-only; backend app/option_chain/analyses.py) and the pure rules the tabs
 * show it by. Every number and sentence comes from the server, computed from the user's own broker chain and the
 * session's earlier snapshots; this module only formats them. Nothing here can place an order.
 */

export type AnalysisKey = "support_resistance" | "build_up" | "pcr" | "max_pain" | "expected_move" | "iv_skew";
export type ColumnKind = "strike" | "oi" | "oi_change" | "change" | "price" | "pct" | "ratio" | "iv" | "iv_diff" | "number" | "time" | "text";
export type Tone = "up" | "down" | "brand" | "muted" | "warn";
export type Cell = string | number | boolean | null;

export interface AnalysisColumn { key: string; label: string; kind: ColumnKind }
export interface AnalysisTable { title: string; columns: AnalysisColumn[]; rows: Record<string, Cell>[] }
export interface ChartPoint { x: number | string; y: number | null }
export interface ChartSeries { key: string; label: string; tone: Tone; points: ChartPoint[] }
export interface AnalysisChart {
  type: "bar" | "line";
  x_kind: "strike" | "time";
  x_label: string;
  y_label: string;
  series: ChartSeries[];
  marker: { x: number; label: string } | null;
}
export interface Analysis {
  key: AnalysisKey;
  title: string;
  state: "ok" | "no_data";
  method: string;
  tables: AnalysisTable[];
  chart: AnalysisChart | null;
  sentences: string[];
  values: Record<string, unknown>;
}
export type AnalysesState = "ok" | "no_broker" | "unavailable" | "no_data";
export interface AnalysesResponse {
  symbol: string;
  expiry: string | null;
  generated_at: string;
  state: AnalysesState;
  message: string | null;
  analyses: Analysis[];
  spot?: number | null;
  data_as_of?: string;
  source?: { broker: string; account_label: string };
  session?: { snapshots: number; since: string | null; note: string | null };
  near_atm_strikes?: number;
}

/** The tabs, in the server's order, with a short label for narrow screens and the glossary term behind each. */
export const TABS: readonly { key: AnalysisKey; label: string; short: string; term?: GlossaryKey }[] = [
  { key: "support_resistance", label: "Support / Resistance", short: "S / R", term: "oi" },
  { key: "build_up", label: "OI build-up", short: "Build-up", term: "oi" },
  { key: "pcr", label: "PCR", short: "PCR", term: "pcr" },
  { key: "max_pain", label: "Max Pain", short: "Max Pain", term: "max-pain" },
  { key: "expected_move", label: "Expected move", short: "Move" },
  { key: "iv_skew", label: "IV smile / skew", short: "IV skew", term: "iv" },
];

/** The tab a key moves to (ARIA tabs pattern): Left / Right wrap around, Home / End jump to the ends; null for any other key. */
export function nextTab(current: AnalysisKey, key: string): AnalysisKey | null {
  const i = Math.max(0, TABS.findIndex((t) => t.key === current));
  const n = TABS.length;
  const to = key === "ArrowRight" ? (i + 1) % n : key === "ArrowLeft" ? (i - 1 + n) % n : key === "Home" ? 0 : key === "End" ? n - 1 : null;
  return to === null ? null : TABS[to].key;
}

export function analysesQuery(symbol: string, expiry?: string | null): string {
  const q = new URLSearchParams({ symbol: symbol.trim().toUpperCase() });
  if (expiry) q.set("expiry", expiry);
  return q.toString();
}

/** One read of the six analyses (GET; the server reads the chain through the user's own broker session). */
export function fetchAnalyses(symbol: string, expiry?: string | null): Promise<AnalysesResponse> {
  return request<AnalysesResponse>(`/option-chain/analyses?${analysesQuery(symbol, expiry)}`);
}

/** The analysis for a tab, or null when the server did not send it. */
export function analysisFor(data: AnalysesResponse | null, key: AnalysisKey): Analysis | null {
  return data?.analyses.find((a) => a.key === key) ?? null;
}

/** A plain sentence for a response that carries no analyses (the server's own message first). */
export function stateMessage(data: AnalysesResponse): string | null {
  if (data.state === "ok") return null;
  if (data.message) return data.message;
  return {
    no_broker: "Connect your broker and log in to it today to see these analyses.",
    unavailable: "Your broker did not return the option chain just now. Try again in a moment.",
    no_data: "Your broker returned no strikes for this symbol and expiry.",
  }[data.state];
}

/** "Data as of 10:00 IST · 4 earlier snapshots since 09:20 IST" - what the numbers stand on. */
export function provenance(data: AnalysesResponse): string {
  const parts: string[] = [];
  if (data.data_as_of) parts.push(`Data as of ${formatTimeIST(data.data_as_of)}`);
  const s = data.session;
  if (s && s.snapshots > 0 && s.since) parts.push(`${s.snapshots} earlier ${s.snapshots === 1 ? "snapshot" : "snapshots"} since ${formatTimeIST(s.since)}`);
  else if (s && !s.note) parts.push("No earlier snapshot this session yet");
  return parts.join(" · ");
}

function isNum(v: Cell | undefined): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** A strike as the exchange writes it: 22000, 122.5 (no digit grouping). */
export function formatStrike(v: Cell | undefined): string {
  return isNum(v) ? String(Number(v.toFixed(2))) : EMPTY;
}

function signed(v: number, decimals: number): string {
  const plain = formatNumber(v, decimals);
  return v > 0 && plain !== formatNumber(0, decimals) ? `+${plain}` : plain;
}

/** One table cell by its column kind. A missing value is "–", never 0. */
export function formatCell(v: Cell | undefined, kind: ColumnKind): string {
  if (kind === "text") return typeof v === "string" && v ? v : EMPTY;
  if (kind === "time") return typeof v === "string" ? formatTimeIST(v, { suffix: false }) : EMPTY;
  if (!isNum(v)) return EMPTY;
  switch (kind) {
    case "strike": return formatStrike(v);
    case "oi": return formatNumber(v, 0);
    case "oi_change": return signed(v, 0);
    case "change": return formatChange(v);
    case "price": return formatPrice(v);
    case "pct": return `${formatNumber(v, 2)}%`;
    case "ratio": return formatNumber(v, 2);
    case "iv": return formatNumber(v, 2);
    case "iv_diff": return signed(v, 2);
    default: return formatNumber(v, 0);
  }
}

/** Text columns read left-aligned; every figure is right-aligned. */
export function isFigure(kind: ColumnKind): boolean {
  return kind !== "text";
}

// ---------------------------------------------------------------- the small chart (plain SVG geometry, pure)

export const CHART = { width: 360, height: 150, padLeft: 44, padRight: 8, padTop: 8, padBottom: 22 } as const;

export interface Scale { min: number; max: number }

/** The y range of every value (always holding 0 for bars), padded 5 % so a line never sits on the frame. */
export function yScale(chart: AnalysisChart): Scale | null {
  const ys = chart.series.flatMap((s) => s.points.map((p) => p.y)).filter((y): y is number => typeof y === "number" && Number.isFinite(y));
  if (!ys.length) return null;
  let min = Math.min(...ys), max = Math.max(...ys);
  if (chart.type === "bar") { min = Math.min(0, min); max = Math.max(0, max); }
  if (min === max) { const d = Math.abs(min) || 1; min -= d * 0.1; max += d * 0.1; }
  const pad = chart.type === "line" ? (max - min) * 0.05 : 0;
  return { min: min - pad, max: max + pad };
}

/** The x values in order (every series shares them; the server sends them in order). */
export function xValues(chart: AnalysisChart): (number | string)[] {
  const seen = new Map<string, number | string>();
  for (const s of chart.series) for (const p of s.points) if (!seen.has(String(p.x))) seen.set(String(p.x), p.x);
  return [...seen.values()];
}

export function yPixel(y: number, scale: Scale): number {
  const { height, padTop, padBottom } = CHART;
  const span = scale.max - scale.min || 1;
  return padTop + (1 - (y - scale.min) / span) * (height - padTop - padBottom);
}

/** The centre of slot `i` of `n` across the plot. */
export function xPixel(i: number, n: number): number {
  const { width, padLeft, padRight } = CHART;
  const slot = (width - padLeft - padRight) / Math.max(n, 1);
  return padLeft + slot * (i + 0.5);
}

/** Where a spot price falls between the listed strikes (linear between neighbours); null outside them. */
export function markerPixel(value: number, xs: (number | string)[]): number | null {
  const nums = xs.map(Number);
  if (nums.length === 0 || nums.some((n) => !Number.isFinite(n))) return null;
  if (value < nums[0] || value > nums[nums.length - 1]) return null;
  for (let i = 0; i < nums.length; i++) {
    if (nums[i] === value) return xPixel(i, nums.length);
    if (i + 1 < nums.length && nums[i] < value && value < nums[i + 1]) {
      const t = (value - nums[i]) / (nums[i + 1] - nums[i]);
      return xPixel(i, nums.length) + t * (xPixel(i + 1, nums.length) - xPixel(i, nums.length));
    }
  }
  return null;
}

/** An x-axis label: a strike, or a session time as HH:MM. */
export function xLabel(x: number | string, kind: AnalysisChart["x_kind"]): string {
  return kind === "time" ? formatTimeIST(String(x), { suffix: false }) : formatStrike(typeof x === "number" ? x : Number(x));
}

/** A y-axis tick: compact for large OI figures ("1.2L", "35K"), two decimals for small ones. */
export function yTick(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? MINUS : "";
  if (a >= 1e7) return `${sign}${trim(a / 1e7)}Cr`;
  if (a >= 1e5) return `${sign}${trim(a / 1e5)}L`;
  if (a >= 1e3) return `${sign}${trim(a / 1e3)}K`;
  return formatNumber(v, a >= 100 ? 0 : 2);
}

function trim(v: number): string {
  return String(Number(v.toFixed(1)));
}

/** The class pair (stroke, fill) of a series tone - v2 tokens only. */
export function toneClasses(tone: Tone): { stroke: string; fill: string; swatch: string } {
  switch (tone) {
    case "up": return { stroke: "stroke-up", fill: "fill-up/70", swatch: "bg-up" };
    case "down": return { stroke: "stroke-down", fill: "fill-down/70", swatch: "bg-down" };
    case "warn": return { stroke: "stroke-warn", fill: "fill-warn/70", swatch: "bg-warn" };
    case "muted": return { stroke: "stroke-fg-muted", fill: "fill-fg-muted/60", swatch: "bg-fg-muted" };
    default: return { stroke: "stroke-brand", fill: "fill-brand/70", swatch: "bg-brand/80" };
  }
}
