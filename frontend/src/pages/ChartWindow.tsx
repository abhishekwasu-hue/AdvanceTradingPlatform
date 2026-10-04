import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import ProChart, { useLiveLtp } from "../components/ProChart";
import type { OHLCVBar } from "../types";

const TIMEFRAMES = ["1min", "5min", "15min", "30min", "60min", "day"];

function lookbackFor(tf: string): number {
  if (tf === "day") return 365;
  if (tf === "60min") return 20;
  if (tf === "30min") return 10;
  return 5; // the last session is always inside 5 calendar days, weekends and holidays included
}

/**
 * A single broker chart filling a whole browser tab, opened from a chart's "New tab" button:
 * `/?chart=NIFTY%2050&tf=5min&exchange=NSE&broker_name=upstox`. It uses the same login as the main
 * tab (the session lives in this browser), fetches the candles itself, refreshes them every minute
 * and moves the forming candle on the live price.
 */
export default function ChartWindow({ params }: { params: URLSearchParams }) {
  const { user, loading } = useAuth();
  const symbol = (params.get("chart") ?? "").trim().toUpperCase();
  const exchange = (params.get("exchange") ?? "NSE").toUpperCase();
  const broker = params.get("broker_name") || undefined;
  const [timeframe, setTimeframe] = useState(TIMEFRAMES.includes(params.get("tf") ?? "") ? (params.get("tf") as string) : "5min");
  const [candles, setCandles] = useState<OHLCVBar[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { document.title = `${symbol} · ${timeframe} · Advance Trading`; }, [symbol, timeframe]);

  useEffect(() => {
    if (!user || !symbol) return;
    let cancelled = false;
    const load = async () => {
      try {
        const r = await api.marketDataCandles([symbol], timeframe, lookbackFor(timeframe), exchange, broker);
        if (cancelled) return;
        const entry = r.symbols[symbol];
        if (!entry || entry.error) throw new Error(entry?.error ?? `No candles for ${symbol}`);
        setCandles(entry.bars); setError(null);
      } catch (e) { if (!cancelled) setError(String(e)); }
    };
    void load();
    const id = window.setInterval(load, 60_000);
    return () => { cancelled = true; window.clearInterval(id); };
  }, [user, symbol, timeframe, exchange, broker]);

  const live = useLiveLtp(!!user && !!symbol && timeframe !== "day", symbol, exchange, broker);

  if (loading) return <div className="p-6 text-sm text-muted">Loading…</div>;
  if (!user) return <div className="p-6 text-sm text-slate-200">Log in in the main tab first, then open the chart again.</div>;
  if (!symbol) return <div className="p-6 text-sm text-slate-200">No symbol given.</div>;

  return (
    <div className="min-h-screen bg-bg p-4">
      <div className="mb-2 flex items-center gap-3 text-xs text-muted">
        <span className="font-semibold text-slate-200">{exchange}</span>
        {broker && <span>candles via {broker}</span>}
        <span>refreshes every minute · live price every 5 s</span>
        {error && <span className="text-rose-300">{error}</span>}
      </div>
      <ProChart candles={candles} symbol={symbol} timeframe={timeframe} timeframes={TIMEFRAMES} onTimeframeChange={setTimeframe}
                live={timeframe === "day" ? null : live.ltp} liveError={live.error} fullWindow deployable exchange={exchange}
                defaultIndicators={["ema_fast", "ema_slow", "vwap", "volume", "rsi"]} />
    </div>
  );
}
