/**
 * UI2-4b2 (Charts page plan §3): the candles of the v2 chart - the timeframe's history (`timeframes.ts` page sizes,
 * week and month included), today's forming bar for day / week / month (UI2-4a2 `forming`), an index's volume from its
 * near-month future (`volume_from_future`, labelled), a refresh of the newest bars every minute, and older history when
 * the trader scrolls back.
 *
 * The chart asks for older history on every scroll step while it is near its oldest bar, so `OlderGuard` lets one
 * request out at a time, stops for good when the broker has nothing older, and waits a little after a failed request.
 * Errors are plain: what happened, then what to do.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { CandleVolumeSource, OHLCVBar } from "../types";
import { mergeBars, istDate } from "../components/chartHistory";
import { DAILY_OR_LONGER, historyDaysFor, olderPageDaysFor, type ChartBar, type TimeframeId } from "./timeframes";

/** After a failed "older" request, the next one waits this long (the chart keeps asking while it sits at the edge). */
export const OLDER_RETRY_MS = 30_000;

/** One "older history" request at a time per chart key; none after the broker had nothing older. Pure. */
export class OlderGuard {
  private key = "";
  private busy = false;
  private done = false;
  private waitUntil = 0;

  constructor(private readonly now: () => number = () => Date.now()) {}

  /** A new symbol / timeframe: everything starts again. */
  reset(key: string): void { this.key = key; this.busy = false; this.done = false; this.waitUntil = 0; }

  get exhausted(): boolean { return this.done; }

  /** True when a request may go out now for `key` (and marks it in flight). */
  begin(key: string, haveBars: boolean): boolean {
    if (key !== this.key || !haveBars || this.busy || this.done || this.now() < this.waitUntil) return false;
    this.busy = true;
    return true;
  }

  /** The answer for `key`: how many older bars came (0 = the broker has none), or null when the request failed. */
  end(key: string, added: number | null): void {
    if (key !== this.key) return;                       // an answer for the previous symbol or timeframe
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

export function useChartCandles({ enabled, symbol, exchange, timeframe, refreshMs = 60_000 }: {
  enabled: boolean; symbol: string; exchange: string; timeframe: TimeframeId; refreshMs?: number;
}): ChartCandles {
  const [candles, setCandles] = useState<ChartBar[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [exhausted, setExhausted] = useState(false);
  const [volumeSource, setVolumeSource] = useState<CandleVolumeSource | null>(null);
  const [nonce, setNonce] = useState(0);
  const sym = symbol.trim().toUpperCase();
  const key = `${sym}|${exchange}|${timeframe}`;
  const keyRef = useRef(key);
  const candlesRef = useRef<ChartBar[]>([]);
  candlesRef.current = candles;
  const guard = useRef(new OlderGuard());
  const v2 = { forming: DAILY_OR_LONGER.has(timeframe), volume_from_future: true };

  useEffect(() => {
    keyRef.current = key;
    guard.current.reset(key);
    setCandles([]); setError(null); setExhausted(false); setLoadingOlder(false); setVolumeSource(null);
    if (!enabled || !sym) return undefined;
    let first = true;
    const load = async () => {
      if (first) setLoading(true);
      try {
        const r = await api.marketDataCandles([sym], timeframe, first ? historyDaysFor(timeframe) : refreshDaysFor(timeframe), exchange,
                                              undefined, undefined, v2);
        if (keyRef.current !== key) return;
        const entry = r.symbols[sym];
        if (!entry || entry.error) throw new Error(entry?.error === "budget used" ? "budget used" : "no bars");
        setCandles((prev) => (first ? entry.bars : mergeBars(prev, refreshBars(entry.bars, timeframe))));
        setVolumeSource(entry.volume_source ?? null);
        setError(null);
      } catch (e) {
        if (keyRef.current === key && first) setError(candleErrorText(e, sym));      // a failed refresh keeps the chart
      } finally {
        if (keyRef.current === key && first) setLoading(false);
        first = false;
      }
    };
    void load();
    const id = window.setInterval(load, refreshMs);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, key, refreshMs, nonce]);

  const loadOlder = useCallback(() => {
    const bars = candlesRef.current;
    if (!enabled || !guard.current.begin(key, bars.length > 0)) return;
    const oldest = Date.parse(bars[0].timestamp);
    setLoadingOlder(true);
    api.marketDataCandles([sym], timeframe, olderPageDaysFor(timeframe), exchange, undefined, istDate(bars[0]),
                          { forming: false, volume_from_future: true })
      .then((r) => {
        const entry = r.symbols[sym];
        const older = entry && !entry.error ? entry.bars.filter((b) => Date.parse(b.timestamp) < oldest) : null;
        guard.current.end(key, older ? older.length : entry?.error === "budget used" ? null : 0);
        if (keyRef.current === key && older?.length) setCandles((prev) => mergeBars(older, prev));
      })
      .catch(() => guard.current.end(key, null))
      .finally(() => { if (keyRef.current === key) { setLoadingOlder(false); setExhausted(guard.current.exhausted); } });
  }, [enabled, key, sym, timeframe, exchange]);

  const retry = useCallback(() => setNonce((n) => n + 1), []);
  return { candles, loading, error, loadingOlder, exhausted, volumeSource, loadOlder, retry };
}
