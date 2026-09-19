import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import CandleChart, { directionMarker, type PriceLineSpec } from "../components/CandleChart";
import SignalCard from "../components/SignalCard";
import { Card, DemoDataBanner } from "../components/ui";
import type { EnrichedSignal, OHLCVBar, SRZone, SignalHistoryEntry, StrategyInfo } from "../types";
import { buildTimeframeData, generateSampleCandles } from "../utils/sampleData";

export default function SignalsPage() {
  const { user } = useAuth();
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [strategyId, setStrategyId] = useState<string>("");
  const [symbol, setSymbol] = useState("NIFTY");
  const [seed, setSeed] = useState(7);
  const [bars, setBars] = useState(356);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EnrichedSignal | null>(null);
  const [chartCandles, setChartCandles] = useState<OHLCVBar[]>([]);
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
      const base = generateSampleCandles(bars, 100, seed);
      const primaryTf = selected.timeframes[0];
      const data = buildTimeframeData(base, selected.timeframes);
      const primaryCandles = data[primaryTf];

      const [enriched, srZones] = await Promise.all([
        api.enrichSignal(selected.id, symbol, data),
        api.supportResistanceZones(symbol, primaryCandles),
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
    if (signal.entry !== null) lines.push({ price: signal.entry, color: "#e2e8f0", title: "Entry" });
    if (signal.stop_loss !== null) lines.push({ price: signal.stop_loss, color: "#ef4444", title: "Stop Loss" });
    if (signal.target1 !== null) lines.push({ price: signal.target1, color: "#22c55e", title: "Target 1" });
    if (signal.target2 !== null) lines.push({ price: signal.target2, color: "#16a34a", title: "Target 2" });
    return lines;
  }, [signal]);

  const markers = useMemo(() => {
    if (!signal || signal.direction === "NO_TRADE") return [];
    return [directionMarker(signal.timestamp, signal.direction, signal.grade)];
  }, [signal]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Signals</h1>
        <p className="text-sm text-muted">Generate a signal from any inbuilt strategy and see the full "why this trade" breakdown.</p>
      </div>

      <DemoDataBanner />

      <Card>
        <div className="grid sm:grid-cols-5 gap-3 items-end">
          <div>
            <label className="block text-xs text-muted mb-1">Strategy</label>
            <select
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
            >
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Symbol</label>
            <input
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Sample bars</label>
            <input
              type="number"
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={bars}
              onChange={(e) => setBars(Number(e.target.value))}
            />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Sample data seed</label>
            <input
              type="number"
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value))}
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleGenerate}
              disabled={!selected || loading}
              className="rounded bg-accent/90 hover:bg-accent text-slate-900 font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
            >
              {loading ? "Generating…" : "Generate Signal"}
            </button>
            <button
              onClick={handlePaperExecute}
              disabled={!lastGenerated}
              title={lastGenerated ? undefined : "Generate a signal first"}
              className="rounded border border-border hover:bg-panel2 text-slate-200 px-4 py-1.5 text-sm disabled:opacity-50"
            >
              Paper Execute
            </button>
          </div>
        </div>
        {executeMsg && <div className="mt-3 text-xs text-slate-300">{executeMsg}</div>}
      </Card>

      {error && <div className="text-sm text-danger">{error}</div>}

      {chartCandles.length > 0 && (
        <Card title="Chart — entry / stop loss / targets / support &amp; resistance">
          <CandleChart candles={chartCandles} priceLines={priceLines} zones={zones} markers={markers} />
        </Card>
      )}

      {result && <SignalCard result={result} />}

      {user && history.length > 0 && (
        <Card title={`Recent signal history (${history.length})`}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted uppercase text-[10px] tracking-wide">
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
                    <td className="py-1 pr-3 text-muted whitespace-nowrap">{new Date(h.created_at).toLocaleString()}</td>
                    <td className="py-1 pr-3">{h.strategy_id}</td>
                    <td className="py-1 pr-3 font-medium text-slate-200">{h.symbol}</td>
                    <td className="py-1 pr-3">{h.direction}</td>
                    <td className="py-1 pr-3">{h.score}</td>
                    <td className="py-1 pr-3 text-muted">{h.grade}</td>
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
