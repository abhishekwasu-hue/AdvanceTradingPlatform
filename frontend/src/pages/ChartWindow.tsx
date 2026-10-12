import { useEffect, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useBrokerChart } from "../components/chartHistory";
import ProChart, { useLiveLtp } from "../components/ProChart";
import { SymbolTierBadge } from "../components/DataTierBadge";
import type { ChartEngine } from "../charting/engine";
import { MarketZonesLayer } from "../charting/MarketZonesLayer";
import { api } from "../api/client";

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
  const [engine, setEngine] = useState<ChartEngine | null>(null);
  // CH5: "Open in workspace" only while the multi-chart workspace is on for this organisation.
  const [workspaceOn, setWorkspaceOn] = useState(false);
  useEffect(() => {
    if (!user) return;
    let live = true;
    api.myFeatures().then((f) => { if (live) setWorkspaceOn(f.chart_workspace === true); }).catch(() => undefined);
    return () => { live = false; };
  }, [user]);
  const lastClose = chart.candles.length ? chart.candles[chart.candles.length - 1].close : null;

  if (loading) return <div className="p-6 text-sm text-fg-muted">Loading…</div>;
  if (!user) return <div className="p-6 text-sm text-fg">Log in in the main tab first, then open the chart again.</div>;
  if (!symbol) return <div className="p-6 text-sm text-fg">No symbol given.</div>;

  return (
    <div className="min-h-screen bg-surface p-4">
      <h1 className="sr-only">{symbol} chart</h1>
      <div className="mb-2 flex items-center gap-3 text-xs text-fg-muted">
        <span className="font-semibold text-fg">{exchange}</span>
        <SymbolTierBadge symbol={symbol} exchange={exchange} live={live.ltp?.data_tier} />
        {broker && <span>candles via {broker}</span>}
        <span>refreshes every minute · live price every 5 s · scroll left for older history</span>
        {workspaceOn && (
          <a href={`${window.location.pathname}?${new URLSearchParams({ workspace: "1", chart: symbol, tf: timeframe, exchange }).toString()}`}
             className="rounded border border-border px-1.5 py-0.5 font-semibold text-fg hover:bg-surface-2">Open in workspace</a>
        )}
        {error && <span className="text-down">{error}</span>}
      </div>
      <ProChart candles={chart.candles} symbol={symbol} timeframe={timeframe} timeframes={TIMEFRAMES} onTimeframeChange={setTimeframe}
                live={timeframe === "day" ? null : live.ltp} liveError={live.error} fullWindow deployable exchange={exchange}
                defaultIndicators={["ema_fast", "ema_slow", "vwap", "volume", "rsi"]}
                onLoadOlder={chart.loadOlder} loadingOlder={chart.loadingOlder} olderExhausted={chart.exhausted} onEngine={setEngine} />
      {/* Z7a-3: stored market zones (flag market_zones; nothing shows while it is off) */}
      <div className="mt-2"><MarketZonesLayer engine={engine} instrument={`${exchange}:${symbol}`} price={lastClose} /></div>
    </div>
  );
}
