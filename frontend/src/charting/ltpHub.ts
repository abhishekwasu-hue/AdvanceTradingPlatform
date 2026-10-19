/**
 * CH5-a review (ADR-0004): one last-price poll per (symbol, exchange) for the whole workspace, shared by every chart
 * that shows it. Each subscriber asks for an interval (5 s for the active chart, 20 s for the others); the symbol is
 * polled at the shortest interval any of its subscribers wants, and only while the tab is visible. With 8 charts on
 * 3 symbols that is at most 3 requests per period instead of 8, and the inactive charts' symbols 4x less often.
 * The server limits /ltp per user and draws every broker call from the session's budget on top of this.
 */
import type { LtpResponse } from "../types";

export const ACTIVE_LTP_MS = 5_000;
export const INACTIVE_LTP_MS = 20_000;

type Fetch = (symbol: string, exchange: string) => Promise<LtpResponse>;
export interface LtpUpdate { ltp: LtpResponse | null; error: string | null }
type Listener = (u: LtpUpdate) => void;

interface Entry { subs: Map<number, { intervalMs: number; listener: Listener }>; timer: ReturnType<typeof setInterval> | null; period: number;
                  last: LtpUpdate | null; inFlight: boolean }

export class LtpHub {
  private entries = new Map<string, Entry>();
  private recent = new Map<string, { update: LtpUpdate; at: number }>();   // survives a resubscribe (focus moving between charts)
  private nextId = 1;

  constructor(private readonly fetchLtp: Fetch, private readonly visible: () => boolean = () => typeof document === "undefined" || document.visibilityState === "visible",
              private readonly now: () => number = () => Date.now()) {}

  static key(symbol: string, exchange: string) { return `${exchange.trim().toUpperCase()}:${symbol.trim().toUpperCase()}`; }

  /** Starts (or joins) the poll of one symbol; returns the unsubscribe. The latest result is handed over at once. */
  subscribe(symbol: string, exchange: string, intervalMs: number, listener: Listener): () => void {
    const key = LtpHub.key(symbol, exchange);
    let entry = this.entries.get(key);
    let fresh = false;
    if (!entry) {
      const cached = this.recent.get(key);
      entry = { subs: new Map(), timer: null, period: 0, last: cached?.update ?? null, inFlight: false };
      this.entries.set(key, entry);
      fresh = cached !== undefined && this.now() - cached.at < intervalMs;      // no extra request just because focus moved
    }
    const id = this.nextId++;
    entry.subs.set(id, { intervalMs, listener });
    if (entry.last) listener(entry.last);
    const first = entry.timer === null;
    this.reschedule(key, entry, symbol, exchange);
    if (first && !fresh) void this.poll(key, entry, symbol, exchange);
    return () => {
      const e = this.entries.get(key);
      if (!e) return;
      e.subs.delete(id);
      if (e.subs.size === 0) {
        if (e.timer !== null) clearInterval(e.timer);
        this.entries.delete(key);
      } else {
        this.reschedule(key, e, symbol, exchange);
      }
    };
  }

  /** How many symbols are being polled, and at what period (tests, diagnostics). */
  polls(): { key: string; periodMs: number; subscribers: number }[] {
    return [...this.entries.entries()].map(([key, e]) => ({ key, periodMs: e.period, subscribers: e.subs.size }));
  }

  private reschedule(key: string, entry: Entry, symbol: string, exchange: string) {
    const period = Math.min(...[...entry.subs.values()].map((s) => s.intervalMs));
    if (period === entry.period && entry.timer !== null) return;
    if (entry.timer !== null) clearInterval(entry.timer);
    entry.period = period;
    entry.timer = setInterval(() => { void this.poll(key, entry, symbol, exchange); }, period);
  }

  private async poll(key: string, entry: Entry, symbol: string, exchange: string) {
    if (entry.inFlight || !this.visible() || this.entries.get(key) !== entry) return;
    entry.inFlight = true;
    let update: LtpUpdate;
    try {
      update = { ltp: await this.fetchLtp(symbol, exchange), error: null };
    } catch (e) {
      update = { ltp: entry.last?.ltp ?? null, error: String(e) };
    } finally {
      entry.inFlight = false;
    }
    if (this.entries.get(key) !== entry) return;
    entry.last = update;
    this.recent.set(key, { update, at: this.now() });
    for (const s of entry.subs.values()) s.listener(update);
  }

  dispose() {
    for (const e of this.entries.values()) if (e.timer !== null) clearInterval(e.timer);
    this.entries.clear();
    this.recent.clear();
  }
}
