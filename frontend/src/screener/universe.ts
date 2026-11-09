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
 * - M90: no fixed 50. A timeframe the store serves (`store_timeframes`) goes as one batch of the whole list
 *   (`batchSize`); symbols the server did not read in a request come back `pending` and are sent next; a symbol the
 *   broker's limit held back is tried again later, more slowly, and a broker 429 or a busy server slows the queue down
 *   ("Slowed by broker limit") instead of failing the run. The trader can stop at any time and keeps what ran.
 * A universe is a list of names: choosing one runs nothing but the scan, and a scan lists matches, never advice.
 */
import { ApiError, request } from "../api/client";
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
// M90: the largest list sent as one ordinary run (and the most "Live counts" previews) - a longer one is streamed in
// batches. Not a cap on the list: the server reads the store's whole list, the broker's share per request.
export const RUN_MAX = 50;
export const CUSTOM = "custom";
export const DEFAULT_CHOICE = `sys:${DEFAULT_KEY}`;

export interface UniverseList { key: string; name: string; count: number }
export interface UniverseGroup { group: string; label: string; lists: UniverseList[] }
export interface BatchPlan { size: number; per_minute: number; per_second: number; max_symbols: number;
  /** M90 B2: the scan timeframes the store reads for the whole list in one request (empty: every read is the broker's). */
  store_timeframes?: string[] }
export interface UniversesResponse { default_key: string; groups: UniverseGroup[]; batch: BatchPlan; fallback?: boolean }
export interface UniverseSymbols { key: string; name: string; symbols: string[]; truncated: boolean; source_count: number }
/** A batch's broker calls (and the ones refused for want of a token), so the page paces the next batch. M90 B5:
 * `slowed` - the broker answered "too many requests" and the calls were paced and retried. */
export interface BatchBudget { broker_calls: number; refused: number; per_minute: number; slowed?: boolean }
/** M90: one batch's answer - its budget, and the symbols it did not read (sent in the next batch). */
export type BatchReply = RunResponse & { budget?: BatchBudget; pending?: string[] };

/** M90 B2: how many symbols one batch sends - the whole list (up to the request's ceiling) on a timeframe the store
 * serves, else the broker plan's batch. */
export function batchSize(plan: BatchPlan, scanTf: string): number {
  return plan.store_timeframes?.includes(scanTf) ? Math.max(plan.size, plan.max_symbols) : plan.size;
}

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

/** Consecutive slices of at most `size` (and never more than one request takes, `limit`). */
export function planBatches(symbols: readonly string[], size: number, limit = RUN_MAX): string[][] {
  const step = Math.max(1, Math.min(limit, Math.floor(size) || 1));
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
  run: (batch: string[]) => Promise<BatchReply>;
  /** `slowed` (M90 B5): the queue is going more slowly because of the broker's limit (said on the screen). */
  onProgress: (done: number, total: number, merged: RunResponse, slowed: boolean) => void;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  stopped?: () => boolean;
}

export const RATE_LIMITED = "rate_limited";        // a row's code when the broker's limit held the symbol back
export const HELD_TEXT = "waited for the broker's limit - run again shortly";
export const RETRY_ROUNDS = 2;                     // M90 B5: a held-back symbol is tried again at most this often
export const ERROR_RETRIES = 3;                    // a busy server / network answer: the same batch again, more slowly
export const SLOW_PAUSE_MS = 2_000;                // the pause a slowed queue waits at least, doubled each time
const SLOW_MAX = 8;

/** M90 B3: a batch that failed for a passing reason (a 429, a busy server, the network) is tried again, never ends the run. */
export function passing(e: unknown): boolean {
  if (e instanceof ApiError) return e.status === 429 || e.status >= 500 || e.status === 0;
  return e instanceof TypeError;                   // fetch's own network failure
}

/** Rows for symbols that could not be read after every retry: the broker's limit held them (run again in a minute). */
function heldRows(batch: readonly string[], first: RunResponse | undefined): RunResponse | null {
  if (!first) return null;
  return { ...first, scanned: batch.length, matched: [],
    results: batch.map((symbol) => ({ symbol, matched: false, reason: HELD_TEXT, code: RATE_LIMITED, action: "retry" as const })),
    funnel: { universe: batch.length, with_data: 0, stages: first.funnel.stages.map((st) => ({ text: st.text, survivors: 0 })) } };
}

/** Runs `symbols` batch after batch (one in flight at a time), reporting the merged answer after each one, and waits
 * between batches for the broker calls each made (`waitAfter`). A batch reply without its budget counts every symbol
 * as a call (the careful reading). Stops early when `stopped()` says so; null when nothing ran.
 * M90: `pending` symbols of a reply are sent next; symbols the broker's limit held back (code `rate_limited`) are tried
 * again at the end of the queue (RETRY_ROUNDS); a slowed reply or a passing failure doubles the pause (up to 8x) and
 * the screen says so - the run never fails on the broker's limit. `size`: the symbols one request sends (`batchSize`). */
export async function runInBatches(symbols: readonly string[], plan: BatchPlan, deps: BatchDeps, size = plan.size): Promise<RunResponse | null> {
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((resolve) => { window.setTimeout(resolve, ms); }));
  const now = deps.now ?? (() => Date.now());
  const step = Math.max(1, Math.min(plan.max_symbols || RUN_MAX, Math.floor(size) || 1));
  const queue: string[] = [...symbols];
  const tries = new Map<string, number>();
  const done: RunResponse[] = [];
  let factor = 1;
  let slowed = false;
  let failures = 0;
  const report = () => { if (done.length) deps.onProgress(done.reduce((n, r) => n + r.scanned, 0), symbols.length, mergeRuns(done), slowed); };
  while (queue.length > 0) {
    if (deps.stopped?.()) break;
    const batch = queue.splice(0, step);
    const started = now();
    let out: BatchReply;
    try {
      out = await deps.run(batch);
      failures = 0;
    } catch (e) {
      if (!passing(e)) throw e;
      failures += 1;
      slowed = true;
      factor = Math.min(SLOW_MAX, factor * 2);
      if (failures > ERROR_RETRIES) {
        const rows = heldRows(batch, done[0]);
        if (!rows) throw e;                          // not one batch answered: the run's own error
        done.push(rows);
        failures = 0;
      } else {
        queue.unshift(...batch);
      }
      report();
      if (queue.length && !deps.stopped?.()) await sleep(SLOW_PAUSE_MS * factor);
      continue;
    }
    let pending = (out.pending ?? []).filter((s) => batch.includes(s));
    if (pending.length >= batch.length) {            // nothing read at all: held back, never sent round again
      out = heldRows(batch, out) ?? out;
      pending = [];
    }
    const again = out.results.filter((r) => r.code === RATE_LIMITED && (tries.get(r.symbol) ?? 0) < RETRY_ROUNDS).map((r) => r.symbol);
    for (const s of again) tries.set(s, (tries.get(s) ?? 0) + 1);
    if (again.length) {
      const later = new Set(again);
      out = { ...out, scanned: out.scanned - again.length, results: out.results.filter((r) => !later.has(r.symbol)),
        funnel: { ...out.funnel, universe: out.funnel.universe - again.length } };
    }
    done.push(out);
    queue.unshift(...pending);                       // not read in this request: next
    queue.push(...again);                            // held back by the broker's limit: again, later
    const slowNow = !!out.budget?.slowed || again.length > 0;
    if (slowNow) { slowed = true; factor = Math.min(SLOW_MAX, factor * 2); }
    report();
    if (queue.length > 0) {
      const calls = out.budget ? out.budget.broker_calls : batch.length - pending.length;
      let wait = waitAfter(calls, out.budget?.per_minute ?? plan.per_minute, now() - started) * factor;
      if (slowNow) wait = Math.max(wait, SLOW_PAUSE_MS * factor);
      if (wait > 0) await sleep(wait);
    }
  }
  return done.length ? mergeRuns(done) : null;
}
