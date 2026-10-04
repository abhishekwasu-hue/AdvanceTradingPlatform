import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { OHLCVBar } from "../types";

/**
 * Broker charts load the history each timeframe needs on their own: switching to 15m or 60m or
 * "day" fetches enough calendar days for a readable chart (a few hundred bars), and scrolling the
 * chart back past its oldest bar fetches the page before it (`before`), until the broker has no
 * more. The newest bars refresh every minute without losing the older pages.
 */

/** Calendar days fetched on first load per timeframe (intraday is built from 1-minute bars, 30 days at most per request). */
export function historyDaysFor(tf: string): number {
  switch (tf) {
    case "1min": return 5;
    case "3min": return 7;
    case "5min": return 10;
    case "15min": return 20;
    case "30min":
    case "60min": return 30;
    case "day": return 730;
    default: return 10;
  }
}

/** Calendar days per "load older" page. */
export function olderPageDaysFor(tf: string): number {
  return tf === "day" ? 730 : Math.min(30, Math.max(5, historyDaysFor(tf)));
}

const ms = (b: OHLCVBar) => new Date(b.timestamp).getTime();

/** Union of two bar lists by time, ascending; `newer` wins where both have a bar. */
export function mergeBars(older: OHLCVBar[], newer: OHLCVBar[]): OHLCVBar[] {
  const byTime = new Map<number, OHLCVBar>();
  for (const b of older) byTime.set(ms(b), b);
  for (const b of newer) byTime.set(ms(b), b);
  return [...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([, b]) => b);
}

/** The IST calendar date (YYYY-MM-DD) of a bar. */
export function istDate(b: OHLCVBar): string {
  return new Date(b.timestamp).toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
}

export interface BrokerChart {
  candles: OHLCVBar[];
  loading: boolean;
  error: string | null;
  loadingOlder: boolean;
  /** The broker had nothing older (or refused): scrolling back stops asking. */
  exhausted: boolean;
  loadOlder: () => void;
}

export function useBrokerChart({ enabled, symbol, timeframe, exchange = "NSE", broker, refreshMs = 60_000 }: {
  enabled: boolean; symbol?: string; timeframe: string; exchange?: string; broker?: string; refreshMs?: number;
}): BrokerChart {
  const [candles, setCandles] = useState<OHLCVBar[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [exhausted, setExhausted] = useState(false);
  const sym = (symbol ?? "").trim().toUpperCase();
  const keyRef = useRef("");
  const candlesRef = useRef<OHLCVBar[]>([]);
  const olderBusy = useRef(false);
  const exhaustedRef = useRef(false);
  candlesRef.current = candles;

  useEffect(() => {
    const key = `${sym}|${timeframe}|${exchange}|${broker ?? ""}`;
    keyRef.current = key;
    setCandles([]); setError(null); setExhausted(false); exhaustedRef.current = false; olderBusy.current = false; setLoadingOlder(false);
    if (!enabled || !sym) return;
    let first = true;
    const load = async () => {
      if (first) setLoading(true);
      try {
        // First load: the timeframe's full window; refreshes: the recent days only, merged in.
        const days = first ? historyDaysFor(timeframe) : timeframe === "day" ? 10 : 2;
        const r = await api.marketDataCandles([sym], timeframe, days, exchange, broker);
        if (keyRef.current !== key) return;
        const entry = r.symbols[sym];
        if (!entry || entry.error) throw new Error(entry?.error ?? `No candles for ${sym}`);
        setCandles((prev) => (first ? entry.bars : mergeBars(prev, entry.bars)));
        setError(null);
      } catch (e) {
        if (keyRef.current === key) setError(String(e).replace(/^Error:\s*/, ""));
      } finally {
        if (keyRef.current === key && first) setLoading(false);
        first = false;
      }
    };
    void load();
    const id = window.setInterval(load, refreshMs);
    return () => { window.clearInterval(id); };
  }, [enabled, sym, timeframe, exchange, broker, refreshMs]);

  const loadOlder = useCallback(() => {
    const bars = candlesRef.current;
    if (!enabled || !sym || bars.length === 0 || olderBusy.current || exhaustedRef.current) return;
    const key = keyRef.current;
    const oldest = ms(bars[0]);
    olderBusy.current = true; setLoadingOlder(true);
    api.marketDataCandles([sym], timeframe, olderPageDaysFor(timeframe), exchange, broker, istDate(bars[0]))
      .then((r) => {
        if (keyRef.current !== key) return;
        const entry = r.symbols[sym];
        const older = (entry?.bars ?? []).filter((b) => ms(b) < oldest);
        if (!entry || entry.error || older.length === 0) { exhaustedRef.current = true; setExhausted(true); return; }
        setCandles((prev) => mergeBars(older, prev));
      })
      .catch(() => { if (keyRef.current === key) { exhaustedRef.current = true; setExhausted(true); } })
      .finally(() => { if (keyRef.current === key) { olderBusy.current = false; setLoadingOlder(false); } });
  }, [enabled, sym, timeframe, exchange, broker]);

  return { candles, loading, error, loadingOlder, exhausted, loadOlder };
}
