import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useBrokerChart } from "../components/chartHistory";
import ProChart, { useLiveLtp } from "../components/ProChart";
import { SymbolTierBadge } from "../components/DataTierBadge";
import type { ChartEngine } from "../charting/engine";
import { MarketZonesLayer } from "../charting/MarketZonesLayer";
import { PriceActionLayer } from "../charting/PriceActionLayer";
import { ReplayBar } from "../charting/ReplayBar";
import { layerTf } from "../charting/priceAction";
import { REPLAY_OFF, replayAt, visibleCandles, windowStart, type ReplayState } from "../charting/replay";

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
  // N4 CH3: bar replay - the chart shows the candles up to the replay bar, the layers are read as of that bar's end
  const [replay, setReplay] = useState<ReplayState>(REPLAY_OFF);
  const [paAvailable, setPaAvailable] = useState(false);
  useEffect(() => { setReplay(REPLAY_OFF); }, [symbol, timeframe]);
  const shown = useMemo(() => visibleCandles(chart.candles, replay), [chart.candles, replay]);
  const at = replayAt(chart.candles, replay, layerTf(timeframe));
  const onWindow = useCallback((start: string) => setReplay((r) => (r.active && !r.start ? { ...r, start } : r)), []);
  const lastClose = shown.length ? shown[shown.length - 1].close : null;

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
        {error && <span className="text-down">{error}</span>}
      </div>
      <ProChart candles={shown} symbol={symbol} timeframe={timeframe} timeframes={TIMEFRAMES} onTimeframeChange={setTimeframe}
                live={timeframe === "day" || replay.active ? null : live.ltp} liveError={live.error} fullWindow deployable exchange={exchange}
                defaultIndicators={["ema_fast", "ema_slow", "vwap", "volume", "rsi"]}
                onLoadOlder={replay.active ? undefined : chart.loadOlder} loadingOlder={chart.loadingOlder} olderExhausted={chart.exhausted} onEngine={setEngine} />
      {/* N4 CH3: price-action layer + replay (flag chart_price_action; nothing shows while it is off) */}
      {paAvailable && <div className="mt-2"><ReplayBar state={replay} count={chart.candles.length} at={at} onChange={setReplay} /></div>}
      <div className="mt-2"><PriceActionLayer engine={engine} instrument={`${exchange}:${symbol}`} timeframe={timeframe} at={at}
                                              start={windowStart(at, replay.start)} onAvailable={setPaAvailable} onWindow={onWindow}
                                              refreshKey={replay.active ? null : chart.candles[chart.candles.length - 1]?.timestamp ?? null} /></div>
      {/* Z7a-3: stored market zones (flag market_zones; nothing shows while it is off) - in replay, as of the replay bar */}
      <div className="mt-2"><MarketZonesLayer engine={engine} instrument={`${exchange}:${symbol}`} price={lastClose} at={at} /></div>
    </div>
  );
}
