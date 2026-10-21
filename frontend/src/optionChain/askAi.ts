import { ApiError, friendlyError, request } from "../api/client";
import { EMPTY, MINUS, formatNumber } from "../ui/format";

/**
 * OC3 (owner's order, PART C2): "Ask AI" on the Option chain and "My analyses" - the API calls, the types and the pure
 * helpers behind AskAiV2 and MyAnalysesV2.
 *
 * - `POST /api/option-chain/ask` turns the trader's words into an analysis spec (the AI writes the spec only; the server
 *   validates it and computes every number from the chain). It counts against the plan's AI quota.
 * - `POST /api/option-chain/eval` evaluates a spec the user already has (a saved analysis). It uses no AI.
 * - Saved analyses are the user's own preferences namespace `oc_analyses` (`GET /api/me/prefs`,
 *   `PUT /api/me/prefs/oc_analyses?version=`), so phone and desktop show the same tabs.
 */

export interface SpecColumn { label: string; expr: string }
export interface AnalysisSpec {
  version?: number;
  title: string;
  window: number;
  columns: SpecColumn[];
  filters?: string[];
  sort?: { by: string; order: "asc" | "desc" } | null;
  limit?: number | null;
  chart?: { type: "bar" | "line"; x: string; y: string[] } | null;
}

export interface ResultColumn { key: string; label: string }
export interface ResultRow { strike: number; atm_offset: number; cells: (number | null)[] }
export interface ChartPoint { x: number | null; y: number | null }
export interface ResultChart { type: "bar" | "line"; x_label: string; series: { label: string; points: ChartPoint[] }[] }
export interface AnalysisResult {
  state: "ok" | "no_spot" | "no_rows";
  message: string | null;
  spot: number | null;
  atm_strike: number | null;
  window: number;
  columns: ResultColumn[];
  rows: ResultRow[];
  chart: ResultChart | null;
  history: { ref: string; at: string | null }[];
  rows_in_window: number;
  rows_kept: number;
}

export type AskState = "ok" | "unsupported" | "cannot" | "ai_off" | "quota" | "ai_error" | "no_broker" | "unavailable" | "no_data";

export interface Evaluated { spec: AnalysisSpec; description: string[]; result: AnalysisResult }
export interface AskResponse {
  symbol: string;
  expiry: string | null;
  state: AskState;
  message: string | null;
  spec: AnalysisSpec | null;
  description: string[];
  result: AnalysisResult | null;
  closest: (Evaluated & { note: string }) | null;
  data: { spot: number | null; data_as_of: string; snapshots: number; history_note: string | null } | null;
  disclaimer: string;
  usage?: { calls: number; cost_inr: number } | null;
}

export const QUESTION_MAX = 500;
/** Sent on `window` when a saved analysis is added, so an open "My analyses" reads the list again. */
export const OC_ANALYSES_EVENT = "atp:oc-analyses-changed";
export const MAX_SAVED = 8;
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9 %()+\-/.,:<>=]{0,39}$/;

type Fetcher = <T>(path: string, init?: RequestInit) => Promise<T>;

function body(symbol: string, expiry: string | null | undefined, extra: Record<string, unknown>): string {
  return JSON.stringify({ symbol: symbol.trim().toUpperCase(), ...(expiry ? { expiry } : {}), ...extra });
}

export function askAi(symbol: string, expiry: string | null | undefined, question: string, fetcher: Fetcher = request): Promise<AskResponse> {
  return fetcher<AskResponse>("/option-chain/ask", { method: "POST", body: body(symbol, expiry, { question: question.trim() }) });
}

export function evaluateSpec(symbol: string, expiry: string | null | undefined, spec: AnalysisSpec, fetcher: Fetcher = request): Promise<AskResponse> {
  return fetcher<AskResponse>("/option-chain/eval", { method: "POST", body: body(symbol, expiry, { spec }) });
}

/** The sentence to show for a failed call (a 429 / 428 / network failure already carries one). */
export function errorSentence(e: unknown): string {
  if (e instanceof ApiError && e.status === 422) return "That analysis could not be read. Ask again in other words.";
  return friendlyError(e);
}

/** The table or the plain sentence: only `ok` (and an unsupported answer's closest analysis) carry numbers. */
export function shownResult(r: AskResponse | null): Evaluated | null {
  if (!r) return null;
  if (r.state === "ok" && r.spec && r.result) return { spec: r.spec, description: r.description, result: r.result };
  return null;
}

// ---------------------------------------------------------------------------------------------------- formatting

/** A computed value: grouped digits, up to 2 decimals, a true minus sign; missing is a dash (never 0). */
export function formatCell(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return EMPTY;
  const decimals = Number.isInteger(v) ? 0 : Math.abs(v) >= 100 ? 1 : 2;
  const text = formatNumber(Math.abs(v), decimals);
  return v < 0 ? `${MINUS}${text}` : text;
}

export function formatStrike(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return EMPTY;
  return Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

// ---------------------------------------------------------------------------------------------------- the small chart

export const CHART = { width: 360, height: 150, padLeft: 46, padRight: 8, padTop: 8, padBottom: 22 } as const;

export interface Scale { min: number; max: number }

/** The y range over every drawn value, always including 0 for bars; null when nothing can be drawn. */
export function yScale(chart: ResultChart): Scale | null {
  const ys = chart.series.flatMap((s) => s.points.map((p) => p.y)).filter((y): y is number => y !== null && Number.isFinite(y));
  if (!ys.length) return null;
  let min = Math.min(...ys), max = Math.max(...ys);
  if (chart.type === "bar") { min = Math.min(min, 0); max = Math.max(max, 0); }
  if (min === max) { min -= 1; max += 1; }
  return { min, max };
}

export function yPixel(y: number, s: Scale): number {
  const { height, padTop, padBottom } = CHART;
  return padTop + ((s.max - y) / (s.max - s.min)) * (height - padTop - padBottom);
}

export function xPixel(i: number, n: number): number {
  const { width, padLeft, padRight } = CHART;
  const slot = (width - padLeft - padRight) / Math.max(n, 1);
  return padLeft + slot * (i + 0.5);
}

// ---------------------------------------------------------------------------------------------------- saved analyses

export interface SavedAnalysis { name: string; spec: AnalysisSpec; saved_at?: string | null }
export type SavedItems = Record<string, SavedAnalysis>;
export interface SavedState { items: SavedItems; version: number }

/** A name the server accepts (letters, digits and simple punctuation, up to 40 characters), or null. */
export function cleanName(raw: string): string | null {
  const name = raw.replace(/\s+/g, " ").trim().slice(0, 40).trim();
  return NAME_RE.test(name) ? name : null;
}

/** The default tab name: the analysis title when it is a valid name. */
export function defaultName(spec: AnalysisSpec): string {
  return cleanName(spec.title) ?? "My analysis";
}

export function newId(items: SavedItems, random: () => number = Math.random): string {
  for (;;) {
    const id = Array.from({ length: 8 }, () => "abcdefghijklmnopqrstuvwxyz0123456789"[Math.floor(random() * 36)]).join("");
    if (!(id in items)) return id;
  }
}

export function withAdded(items: SavedItems, id: string, analysis: SavedAnalysis): SavedItems {
  return { ...items, [id]: analysis };
}

export function withRenamed(items: SavedItems, id: string, name: string): SavedItems {
  return id in items ? { ...items, [id]: { ...items[id], name } } : items;
}

export function withDeleted(items: SavedItems, id: string): SavedItems {
  const { [id]: _gone, ...rest } = items;
  return rest;
}

function readItems(value: unknown): SavedItems {
  const items = (value as { items?: unknown } | null)?.items;
  return items && typeof items === "object" ? (items as SavedItems) : {};
}

export async function loadSaved(fetcher: Fetcher = request): Promise<SavedState> {
  const r = await fetcher<{ prefs: { oc_analyses?: unknown }; versions: { oc_analyses?: number } }>("/me/prefs");
  return { items: readItems(r.prefs?.oc_analyses), version: r.versions?.oc_analyses ?? 0 };
}

export type SaveOutcome = { ok: true; state: SavedState } | { ok: false; conflict: SavedState | null; message: string };

/** Saves the whole list on the version it was made on. Another device's newer list is a conflict (its list is returned). */
export async function saveSaved(items: SavedItems, version: number, fetcher: Fetcher = request): Promise<SaveOutcome> {
  try {
    const r = await fetcher<{ value: unknown; version: number }>(`/me/prefs/oc_analyses?version=${version}`, {
      method: "PUT", body: JSON.stringify({ items }),
    });
    return { ok: true, state: { items: readItems(r.value), version: r.version } };
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      try {
        const detail = (JSON.parse(e.body) as { detail?: { version?: unknown; current?: unknown } }).detail;
        if (typeof detail?.version === "number") {
          return { ok: false, conflict: { items: readItems(detail.current), version: detail.version },
            message: "Your analyses were changed on another device. That list is shown now; try again." };
        }
      } catch { /* not the prefs conflict shape */ }
    }
    return { ok: false, conflict: null, message: "Your analyses were not saved. Check your connection and try again." };
  }
}
