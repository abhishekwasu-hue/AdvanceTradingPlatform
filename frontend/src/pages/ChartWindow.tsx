import { useEffect, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useBrokerChart } from "../components/chartHistory";
import ProChart, { useLiveLtp } from "../components/ProChart";

const TIMEFRAMES = ["1min", "5min", "15min", "30min", "60min", "day"];

/**
 * A single broker chart filling a whole browser tab, opened from a chart's "New tab" button:
 * `/?chart=NIFTY%2050&tf=5min&exchange=NSE&broker_name=upstox`. It uses the same login as the main
 * tab (the session lives in this browser), fetches the candles itself - each timeframe's own
 * history, older pages as the chart is scrolled back - refreshes them every minute and moves the
 * forming candle on the live price.
 */
export default function ChartWindow({ params }: { params: URLSearchParams }) {
  const { user, loading } = useAuth();
  const symbol = (params.get("chart") ?? "").trim().toUpperCase();
  const exchange = (params.get("exchange") ?? "NSE").toUpperCase();
  const broker = params.get("broker_name") || undefined;
  const [timeframe, setTimeframe] = useState(TIMEFRAMES.includes(params.get("tf") ?? "") ? (params.get("tf") as string) : "5min");

  useEffect(() => { document.title = `${symbol} · ${timeframe} · Advance Trading`; }, [symbol, timeframe]);

  const chart = useBrokerChart({ enabled: !!user && !!symbol, symbol, timeframe, exchange, broker });
  const error = chart.error;

  const live = useLiveLtp(!!user && !!symbol && timeframe !== "day", symbol, exchange, broker);

  if (loading) return <div className="p-6 text-sm text-muted">Loading…</div>;
  if (!user) return <div className="p-6 text-sm text-slate-200">Log in in the main tab first, then open the chart again.</div>;
  if (!symbol) return <div className="p-6 text-sm text-slate-200">No symbol given.</div>;

  return (
    <div className="min-h-screen bg-bg p-4">
      <h1 className="sr-only">{symbol} chart</h1>
      <div className="mb-2 flex items-center gap-3 text-xs text-muted">
        <span className="font-semibold text-slate-200">{exchange}</span>
        {broker && <span>candles via {broker}</span>}
        <span>refreshes every minute · live price every 5 s · scroll left for older history</span>
        {error && <span className="text-rose-300">{error}</span>}
      </div>
      <ProChart candles={chart.candles} symbol={symbol} timeframe={timeframe} timeframes={TIMEFRAMES} onTimeframeChange={setTimeframe}
                live={timeframe === "day" ? null : live.ltp} liveError={live.error} fullWindow deployable exchange={exchange}
                defaultIndicators={["ema_fast", "ema_slow", "vwap", "volume", "rsi"]}
                onLoadOlder={chart.loadOlder} loadingOlder={chart.loadingOlder} olderExhausted={chart.exhausted} />
    </div>
  );
}
