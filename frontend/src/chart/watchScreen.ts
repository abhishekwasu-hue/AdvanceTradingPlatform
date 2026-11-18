/**
 * M91 (owner order): a watchlist's "Screen" - the trader's own saved screen read on the list (W1-W4), and the same
 * screen drawn on a stock's chart opened from that list (C1). Behind the `watchlist_screen` flag (with `watchlists` and
 * `screener_v2`); while it is off the server answers 503 and nothing here shows.
 *
 * - W1 each list keeps its choice (None / one of his own saved screens) on the server: phone and desktop agree.
 * - W2 with a screen chosen, the rows that meet it move to the top (in the list's own order) and the rest stay below,
 *   dimmed - never removed. "Matches only" shows the matches alone; the count says "12 of 80 match".
 * - W3 the list is checked when it shows and again when a new bar of the screen's timeframe has closed (the server says
 *   when: `next_check_at`), only while the list is on screen and the tab is visible. Store-first; the broker only for
 *   symbols the store lacks, paced by the screener's own batch queue (`runInBatches`).
 * - W4 an alert when a stock newly enters the matches: OFF until he turns it on (in the app; Telegram optional).
 * - C2 the rows and the chart read ONE server evaluation (app/screener/watch_screen.py `screen_marks`).
 * No default screen, no default parameter, no rule of ATP's own: the screen, its timeframe and its numbers are his.
 */
import { request } from "../api/client";
import type { RunResponse, RunResult } from "../screener/api";
import { FALLBACK_PLAN, RATE_LIMITED, runInBatches, type BatchBudget, type BatchPlan } from "../screener/universe";
import type { EvidenceDrawable, EvidencePack, RowState } from "../screener/evidence";
import type { ChartBar } from "./timeframes";
import { itemKey, type WatchItem } from "./watchlistsV2";

export interface OwnScreen { id: number; name: string; base_tf: string; usable: boolean; why?: string }
export interface ListChoice { screen_id: number | null; matches_only: boolean; alert: boolean; telegram: boolean }
export const NO_CHOICE: ListChoice = { screen_id: null, matches_only: false, alert: false, telegram: false };

export type CheckResult = RunResult & { marks?: RowState[] };
export interface CheckReply extends Omit<RunResponse, "run_id" | "results"> {
  results: CheckResult[];
  screen: { id: number; name: string };
  budget?: BatchBudget; pending?: string[];
  bar: string | null;
  /** Epoch milliseconds: when a newer bar of the screen's timeframe has closed (plus a short wait for its data). */
  next_check_at: number | null;
  alerted: string[];
}
/** Owner 15 Nov: the kept results of a list on one market. `fresh`: rows read on the newest closed bar (nothing to check). */
export interface SavedReply {
  results: CheckResult[]; fresh: string[]; bar: string | null; market_open: boolean; last_close: string | null;
  checked_at: string | null; next_check_at: number | null;
}
export interface LegendRow { id: string; block: string; text: string; state: RowState; value: string }
export interface OverlayReply {
  symbol: string; exchange: string; base_tf: string; screen: { id: number; name: string }; text: string; disclaimer: string;
  available: boolean; reason?: string | null; matched?: boolean;
  legend?: LegendRow[]; hits?: string[]; as_of?: { bar_start: string; bar_close?: string };
  bars?: EvidencePack["bars"] | null; drawables?: EvidenceDrawable[]; matched_index?: number | null;
}

export const watchScreenApi = {
  screens: () => request<{ screens: OwnScreen[] }>("/screener/watchlist-screen/screens"),
  settings: () => request<{ lists: Record<string, ListChoice> }>("/screener/watchlist-screen/settings"),
  save: (key: string, choice: ListChoice) =>
    request<{ list_key: string; choice: ListChoice | null }>(`/screener/watchlist-screen/settings/${encodeURIComponent(key)}`,
      { method: "PUT", body: JSON.stringify(choice) }),
  check: (body: { list_key: string; list_name: string; screen_id: number; symbols: string[]; exchange: string }) =>
    request<CheckReply>("/screener/watchlist-screen/check", { method: "POST", body: JSON.stringify(body) }),
  /** Owner 15 Nov: the list's results kept on the server (no broker call) - which rows are read on the newest closed bar. */
  saved: (body: { list_key: string; list_name: string; screen_id: number; symbols: string[]; exchange: string }) =>
    request<SavedReply>("/screener/watchlist-screen/saved", { method: "POST", body: JSON.stringify(body) }),
  overlay: (body: { screen_id: number; symbol: string; exchange: string }) =>
    request<OverlayReply>("/screener/watchlist-screen/overlay", { method: "POST", body: JSON.stringify(body) }),
};
export type WatchScreenApi = typeof watchScreenApi;

/** The key a list's choice is kept under: one of his own lists by id, a system list by its key. */
export function listKeyOf(ref: { kind: "own"; id: number } | { kind: "system"; key: string }): string {
  return ref.kind === "own" ? `o:${ref.id}` : `s:${ref.key}`;
}

/** The exchanges a screen runs on (the screener's run); a row on any other market is "not checked". */
export const SCREENED_EXCHANGES: ReadonlySet<string> = new Set(["NSE", "BSE", "NFO", "BFO", "MCX", "CDS"]);

/** W2: the rows in display order - the matches first (in the list's own order), then the rest (in theirs). */
export function orderByMatch<T extends WatchItem>(items: readonly T[], matched: ReadonlySet<string>): T[] {
  const top = items.filter((i) => matched.has(itemKey(i)));
  return [...top, ...items.filter((i) => !matched.has(itemKey(i)))];
}

/** W2: the rows shown - every row, or with "Matches only" the matches alone. */
export function shownRows<T extends WatchItem>(items: readonly T[], matched: ReadonlySet<string>, matchesOnly: boolean): T[] {
  const ordered = orderByMatch(items, matched);
  return matchesOnly ? ordered.filter((i) => matched.has(itemKey(i))) : ordered;
}

/** W2: "12 of 80 match" (every row of the list counts, a row not checked among the rest). */
export function countText(items: readonly WatchItem[], matched: ReadonlySet<string>): string {
  const n = items.filter((i) => matched.has(itemKey(i))).length;
  return `${n} of ${items.length} ${n === 1 && items.length === 1 ? "matches" : "match"}`;
}

/** W3: milliseconds until the next check (at least a second, at most a day - the timer is set again then). */
export const MAX_WAIT_MS = 24 * 60 * 60_000;
export function waitUntil(nextAt: number | null | undefined, now: number): number | null {
  if (nextAt == null || !Number.isFinite(nextAt)) return null;
  return Math.min(MAX_WAIT_MS, Math.max(1_000, nextAt - now));
}

/** The list's rows by the exchange a check is sent for (rows on other markets are left out: not checked). */
export function byExchange(items: readonly WatchItem[]): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const i of items) {
    const ex = i.exchange.toUpperCase();
    if (!SCREENED_EXCHANGES.has(ex)) continue;
    out.set(ex, [...(out.get(ex) ?? []), i.symbol.toUpperCase()]);
  }
  return out;
}

export interface CheckOutcome {
  matched: Set<string>; results: Map<string, CheckResult>; nextAt: number | null; alerted: string[]; slowed: boolean; bar: string | null;
  /** Owner 15 Nov: rows read so far (a held-back row is not counted), of `total` rows in the list. */
  checked?: number; total?: number;
  /** Rows the broker's limit held back: "not checked" (never counted as not matched); retried at the next bar. */
  held?: Set<string>;
  /** The market is closed: the result is the one from the last close (ISO time), nothing is checked until a new bar. */
  closedSince?: string | null;
  /** The check reached its time limit; the rows not read are retried at the next bar. */
  timedOut?: boolean;
}

/** Owner 15 Nov: a held-back row - the broker's limit (or a budget) kept it from being read; it is checked again. */
export function isHeld(r: CheckResult | undefined): boolean {
  return !!r && !r.matched && (r.code === RATE_LIMITED || r.action === "retry");
}

/** The counts the bar shows: rows read (held-back rows are not), matches, held back. */
export function tally(o: CheckOutcome | null, total: number): { checked: number; total: number; matched: number; held: number } {
  if (!o) return { checked: 0, total, matched: 0, held: 0 };
  const held = o.held?.size ?? [...o.results.values()].filter(isHeld).length;
  return { checked: Math.min(total, o.checked ?? Math.max(0, o.results.size - held)), total, matched: o.matched.size, held };
}

/** Owner 15 Nov: "Checked 40 of 218" while it runs; "Checked 218 of 218 · 3 match" when done. */
export function progressText(o: CheckOutcome | null, total: number, busy: boolean): string {
  const t = tally(o, total);
  const head = `Checked ${t.checked} of ${t.total}`;
  return busy ? head : `${head} · ${t.matched} ${t.matched === 1 && t.total === 1 ? "matches" : "match"}`;
}

/** "15 Nov, 15:30" - when the market last closed (the exchange clock). */
export function closeLabel(iso: string | null | undefined): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (!Number.isFinite(t)) return "the last close";
  return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }).format(t);
}

function finish(out: CheckOutcome, items: readonly WatchItem[]): CheckOutcome {
  const held = new Set<string>();
  for (const [k, r] of out.results) if (isHeld(r)) { held.add(k); out.matched.delete(k); }
  out.held = held;
  out.total = items.length;
  out.checked = [...out.results.keys()].filter((k) => !held.has(k)).length;
  return out;
}

/**
 * W3: one check of the whole list - each market's rows through the screener's own paced batch queue (store-first on
 * the server; `pending` symbols sent next; a broker 429 slows it, never fails it). `stopped` ends it early (the list left
 * the screen). The earliest `next_check_at` of the replies says when to check again.
 */
export async function checkList(api: Pick<WatchScreenApi, "check">, args: { listKey: string; listName: string; screenId: number; items: readonly WatchItem[] },
  opts: { plan?: BatchPlan; stopped?: () => boolean; sleep?: (ms: number) => Promise<void>; onProgress?: (o: CheckOutcome) => void;
    /** Owner 15 Nov: the kept results to start from, and the rows they already hold on the newest bar (not sent). */
    start?: CheckOutcome | null; skip?: ReadonlySet<string> } = {}): Promise<CheckOutcome> {
  const out: CheckOutcome = opts.start
    ? { ...opts.start, matched: new Set(opts.start.matched), results: new Map(opts.start.results), alerted: [], slowed: false }
    : { matched: new Set(), results: new Map(), nextAt: null, alerted: [], slowed: false, bar: null };
  if (opts.start) out.nextAt = null;
  const plan = opts.plan ?? { ...FALLBACK_PLAN, size: 500, max_symbols: 500 };
  const progress = () => opts.onProgress?.(finish(out, args.items));
  for (const [exchange, all] of byExchange(args.items)) {
    if (opts.stopped?.()) break;
    const symbols = opts.skip ? all.filter((s) => !opts.skip?.has(`${exchange}:${s}`)) : all;
    if (symbols.length === 0) continue;
    const replies: CheckReply[] = [];
    await runInBatches(symbols, plan, {
      run: async (batch) => {
        const r = await api.check({ list_key: args.listKey, list_name: args.listName, screen_id: args.screenId, symbols: batch, exchange });
        replies.push(r);
        return { ...r, run_id: 0 } as RunResponse & { budget?: BatchBudget; pending?: string[] };
      },
      onProgress: (_d, _t, merged, slowed) => {
        for (const r of merged.results as CheckResult[]) {
          const key = `${exchange}:${r.symbol.toUpperCase()}`;
          out.results.set(key, r);
          if (r.matched) out.matched.add(key); else out.matched.delete(key);
        }
        out.slowed = out.slowed || slowed;
        progress();
      },
      stopped: opts.stopped, sleep: opts.sleep,
    }, plan.size);
    for (const r of replies) {
      if (r.next_check_at != null && (out.nextAt == null || r.next_check_at < out.nextAt)) out.nextAt = r.next_check_at;
      out.alerted.push(...(r.alerted ?? []));
      if (r.bar && (!out.bar || r.bar > out.bar)) out.bar = r.bar;
    }
  }
  if (opts.start && out.nextAt == null) out.nextAt = opts.start.nextAt;
  return finish(out, args.items);
}

/**
 * Owner 15 Nov: the list's kept results (no broker call) as an outcome, and the rows already read on the newest closed
 * bar (`fresh`: not sent again). A market whose answer failed counts nothing as fresh (its rows are checked as before).
 */
export async function savedList(api: Pick<WatchScreenApi, "saved">, args: { listKey: string; listName: string; screenId: number; items: readonly WatchItem[] }):
  Promise<{ outcome: CheckOutcome; fresh: Set<string>; closed: boolean } | null> {
  const out: CheckOutcome = { matched: new Set(), results: new Map(), nextAt: null, alerted: [], slowed: false, bar: null };
  const fresh = new Set<string>();
  let closed = true;
  let any = false;
  let lastClose: string | null = null;
  for (const [exchange, symbols] of byExchange(args.items)) {
    let r: SavedReply;
    try {
      r = await api.saved({ list_key: args.listKey, list_name: args.listName, screen_id: args.screenId, symbols, exchange });
    } catch {
      closed = false;
      continue;
    }
    any = true;
    for (const row of r.results ?? []) {
      const key = `${exchange}:${row.symbol.toUpperCase()}`;
      out.results.set(key, row);
      if (row.matched) out.matched.add(key);
    }
    for (const s of r.fresh ?? []) fresh.add(`${exchange}:${s.toUpperCase()}`);
    if (r.next_check_at != null && (out.nextAt == null || r.next_check_at < out.nextAt)) out.nextAt = r.next_check_at;
    if (r.bar && (!out.bar || r.bar > out.bar)) out.bar = r.bar;
    closed = closed && !r.market_open;
    if (r.last_close && (!lastClose || r.last_close > lastClose)) lastClose = r.last_close;
  }
  if (!any) return null;
  if (closed) out.closedSince = lastClose;
  return { outcome: finish(out, args.items), fresh, closed };
}

// ------------------------------------------------------------------------------------------------ C1: the overlay
/** The bar length in seconds of a screen timeframe (to match its bars to the chart's candles). */
const TF_SECONDS: Record<string, number> = { "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86_400, "1w": 604_800, "1M": 2_592_000 };

/**
 * C1: the overlay's bar times put on the chart's own candles - each one to the nearest candle within half a bar (the
 * server labels a daily bar by its session, the chart by its own clock). A bar with no candle near it stays as it was.
 */
export function onCandles(times: readonly string[], candles: readonly Pick<ChartBar, "timestamp">[], baseTf: string): string[] {
  const half = ((TF_SECONDS[baseTf] ?? 86_400) * 1000) / 2;
  const at = candles.map((c) => Date.parse(c.timestamp)).filter(Number.isFinite).sort((a, b) => a - b);
  const iso = new Map(candles.map((c) => [Date.parse(c.timestamp), c.timestamp]));
  return times.map((t) => {
    const x = Date.parse(t);
    if (!Number.isFinite(x) || at.length === 0) return t;
    let lo = 0;
    let hi = at.length - 1;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (at[mid] < x) lo = mid + 1; else hi = mid; }
    const near = [at[lo], at[lo - 1]].filter((v) => v !== undefined).reduce((a, b) => (Math.abs(b - x) < Math.abs(a - x) ? b : a));
    return Math.abs(near - x) <= half ? iso.get(near) ?? t : t;
  });
}

/** C1: the legend's mark for a condition state - ✓ met, ✗ not met, – not needed (an OR met by another part), ? unknown. */
export function markOf(state: RowState): { sign: string; label: string; tone: "up" | "down" | "muted" } {
  if (state === "met") return { sign: "✓", label: "Met", tone: "up" };
  if (state === "failed") return { sign: "✗", label: "Not met", tone: "down" };
  if (state === "not_needed") return { sign: "–", label: "Not needed", tone: "muted" };
  return { sign: "?", label: "Could not be checked", tone: "muted" };
}

/** C1: the screen chosen for the list a chart was opened from (null: none, nothing drawn). */
export interface ScreenFrom {
  symbol: string; exchange: string; screenId: number; screenName: string; listKey: string;
  /** Owner 18 Nov: the screen's own timeframe (its `base_tf`), so the chart opens where the conditions and marks are drawn. */
  baseTf?: string;
}

export { SCREEN_PARAM, screenIdOf, screenParam, withScreen } from "./screenParam";

// ------------------------------------------------------------------------------------------- W3: when a list is checked
export interface CheckerTarget { listKey: string; listName: string; screenId: number; items: readonly WatchItem[] }
/** Owner 15 Nov: one check stops after this long; the rows not read by then are checked at the next bar. */
export const CHECK_LIMIT_MS = 10 * 60_000;
/** A check that failed outright tries again at the next bar close, or after this when none is known. */
export const RETRY_AFTER_ERROR_MS = 5 * 60_000;
export interface SavedState { outcome: CheckOutcome; fresh: ReadonlySet<string>; closed: boolean }
export interface CheckerDeps {
  check: (target: CheckerTarget, stopped: () => boolean, onProgress: (o: CheckOutcome) => void,
    resume?: { start: CheckOutcome; skip: ReadonlySet<string> }) => Promise<CheckOutcome>;
  /** Owner 15 Nov: the kept results (null: none / not readable - the whole list is checked). */
  saved?: (target: CheckerTarget) => Promise<SavedState | null>;
  limitMs?: number;
  onOutcome: (o: CheckOutcome | null) => void;
  onBusy: (busy: boolean) => void;
  /** `error`: what the failed check threw (owner 18 Nov: the server's own reason is shown, e.g. no broker session). */
  onFailed: (failed: boolean, error?: unknown) => void;
  hidden: () => boolean;
  now: () => number;
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (t: unknown) => void;
}

/**
 * W3: the list on screen is checked when it shows (and when its rows or its screen change), then again only when the
 * server's `next_check_at` comes - a newer bar of the screen's timeframe has closed. A list that is not on screen
 * (`active` false) is never checked; a check that falls due while the tab is hidden waits for it to show.
 */
export class ScreenChecker {
  private key = "";
  private target: CheckerTarget | null = null;
  private timer: unknown = null;
  private due = false;
  private run = 0;
  private busy = false;
  private lastNext: number | null = null;

  constructor(private readonly deps: CheckerDeps) {}

  update(active: boolean, target: CheckerTarget | null): void {
    const key = active && target ? `${target.listKey}|${target.screenId}|${target.items.map((i) => itemKey(i)).sort().join(",")}` : "";
    if (target) this.target = target;
    if (key === this.key) return;
    const screenChanged = !this.key || !key || this.key.split("|").slice(0, 2).join("|") !== key.split("|").slice(0, 2).join("|");
    this.stop();
    this.key = key;
    if (screenChanged) this.deps.onOutcome(null);
    if (key) this.check();
  }

  /** The tab showed again: a check that fell due meanwhile runs now. */
  visible(): void {
    if (this.due && this.key && !this.deps.hidden()) this.check();
  }

  close(): void {
    this.stop();
    this.key = "";
  }

  private stop(): void {
    this.run += 1;
    if (this.timer !== null) this.deps.clearTimer(this.timer);
    this.timer = null;
    this.due = false;
    this.setBusy(false);                         // owner 15 Nov: a stopped check never leaves "Checking…" behind
  }

  private setBusy(busy: boolean): void {
    if (busy === this.busy) return;
    this.busy = busy;
    this.deps.onBusy(busy);
  }

  private check(): void {
    if (!this.target || !this.key) return;
    if (this.deps.hidden()) { this.due = true; return; }
    this.due = false;
    const run = ++this.run;
    const target = this.target;
    const deadline = this.deps.now() + (this.deps.limitMs ?? CHECK_LIMIT_MS);
    let timedOut = false;
    const stopped = () => {
      if (run !== this.run) return true;
      if (this.deps.now() <= deadline) return false;
      timedOut = true;                           // the time limit: what was read stays, the rest waits for the next bar
      return true;
    };
    const left = () => run !== this.run;
    this.setBusy(true);
    // intentional: saved results are optional; a failed read falls back to a full check below
    const begin = this.deps.saved ? this.deps.saved(target).catch(() => null) : Promise.resolve(null);
    begin.then((kept) => {
      if (left()) return undefined;
      if (kept) {
        this.deps.onOutcome(kept.outcome);
        const stale = target.items.filter((i) => SCREENED_EXCHANGES.has(i.exchange.toUpperCase()) && !kept.fresh.has(itemKey(i)));
        if (stale.length === 0) {                // every row read on the newest closed bar (or the market is closed): nothing to ask
          this.deps.onFailed(false);
          this.setBusy(false);
          this.schedule(kept.outcome.nextAt);
          return undefined;
        }
      }
      const resume = kept ? { start: kept.outcome, skip: kept.fresh } : undefined;
      return this.deps.check(target, stopped, (o) => { if (!left()) this.deps.onOutcome(o); }, resume).then((o) => {
        if (left()) return;
        const out = timedOut ? { ...o, timedOut: true } : o;
        this.deps.onOutcome(out);
        this.deps.onFailed(false);
        this.setBusy(false);
        this.schedule(out.nextAt);
      });
    }).catch((err: unknown) => {
      if (left()) return;
      this.deps.onFailed(true, err);
      this.setBusy(false);
      // owner 15 Nov: a failed check is tried again - at the next bar close when it is still ahead, else a little later
      const next = this.lastNext != null && this.lastNext > this.deps.now() ? this.lastNext : this.deps.now() + RETRY_AFTER_ERROR_MS;
      this.schedule(next);
    });
  }

  private schedule(nextAt: number | null): void {
    const wait = waitUntil(nextAt, this.deps.now());
    if (wait == null) return;
    this.lastNext = nextAt;
    const run = this.run;
    // owner 15 Nov: a wait longer than one timer (a weekend, a holiday) is waited out, never a check before the bar closes
    const capped = nextAt != null && nextAt - this.deps.now() > MAX_WAIT_MS;
    this.timer = this.deps.setTimer(() => {
      this.timer = null;
      if (run !== this.run) return;
      if (capped) this.schedule(nextAt); else this.check();
    }, wait);
  }
}
