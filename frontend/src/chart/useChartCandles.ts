/**
 * UI2-4b2 (Charts page plan §3): the candles of the v2 chart - the timeframe's history (`timeframes.ts` page sizes,
 * week and month included), today's forming bar for day / week / month (UI2-4a2 `forming`), an index's volume from its
 * near-month future (`volume_from_future`, labelled), a refresh of the newest bars every minute, and older history when
 * the trader scrolls back.
 *
 * The chart asks for older history on every scroll step while it is near its oldest bar, so `OlderGuard` lets one
 * request out at a time, stops for good when the broker has nothing older, and waits a little after a failed request.
 * Errors are plain: what happened, then what to do.
 *
 * UI2-4d (the Multi-chart tab): `paused` holds a chart's reads while it is off screen or the browser tab is hidden
 * (`FeedGate`), and `fetchCandles` lets the tab's cells share one `CandlePool` (chart/multiChart.ts). Left out, the
 * chart reads exactly as before.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { CandleVolumeSource, CandlesResponse, OHLCVBar } from "../types";
import { mergeBars, istDate } from "../components/chartHistory";
import { DAILY_OR_LONGER, historyDaysFor, olderPageDaysFor, type ChartBar, type TimeframeId } from "./timeframes";

/** After a failed "older" request, the next one waits this long (the chart keeps asking while it sits at the edge). */
export const OLDER_RETRY_MS = 30_000;

/** One "older history" request at a time per chart; none after the broker had nothing older. Each `reset` (a new
 * symbol or timeframe, a retry, or switching back to an earlier symbol) starts a new generation, and an answer is taken
 * only by the generation that asked for it. Pure. */
export class OlderGuard {
  private gen = 0;
  private busy = false;
  private done = false;
  private waitUntil = 0;

  constructor(private readonly now: () => number = () => Date.now()) {}

  /** A new chart (or the same one loaded again): everything starts again; answers to earlier requests are dropped. */
  reset(): void { this.gen += 1; this.busy = false; this.done = false; this.waitUntil = 0; }

  get exhausted(): boolean { return this.done; }

  /** A ticket when a request may go out now (it is then in flight), else null. */
  begin(haveBars: boolean): number | null {
    if (!haveBars || this.busy || this.done || this.now() < this.waitUntil) return null;
    this.busy = true;
    return this.gen;
  }

  /** True while `ticket` belongs to the current chart (its answer may be shown). */
  current(ticket: number): boolean { return ticket === this.gen; }

  /** The answer for `ticket`: how many older bars came (0 = the broker has none older), or null when the request
   * failed or the broker could not answer this time (it is asked again after a pause). */
  end(ticket: number, added: number | null): void {
    if (ticket !== this.gen) return;                     // an answer for an earlier chart
    this.busy = false;
    if (added === null) this.waitUntil = this.now() + OLDER_RETRY_MS;
    else if (added === 0) this.done = true;
  }
}

/** Days of the newest bars a refresh reads (merged into what is shown). Week / month read past one whole period. */
export function refreshDaysFor(tf: TimeframeId): number {
  return tf === "month" ? 70 : tf === "week" ? 21 : tf === "day" ? 10 : 2;
}

/** A refresh's bars to merge: for week / month the first one may be cut by the window's start, so it is left out. */
export function refreshBars<T extends OHLCVBar>(bars: T[], tf: TimeframeId): T[] {
  return tf === "week" || tf === "month" ? bars.slice(1) : bars;
}

/** Plain words for a failed read: what happened, then what to do. */
export function candleErrorText(error: unknown, symbol: string): string {
  if (error instanceof ApiError && (error.status === 401 || error.status === 409 || error.status === 412)) {
    return "Your broker session has ended. Log in to your broker again, then reload this page.";
  }
  if (error instanceof ApiError && error.status === 429) return "Too many chart requests in a short time. Wait a minute, then try again.";
  if (error instanceof Error && error.message === "budget used") {
    return "Your broker's request limit is used up for the moment. The chart tries again within a minute.";
  }
  if (error instanceof Error && error.message === "no bars") return `No prices for ${symbol} from your broker. Check the symbol, or pick another one.`;
  return "Prices could not be loaded from your broker. Check your connection, then try again.";
}

type CandleEntry = CandlesResponse["symbols"][string];

export interface FeedEvents {
  loading: (on: boolean) => void;
  /** The first answer: the whole history (replaces what is shown). */
  replace: (bars: ChartBar[], volume: CandleVolumeSource | null) => void;
  /** A refresh: the newest bars, merged into what is shown. */
  merge: (bars: ChartBar[], volume: CandleVolumeSource | null) => void;
  error: (text: string | null) => void;
}

/** UI2-4d: when a chart's reads may go out. Running, each refresh tick reads; paused (off screen, a hidden tab), nothing
 * is sent and a tick that falls due - the first load included - waits until the chart runs again, once. Pure. */
export class FeedGate {
  private due = false;

  constructor(private readonly tick: () => void, private paused = false) {
    if (paused) this.due = true;
    else tick();
  }

  /** The refresh timer fired. */
  timer(): void {
    if (this.paused) this.due = true;
    else this.tick();
  }

  setPaused(paused: boolean): void {
    this.paused = paused;
    if (!paused && this.due) { this.due = false; this.tick(); }
  }
}

/** The candle read, with an optional last argument: this chart's signal, aborted when it stops (a shared pool then
 * drops a read nobody waits for; the plain API call ignores it). */
export type FetchCandles = (...args: [...Parameters<typeof api.marketDataCandles>, AbortSignal?]) => ReturnType<typeof api.marketDataCandles>;

/** One chart's reads: the timeframe's whole history until one read succeeds (a failed first read is retried whole on
 * the next tick, never as a short refresh), then the newest days. After `stop` no answer is used, so a late answer
 * never lands on another chart - even one with the same symbol and timeframe (a retry, or switching back). Pure. */
export class CandleFeed {
  private loaded = false;
  private stopped = false;
  private inFlight = false;

  constructor(private readonly fetchDays: (days: number) => Promise<CandlesResponse>, private readonly symbol: string,
              private readonly timeframe: TimeframeId, private readonly on: FeedEvents) {}

  async tick(): Promise<void> {
    if (this.stopped || this.inFlight) return;
    this.inFlight = true;
    const first = !this.loaded;
    if (first) this.on.loading(true);
    try {
      const r = await this.fetchDays(first ? historyDaysFor(this.timeframe) : refreshDaysFor(this.timeframe));
      if (this.stopped) return;
      const entry: CandleEntry | undefined = r.symbols[this.symbol];
      if (!entry || entry.error) throw new Error(entry?.error === "budget used" ? "budget used" : "no bars");
      if (first) this.on.replace(entry.bars, entry.volume_source ?? null);
      else this.on.merge(refreshBars(entry.bars, this.timeframe), entry.volume_source ?? null);
      this.loaded = true;
      this.on.error(null);
    } catch (e) {
      if (!this.stopped && first) this.on.error(candleErrorText(e, this.symbol));     // a failed refresh keeps the chart
    } finally {
      this.inFlight = false;
      if (!this.stopped && first) this.on.loading(false);
    }
  }

  stop(): void { this.stopped = true; }
}

export interface ChartCandles {
  candles: ChartBar[];
  loading: boolean;
  error: string | null;
  loadingOlder: boolean;
  exhausted: boolean;
  volumeSource: CandleVolumeSource | null;
  loadOlder: () => void;
  retry: () => void;
}

export function useChartCandles({ enabled, symbol, exchange, timeframe, refreshMs = 60_000, paused = false, fetchCandles = api.marketDataCandles }: {
  enabled: boolean; symbol: string; exchange: string; timeframe: TimeframeId; refreshMs?: number;
  /** UI2-4d: nothing is read while true (the chart is off screen or its tab hidden); a missed read follows on resume. */
  paused?: boolean;
  /** UI2-4d: the candle read (a Multi-chart cell passes its tab's shared pool). */
  fetchCandles?: FetchCandles;
}): ChartCandles {
  const [candles, setCandles] = useState<ChartBar[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [exhausted, setExhausted] = useState(false);
  const [volumeSource, setVolumeSource] = useState<CandleVolumeSource | null>(null);
  const [nonce, setNonce] = useState(0);
  const sym = symbol.trim().toUpperCase();
  const candlesRef = useRef<ChartBar[]>([]);
  candlesRef.current = candles;
  const guard = useRef(new OlderGuard());
  const fetchRef = useRef(fetchCandles);
  fetchRef.current = fetchCandles;
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const gate = useRef<FeedGate | null>(null);
  const stopRef = useRef<AbortController | null>(null);

  useEffect(() => {
    guard.current.reset();
    setCandles([]); setError(null); setExhausted(false); setLoadingOlder(false); setVolumeSource(null);
    if (!enabled || !sym) return undefined;
    const v2 = { forming: DAILY_OR_LONGER.has(timeframe), volume_from_future: true };
    const stop = new AbortController();
    stopRef.current = stop;
    const feed = new CandleFeed((days) => fetchRef.current([sym], timeframe, days, exchange, undefined, undefined, v2, stop.signal), sym, timeframe, {
      loading: setLoading,
      replace: (bars, volume) => { setCandles(bars); setVolumeSource(volume); },
      merge: (bars, volume) => { setCandles((prev) => mergeBars(prev, bars)); setVolumeSource(volume); },
      error: setError,
    });
    const g = new FeedGate(() => void feed.tick(), pausedRef.current);
    gate.current = g;
    const id = window.setInterval(() => g.timer(), refreshMs);
    return () => { feed.stop(); stop.abort(); window.clearInterval(id); setLoading(false); if (gate.current === g) gate.current = null; };
  }, [enabled, sym, exchange, timeframe, refreshMs, nonce]);
  useEffect(() => { gate.current?.setPaused(paused); }, [paused]);

  const loadOlder = useCallback(() => {
    const bars = candlesRef.current;
    const ticket = enabled ? guard.current.begin(bars.length > 0) : null;
    if (ticket === null) return;
    const oldest = Date.parse(bars[0].timestamp);
    setLoadingOlder(true);
    fetchRef.current([sym], timeframe, olderPageDaysFor(timeframe), exchange, undefined, istDate(bars[0]),
                     { forming: false, volume_from_future: true }, stopRef.current?.signal)
      .then((r) => {
        const entry = r.symbols[sym];
        // Only a good answer with nothing older ends the paging; a per-symbol error (budget, a broker hiccup) is retried.
        const older = entry && !entry.error ? entry.bars.filter((b) => Date.parse(b.timestamp) < oldest) : null;
        guard.current.end(ticket, older ? older.length : null);
        if (guard.current.current(ticket) && older?.length) setCandles((prev) => mergeBars(older, prev));
      })
      .catch(() => guard.current.end(ticket, null))
      .finally(() => { if (guard.current.current(ticket)) { setLoadingOlder(false); setExhausted(guard.current.exhausted); } });
  }, [enabled, sym, timeframe, exchange]);

  const retry = useCallback(() => setNonce((n) => n + 1), []);
  return { candles, loading, error, loadingOlder, exhausted, volumeSource, loadOlder, retry };
}
