import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { useBrokerChart } from "../components/chartHistory";
import ProChart, { chartWindowUrl, directionMarker, useLiveLtp, type PriceLineSpec } from "../components/ProChart";
import SignalCard from "../components/SignalCard";
import { Card, Disclaimer } from "../components/ui";
import { DataSourceBar, useCandleSource } from "../components/DataSource";
import type { EnrichedSignal, OHLCVBar, SRZone, SignalHistoryEntry, StrategyInfo } from "../types";
import { buildTimeframeData } from "../utils/sampleData";
import { Button, Input, PageHeader, Select } from "../components/primitives";

export default function SignalsPage() {
  const { user } = useAuth();
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [strategyId, setStrategyId] = useState<string>("");
  const [symbol, setSymbol] = useState("NIFTY");
  const [seed, setSeed] = useState(7);
  const [bars, setBars] = useState(356);
  const source = useCandleSource();            // Phase AA
  const [dataWarnings, setDataWarnings] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EnrichedSignal | null>(null);
  const [chartCandles, setChartCandles] = useState<OHLCVBar[]>([]);
  const [baseCandles, setBaseCandles] = useState<OHLCVBar[]>([]);     // Phase AN: 1-minute base for the chart's timeframe switcher
  const [chartTf, setChartTf] = useState<string>("5min");
  const [zones, setZones] = useState<SRZone[]>([]);
  const [executeMsg, setExecuteMsg] = useState<string | null>(null);
  const [history, setHistory] = useState<SignalHistoryEntry[]>([]);
  const [lastGenerated, setLastGenerated] = useState<{ strategyId: string; symbol: string; data: Record<string, OHLCVBar[]> } | null>(null);

  useEffect(() => {
    api.listStrategies().then((list) => {
      setStrategies(list);
      if (list.length) setStrategyId(list[0].id);
    });
  }, []);

  function refreshHistory() {
    if (user) api.listSignalHistory().then(setHistory).catch(() => {});
  }

  useEffect(refreshHistory, [user]);

  const selected = useMemo(() => strategies.find((s) => s.id === strategyId), [strategies, strategyId]);

  async function handleGenerate() {
    if (!selected) return;
    setLoading(true);
    setError(null);
    setExecuteMsg(null);
    try {
      // Strategies read every timeframe off one-minute bars, so broker mode fetches 1min.
      const fetched = await source.fetch([symbol], "1min", { count: bars, startPriceFor: () => 100, seedFor: () => seed });
      setDataWarnings(fetched.warnings);
      const base = fetched.candles[symbol.trim().toUpperCase()];
      if (!base?.length) throw new Error(`No candles for ${symbol}`);
      const primaryTf = selected.timeframes[0];
      const data = buildTimeframeData(base, selected.timeframes);
      const primaryCandles = data[primaryTf];

      // The S/R overlay is decoration on top of the signal: a refused zones call (anonymous visitor, P0.1)
      // must not take the signal itself down with it.
      const [enriched, srZones] = await Promise.all([
        api.enrichSignal(selected.id, symbol, data),
        api.supportResistanceZones(symbol, primaryCandles).catch(() => [] as SRZone[]),
      ]);

      // The engine can return dozens of small swing clusters; keep only the strongest few
      // near the current price so the chart overlay stays readable rather than a dashed grid.
      const lastClose = primaryCandles[primaryCandles.length - 1].close;
      const relevantZones = srZones
        .filter((z) => Math.abs(z.mid - lastClose) / lastClose <= 0.04)
        .sort((a, b) => b.strength_score - a.strength_score)
        .slice(0, 5);

      setResult(enriched);
      setChartCandles(primaryCandles);
      setBaseCandles(base);
      setChartTf(primaryTf);
      setZones(relevantZones);
      // Pin the exact candles that produced this signal, so Paper Execute always fires the
      // trade the user is actually looking at - even if they nudge the bars/seed inputs
      // afterward without clicking Generate again.
      setLastGenerated({ strategyId: selected.id, symbol, data });
      refreshHistory();
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function handlePaperExecute() {
    if (!lastGenerated) return;
    setExecuteMsg(null);
    try {
      const res = await api.paperExecute(lastGenerated.strategyId, lastGenerated.symbol, lastGenerated.data);
      setExecuteMsg(res.executed ? `Paper order filled: ${res.reasons.join("; ")}` : `Not executed: ${res.reasons.join("; ")}`);
    } catch (e) {
      setExecuteMsg(String(e));
    }
  }

  const signal = result?.signal;
  const priceLines: PriceLineSpec[] = useMemo(() => {
    if (!signal || signal.direction === "NO_TRADE") return [];
    const lines: PriceLineSpec[] = [];
    if (signal.entry !== null) lines.push({ price: signal.entry, color: "fg", title: "Entry" });
    if (signal.stop_loss !== null) lines.push({ price: signal.stop_loss, color: "down", title: "Stop Loss" });
    if (signal.target1 !== null) lines.push({ price: signal.target1, color: "up", title: "Target 1" });
    if (signal.target2 !== null) lines.push({ price: signal.target2, color: "up", title: "Target 2" });
    return lines;
  }, [signal]);

  const markers = useMemo(() => {
    if (!signal || signal.direction === "NO_TRADE") return [];
    return [directionMarker(signal.timestamp, signal.direction, signal.grade)];
  }, [signal]);

  // Phase AN: the chart can show any timeframe resampled from the 1-minute base; the strategy's
  // primary timeframe is the default. Live price in broker mode moves the forming candle.
  // With broker candles each chart timeframe loads its own history (and older pages on scroll-back);
  // sample candles are resampled from the 1-minute base.
  const brokerMode = source.mode === "broker" && !!lastGenerated;
  const brokerChart = useBrokerChart({ enabled: brokerMode, symbol: lastGenerated?.symbol, timeframe: chartTf, exchange: "NSE", broker: source.broker || undefined });
  const displayCandles = useMemo(() => {
    if (brokerMode && brokerChart.candles.length > 0) return brokerChart.candles;
    if (baseCandles.length === 0 || chartTf === "day") return chartCandles;
    if (selected && chartTf === selected.timeframes[0]) return chartCandles;
    return buildTimeframeData(baseCandles, [chartTf])[chartTf] ?? chartCandles;
  }, [brokerMode, brokerChart.candles, baseCandles, chartCandles, chartTf, selected]);
  const live = useLiveLtp(source.mode === "broker" && !!lastGenerated, lastGenerated?.symbol, "NSE", source.broker || undefined);

  return (
    <div className="space-y-4">
      <PageHeader title="Signals" description={<>Generate a signal from any inbuilt strategy and see the full "why this trade" breakdown.</>} />

      <DataSourceBar source={source} note="Bars and seed apply to sample data only." />
      {dataWarnings.map((w, i) => <div key={i} className="text-xs text-warn">{w}</div>)}
      <Disclaimer kind="signals" />

      <Card>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(12rem,1.4fr)_repeat(3,minmax(7rem,1fr))_auto] items-end">
          <Select
            label="Strategy"
            value={strategyId}
            onChange={setStrategyId}
            options={strategies.map((s) => ({ value: s.id, label: s.name }))}
          />
          <Input label="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
          <Input label="Sample bars" type="number" value={bars} onChange={(e) => setBars(Number(e.target.value))} />
          <Input label="Sample data seed" type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
          <div className="flex flex-wrap gap-2 sm:col-span-2 lg:col-span-1">
            <Button variant="primary" onClick={handleGenerate} disabled={!selected || loading} loading={loading}>
              {loading ? "Generating…" : "Generate Signal"}
            </Button>
            <Button onClick={handlePaperExecute} disabled={!lastGenerated} title={lastGenerated ? undefined : "Generate a signal first"}>
              Paper Execute
            </Button>
          </div>
        </div>
        {executeMsg && <div className="mt-3 text-xs text-fg-muted">{executeMsg}</div>}
      </Card>

      {error && <div className="text-sm text-down">{error}</div>}

      {chartCandles.length > 0 && (
        <Card title="Chart — strategy indicators, entry / stop / targets, support &amp; resistance">
          <ProChart candles={displayCandles} symbol={lastGenerated?.symbol} timeframe={chartTf}
                    timeframes={brokerMode ? ["1min", "5min", "15min", "30min", "60min", "day"] : ["1min", "5min", "15min", "30min", "60min"]} onTimeframeChange={setChartTf}
                    priceLines={priceLines} zones={zones} markers={markers} strategyParams={selected?.default_params} live={chartTf === "day" ? null : live.ltp} liveError={live.error}
                    openUrl={source.mode === "broker" && lastGenerated ? chartWindowUrl(lastGenerated.symbol, chartTf, "NSE", source.broker || undefined) : undefined}
                    deployable={source.mode === "broker" && !!lastGenerated}
                    onLoadOlder={brokerMode ? brokerChart.loadOlder : undefined} loadingOlder={brokerChart.loadingOlder} olderExhausted={brokerChart.exhausted} />
        </Card>
      )}

      {result && <SignalCard result={result} />}

      {user && history.length > 0 && (
        <Card title={`Recent signal history (${history.length})`}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-fg-muted uppercase text-[10px] tracking-wide">
                <tr className="text-left">
                  <th className="py-1 pr-3">Time</th>
                  <th className="py-1 pr-3">Strategy</th>
                  <th className="py-1 pr-3">Symbol</th>
                  <th className="py-1 pr-3">Direction</th>
                  <th className="py-1 pr-3">Score</th>
                  <th className="py-1 pr-3">Grade</th>
                </tr>
              </thead>
              <tbody>
                {history.slice(0, 20).map((h) => (
                  <tr key={h.id} className="border-t border-border">
                    <td className="py-1 pr-3 text-fg-muted whitespace-nowrap">{new Date(h.created_at).toLocaleString()}</td>
                    <td className="py-1 pr-3">{h.strategy_id}</td>
                    <td className="py-1 pr-3 font-medium text-fg">{h.symbol}</td>
                    <td className="py-1 pr-3">{h.direction}</td>
                    <td className="py-1 pr-3">{h.score}</td>
                    <td className="py-1 pr-3 text-fg-muted">{h.grade}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
