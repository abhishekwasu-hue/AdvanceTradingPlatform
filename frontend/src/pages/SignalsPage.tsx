import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import SignalCard from "../components/SignalCard";
import { Card, DemoDataBanner } from "../components/ui";
import type { EnrichedSignal, StrategyInfo } from "../types";
import { buildTimeframeData, generateSampleCandles } from "../utils/sampleData";

export default function SignalsPage() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [strategyId, setStrategyId] = useState<string>("");
  const [symbol, setSymbol] = useState("NIFTY");
  const [seed, setSeed] = useState(7);
  const [bars, setBars] = useState(356);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EnrichedSignal | null>(null);
  const [executeMsg, setExecuteMsg] = useState<string | null>(null);

  useEffect(() => {
    api.listStrategies().then((list) => {
      setStrategies(list);
      if (list.length) setStrategyId(list[0].id);
    });
  }, []);

  const selected = useMemo(() => strategies.find((s) => s.id === strategyId), [strategies, strategyId]);

  async function handleGenerate() {
    if (!selected) return;
    setLoading(true);
    setError(null);
    setExecuteMsg(null);
    try {
      const base = generateSampleCandles(bars, 100, seed);
      const data = buildTimeframeData(base, selected.timeframes);
      const enriched = await api.enrichSignal(selected.id, symbol, data);
      setResult(enriched);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function handlePaperExecute() {
    if (!selected) return;
    setExecuteMsg(null);
    try {
      const base = generateSampleCandles(bars, 100, seed);
      const data = buildTimeframeData(base, selected.timeframes);
      const res = await api.paperExecute(selected.id, symbol, data);
      setExecuteMsg(res.executed ? `Paper order filled: ${res.reasons.join("; ")}` : `Not executed: ${res.reasons.join("; ")}`);
    } catch (e) {
      setExecuteMsg(String(e));
    }
  }

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
              disabled={!selected}
              className="rounded border border-border hover:bg-panel2 text-slate-200 px-4 py-1.5 text-sm"
            >
              Paper Execute
            </button>
          </div>
        </div>
        {executeMsg && <div className="mt-3 text-xs text-slate-300">{executeMsg}</div>}
      </Card>

      {error && <div className="text-sm text-danger">{error}</div>}

      {result && <SignalCard result={result} />}
    </div>
  );
}
