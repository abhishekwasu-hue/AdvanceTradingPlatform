import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useBrokerChart } from "../components/chartHistory";
import ProChart, { useLiveLtp } from "../components/ProChart";
import { SymbolTierBadge } from "../components/DataTierBadge";
import { Skeleton } from "../components/primitives";
import type { ChartEngine } from "../charting/engine";
import { MarketZonesLayer } from "../charting/MarketZonesLayer";
import { PriceActionLayer } from "../charting/PriceActionLayer";
import { StudiesPanel } from "../charting/StudiesPanel";
import { ReplayBar } from "../charting/ReplayBar";
import { TradesLayer } from "../charting/TradesLayer";
import { layerTf } from "../charting/priceAction";
import { REPLAY_OFF, parseCandleTime, replayAt, visibleCandles, windowStart, type ReplayState } from "../charting/replay";
import ExpectedMoveLayer from "../progression/ExpectedMoveLayer";
import { useFeatures } from "../ui/useFeatures";
import { chartWindowRedirect } from "../chart/chartsPage";
import { useWaitCap } from "../shell/shellWait";

const TIMEFRAMES = ["1min", "5min", "15min", "30min", "60min", "day"];
/** M75 NAV-3: the multi-chart workspace opens inside this window (its own chunk, read only when it is opened). */
const ChartWorkspace = lazy(() => import("./ChartWorkspace"));

/**
 * A single broker chart filling a whole browser tab, opened from a chart's "New tab" button:
 * `/?chart=NIFTY%2050&tf=5min&exchange=NSE&broker_name=upstox`. It uses the same login as the main
 * tab (the session lives in this browser), fetches the candles itself - each timeframe's own
 * history, older pages as the chart is scrolled back - refreshes them every minute and moves the
 * forming candle on the live price.
 */
export default function ChartWindow({ params }: { params: URLSearchParams }) {
  const { user, loading } = useAuth();
  // UI2-0: the flags come from the app's single /system/features read (useFeatures).
  const features = useFeatures();
  // UI2-4e: with `ui_v2` on, this window's link opens the Charts page's Chart tab instead (its timeframe and exchange),
  // and nothing is read here: the window waits for the flags, at most SHELL_WAIT_MS (as the shell does); a read that
  // has not answered by then counts as "every flag off" for this window's life. The Charts page has no `chart`
  // parameter, so this cannot loop, and the redirect is sent once. With `ui_v2` off: the chart as before.
  const capped = useWaitCap(!!user && !features.loaded);
  const [flagsOff, setFlagsOff] = useState(false);
  if (capped && !features.loaded && !flagsOff) setFlagsOff(true);
  const known = features.loaded || flagsOff;
  const v2 = !flagsOff && features.loaded && features.on("ui_v2");
  const reads = known && !v2;
  const redirected = useRef(false);
  useEffect(() => {
    if (!v2 || redirected.current) return;
    redirected.current = true;
    window.location.replace(chartWindowRedirect(params));
  }, [v2, params]);
  const symbol = (params.get("chart") ?? "").trim().toUpperCase();
  const exchange = (params.get("exchange") ?? "NSE").toUpperCase();
  const broker = params.get("broker_name") || undefined;
  const [timeframe, setTimeframe] = useState(TIMEFRAMES.includes(params.get("tf") ?? "") ? (params.get("tf") as string) : "5min");

  useEffect(() => { document.title = `${symbol} · ${timeframe} · AiAlgoDesk`; }, [symbol, timeframe]);

  const chart = useBrokerChart({ enabled: reads && !!user && !!symbol, symbol, timeframe, exchange, broker });
  const error = chart.error;
  // UI2-4e review: until the first candle answer the window shows a placeholder (M63 I-7: the shared Skeleton), so the
  // chart's empty-state line ("No candles yet …") never stands in for prices still on their way. After it the chart
  // stays, as before.
  const [firstRead, setFirstRead] = useState<"waiting" | "reading" | "done">("waiting");
  if (firstRead === "waiting" && chart.loading) setFirstRead("reading");
  if (firstRead === "reading" && !chart.loading) setFirstRead("done");

  const live = useLiveLtp(reads && !!user && !!symbol && timeframe !== "day", symbol, exchange, broker);
  const [engine, setEngine] = useState<ChartEngine | null>(null);
  // CH5: "Open in workspace" only while the multi-chart workspace is on for this organisation.
  const workspaceOn = !!user && features.on("chart_workspace");
  // M75 NAV-3: "Open in workspace" lays the workspace over this window in place (starting from this chart); "Back to
  // the chart" returns to the chart exactly as it was (it stays mounted underneath: timeframe, replay, indicators).
  const [workspace, setWorkspace] = useState(false);
  const workspaceParams = useMemo(() => new URLSearchParams({ workspace: "1", chart: symbol, tf: timeframe, exchange }), [symbol, timeframe, exchange]);
  // N4 CH3: bar replay - the chart shows the candles up to the replay bar, the layers are read as of that bar's end
  const [replay, setReplay] = useState<ReplayState>(REPLAY_OFF);
  const [paAvailable, setPaAvailable] = useState(false);
  useEffect(() => { setReplay(REPLAY_OFF); }, [symbol, timeframe]);
  const shown = useMemo(() => visibleCandles(chart.candles, replay), [chart.candles, replay]);
  const at = replayAt(chart.candles, replay, layerTf(timeframe));
  const onWindow = useCallback((start: string) => setReplay((r) => (r.active && !r.start ? { ...r, start } : r)), []);
  const lastClose = shown.length ? shown[shown.length - 1].close : null;
  const candleTimes = useMemo(() => shown.map((c) => Math.floor(parseCandleTime(c.timestamp) / 1000)), [shown]);
  const candleCloses = useMemo(() => shown.map((c) => c.close), [shown]);
  // N2-4: the expected-move cone (flag decision_tools; the toggle shows only when the organisation has it)
  const decisionTools = !!user && features.on("decision_tools");

  if (loading || v2 || (user && !known)) return <ChartLoading />;
  if (!user) return <div className="p-6 text-sm text-fg">Log in in the main tab first, then open the chart again.</div>;
  if (!symbol) return <div className="p-6 text-sm text-fg">No symbol given.</div>;
  if (firstRead !== "done") return <ChartLoading />;

  return (
    <>
    {workspace && (
      <div data-workspace-in-place>
        <div className="flex items-center bg-surface px-4 pt-2 text-xs">
          <button type="button" onClick={() => setWorkspace(false)} data-close-workspace
            className="rounded border border-border px-1.5 py-0.5 font-semibold text-fg hover:bg-surface-2">Back to the chart</button>
        </div>
        <Suspense fallback={<ChartLoading />}><ChartWorkspace params={workspaceParams} /></Suspense>
      </div>
    )}
    <div className="min-h-screen bg-surface p-4" hidden={workspace}>
      <h1 className="sr-only">{symbol} chart</h1>
      <div className="mb-2 flex items-center gap-3 text-xs text-fg-muted">
        <span className="font-semibold text-fg">{exchange}</span>
        <SymbolTierBadge symbol={symbol} exchange={exchange} live={live.ltp?.data_tier} />
        {broker && <span>candles via {broker}</span>}
        <span>refreshes every minute · live price every 5 s · scroll left for older history</span>
        {workspaceOn && (
          <button type="button" onClick={() => setWorkspace(true)} data-open-workspace
             className="rounded border border-border px-1.5 py-0.5 font-semibold text-fg hover:bg-surface-2">Open in workspace</button>
        )}
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
      {/* N4 CH4a: the user's own PAPER trades (flag chart_trades_layer; nothing shows while it is off) - as of the replay bar */}
      <div className="mt-2"><TradesLayer engine={engine} instrument={`${exchange}:${symbol}`} candles={shown} at={at}
                                          refreshKey={replay.active ? null : chart.candles[chart.candles.length - 1]?.timestamp ?? null} /></div>
      {/* N4 CH6: ScreenQL studies from the server's screener runtime (flag chart_studies; nothing shows while it is off) */}
      <div className="mt-2"><StudiesPanel engine={engine} instrument={`${exchange}:${symbol}`} timeframe={timeframe} at={at} start={windowStart(at, replay.start)}
                                          refreshKey={replay.active ? null : chart.candles[chart.candles.length - 1]?.timestamp ?? null}
                                          candleTimes={candleTimes} closes={candleCloses} /></div>
      {/* Z7a-3: stored market zones (flag market_zones; nothing shows while it is off) - in replay, as of the replay bar */}
      <div className="mt-2"><MarketZonesLayer engine={engine} instrument={`${exchange}:${symbol}`} price={lastClose} at={at} /></div>
      <div className="mt-2"><ExpectedMoveLayer engine={engine} instrument={`${exchange}:${symbol}`} enabled={decisionTools} /></div>
    </div>
    </>
  );
}

/** M63 I-7: the window's first screen - a placeholder where the chart will be, never a bare "Loading…" line. */
function ChartLoading() {
  return (
    <div className="min-h-screen bg-surface p-4">
      <Skeleton className="mb-2 h-4 w-64 max-w-full" />
      <Skeleton className="h-[60vh] w-full" panel label="Loading the chart" />
    </div>
  );
}
