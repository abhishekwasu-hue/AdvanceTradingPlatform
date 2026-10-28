/**
 * Owner order (23 Oct) "the scan universe is never empty" (behind `ui_v2` + `screener_v2`): what Find setups scans.
 * - The lists come from GET /screener/universes (the system index-stock, sector and F&O lists; app/screener/universes.py).
 *   NIFTY 50 is chosen on open. When the server has no NIFTY 50 list (the system lists are not built yet, or the call
 *   failed), the names-only fallback below stands in.
 * - A run is never sent with no symbols: an empty choice (an empty watchlist, a blank custom box) scans NIFTY 50 and
 *   says so.
 * - A universe larger than one run (50 symbols) goes in batches, one at a time. Each batch is a `/run` with
 *   `batch: true`: every broker call takes a token from the broker session's shared API budget, and the page waits
 *   after each batch for the calls it made at the per-minute share (`waitAfter`), so a long list never spends past it.
 * A universe is a list of names: choosing one runs nothing but the scan, and a scan lists matches, never advice.
 */
import { request } from "../api/client";
import type { RunResponse } from "./api";

/** NIFTY 50 by name only, for when the server has no NIFTY 50 list yet (no prices, no weights). The system list, built
 * from the exchange's file every day, replaces it as soon as it exists (docs/OPEN_QUESTIONS.md SU-1). */
export const NIFTY50_FALLBACK: readonly string[] = [
  "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL",
  "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
  "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
  "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
  "SUNPHARMA", "TATACONSUM", "TATASTEEL", "TCS", "TECHM", "TITAN", "TMPV", "TRENT", "ULTRACEMCO", "WIPRO",
];

/** SCR-FIX D10: the day the names above were taken from the exchange's NIFTY 50 list. The index changes twice a year, so
 * the page says this date whenever the built-in list stands in (NIFTY50_FALLBACK_NOTE); update both together. */
export const NIFTY50_FALLBACK_AS_OF = "2026-10-23";
/** The one line shown while the built-in list is used (the server's list is missing or could not be loaded). */
export const NIFTY50_FALLBACK_NOTE = "The latest NIFTY 50 list could not be loaded, so the built-in list (as of 23 Oct 2026) is used.";

export const DEFAULT_KEY = "index-nifty50";
export const RUN_MAX = 50;                    // the run route's cap per call (app/screener/routes.py MAX_SYMBOLS)
export const CUSTOM = "custom";
export const DEFAULT_CHOICE = `sys:${DEFAULT_KEY}`;

export interface UniverseList { key: string; name: string; count: number }
export interface UniverseGroup { group: string; label: string; lists: UniverseList[] }
export interface BatchPlan { size: number; per_minute: number; per_second: number; max_symbols: number }
export interface UniversesResponse { default_key: string; groups: UniverseGroup[]; batch: BatchPlan }
export interface UniverseSymbols { key: string; name: string; symbols: string[]; truncated: boolean; source_count: number }
/** A batch's broker calls (and the ones refused for want of a token), so the page paces the next batch. */
export interface BatchBudget { broker_calls: number; refused: number; per_minute: number }

/** The most careful plan (the share of a broker without a recorded limit), used until the server's plan is read. */
export const FALLBACK_PLAN: BatchPlan = { size: 10, per_minute: 50, per_second: 1, max_symbols: 500 };

export const universesApi = {
  list: () => request<UniversesResponse>("/screener/universes"),
  symbols: (key: string) => request<UniverseSymbols>(`/screener/universes/${encodeURIComponent(key)}`),
};

export function isUniverses(v: unknown): v is UniversesResponse {
  const r = v as UniversesResponse | null;
  return !!r && typeof r === "object" && Array.isArray(r.groups) && !!r.batch && typeof r.batch.size === "number"
    && r.groups.every((g) => g && Array.isArray(g.lists));
}

/** "NIFTY 50 stocks (50)" -> "NIFTY 50"; a sector or "F&O stocks (180)" keeps its name, without the count. */
export function listLabel(list: UniverseList, group: string): string {
  const base = list.name.replace(/\s*\(\d+\)$/, "");
  return group === "INDEX_STOCKS" ? base.replace(/\s+stocks$/i, "") : base;
}

/** Does the server hold the NIFTY 50 list (else the fallback names stand in)? */
export function serverHasDefault(res: UniversesResponse | null): boolean {
  return !!res?.groups.some((g) => g.lists.some((l) => l.key === DEFAULT_KEY));
}

/** The symbols a run is sent with: the chosen ones, or NIFTY 50 when the choice holds none - never an empty list. */
export function effectiveUniverse(chosen: readonly string[], nifty50: readonly string[]): { symbols: string[]; fellBack: boolean } {
  if (chosen.length > 0) return { symbols: [...chosen], fellBack: false };
  return { symbols: [...(nifty50.length ? nifty50 : NIFTY50_FALLBACK)], fellBack: true };
}

/** Consecutive slices of at most `size` (and never more than one run takes). */
export function planBatches(symbols: readonly string[], size: number): string[][] {
  const step = Math.max(1, Math.min(RUN_MAX, Math.floor(size) || 1));
  const out: string[][] = [];
  for (let i = 0; i < symbols.length; i += step) out.push(symbols.slice(i, i + step));
  return out;
}

/** Milliseconds to wait after a batch that made `calls` broker calls in `elapsedMs`, so the calls stay inside the
 * per-minute share (a batch served from cached bars made none: no wait). */
export function waitAfter(calls: number, perMinute: number, elapsedMs: number): number {
  if (calls <= 0) return 0;
  return Math.max(0, Math.ceil((calls * 60_000) / Math.max(1, perMinute) - elapsedMs));
}

/** One answer from several batch runs: the results in order, the funnel's counts added up stage by stage. */
export function mergeRuns(runs: readonly RunResponse[]): RunResponse {
  const [first] = runs;
  const last = runs[runs.length - 1];
  const stages = first.funnel.stages.map((s, i) => {
    const removed = runs.flatMap((r) => r.funnel.stages[i]?.removed ?? []);
    return { text: s.text, survivors: runs.reduce((n, r) => n + (r.funnel.stages[i]?.survivors ?? 0), 0), ...(removed.length ? { removed } : {}) };
  });
  const passes = runs.some((r) => r.funnel.passes) ? Object.assign({}, ...runs.map((r) => r.funnel.passes ?? {})) as Record<string, boolean[]> : undefined;
  return {
    ...last,
    text: first.text, base_tf: first.base_tf, data_source: first.data_source, disclaimer: first.disclaimer,
    scanned: runs.reduce((n, r) => n + r.scanned, 0),
    session_held_back: runs.reduce((n, r) => n + (r.session_held_back ?? 0), 0) || undefined,
    matched: runs.flatMap((r) => r.matched),
    results: runs.flatMap((r) => r.results),
    funnel: { universe: runs.reduce((n, r) => n + r.funnel.universe, 0), with_data: runs.reduce((n, r) => n + r.funnel.with_data, 0),
      stages, ...(passes ? { passes } : {}) },
  };
}

/** Symbols the broker's limit held back in this answer (the page offers to run them again). */
export function heldBack(run: RunResponse | null): string[] {
  return (run?.results ?? []).filter((r) => !r.matched && (r.reason ?? "").includes("broker's limit")).map((r) => r.symbol);
}

export interface BatchDeps {
  run: (batch: string[]) => Promise<RunResponse & { budget?: BatchBudget }>;
  onProgress: (done: number, total: number, merged: RunResponse) => void;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  stopped?: () => boolean;
}

/** Runs `symbols` batch after batch (one in flight at a time), reporting the merged answer after each one, and waits
 * between batches for the broker calls each made (`waitAfter`). A batch reply without its budget counts every symbol
 * as a call (the careful reading). Stops early when `stopped()` says so; null when nothing ran. */
export async function runInBatches(symbols: readonly string[], plan: BatchPlan, deps: BatchDeps): Promise<RunResponse | null> {
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((resolve) => { window.setTimeout(resolve, ms); }));
  const now = deps.now ?? (() => Date.now());
  const batches = planBatches(symbols, plan.size);
  const done: RunResponse[] = [];
  for (let i = 0; i < batches.length; i += 1) {
    if (deps.stopped?.()) break;
    const started = now();
    const out = await deps.run(batches[i]);
    done.push(out);
    deps.onProgress(done.reduce((n, r) => n + r.scanned, 0), symbols.length, mergeRuns(done));
    if (i < batches.length - 1) {
      const calls = out.budget ? out.budget.broker_calls : batches[i].length;
      const wait = waitAfter(calls, out.budget?.per_minute ?? plan.per_minute, now() - started);
      if (wait > 0) await sleep(wait);
    }
  }
  return done.length ? mergeRuns(done) : null;
}
