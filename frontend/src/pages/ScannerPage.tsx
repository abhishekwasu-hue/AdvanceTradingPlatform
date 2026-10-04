import { useState } from "react";
import { api } from "../api/client";
import { Card, Disclaimer } from "../components/ui";
import { DataSourceBar, useCandleSource } from "../components/DataSource";
import { ConditionListEditor } from "./StrategyBuilderPage";
import {
  OPTION_FILTER_LABELS,
  STRUCTURE_FILTER_LABELS,
  defaultOptionFilter,
  defaultStructureFilter,
  type Condition,
  type ConditionOperator,
  type OptionFilter,
  type OptionFilterType,
  type ScannerRequest,
  type ScannerResult,
  type ScannerSymbolInput,
  type ScanPlan,
  type ScanRead,
  type StructureFilter,
  type StructureFilterType,
} from "../types";
import { generateSampleOptionChain } from "../utils/sampleData";
import type { OHLCVBar, OptionChain } from "../types";

const STRUCTURE_TYPES = Object.keys(STRUCTURE_FILTER_LABELS) as StructureFilterType[];
const OPTION_TYPES = Object.keys(OPTION_FILTER_LABELS) as OptionFilterType[];
const OPERATORS: { value: ConditionOperator; label: string }[] = [
  { value: "GT", label: ">" },
  { value: "LT", label: "<" },
  { value: "GTE", label: ">=" },
  { value: "LTE", label: "<=" },
];
const OPTION_TILTS: Array<"bullish" | "bearish" | "mixed"> = ["bullish", "bearish", "mixed"];

function hashSeed(symbol: string): number {
  let h = 0;
  for (let i = 0; i < symbol.length; i++) h = (h * 31 + symbol.charCodeAt(i)) | 0;
  return Math.abs(h) || 1;
}

/** Builds one symbol's sample candles + option chain deterministically from its own name, so
 * every watchlist entry gets a different (but reproducible) price path and option-chain tilt.
 */
function sampleChainFor(symbol: string, candles: OHLCVBar[]): OptionChain {
  const seed = hashSeed(symbol);
  const lastClose = candles[candles.length - 1].close;
  return generateSampleOptionChain(symbol, lastClose, OPTION_TILTS[seed % OPTION_TILTS.length]);
}

function buildSymbolInput(symbol: string, timeframe: string, candles: OHLCVBar[], chain: OptionChain | null): ScannerSymbolInput {
  return { symbol, timeframe, candles, option_chain: chain };
}

export default function ScannerPage() {
  const [watchlist, setWatchlist] = useState("NIFTY, BANKNIFTY, RELIANCE, TCS, HDFCBANK, INFY");
  const [timeframe, setTimeframe] = useState("5min");
  const [indicatorConditions, setIndicatorConditions] = useState<Condition[]>([]);
  const [structureFilters, setStructureFilters] = useState<StructureFilter[]>([]);
  const [optionFilters, setOptionFilters] = useState<OptionFilter[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ScannerResult | null>(null);
  // Phase Y: describe the scan in plain language; read the matches with the AI.
  const [aiText, setAiText] = useState("");
  const [aiPlan, setAiPlan] = useState<ScanPlan | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiRead, setAiRead] = useState<ScanRead | null>(null);
  // Phase AA: sample candles or the tenant's broker candles; option chains stay sample either way.
  const source = useCandleSource();
  const [dataWarnings, setDataWarnings] = useState<string[]>([]);
  const [lastRequest, setLastRequest] = useState<ScannerRequest | null>(null);

  async function planWithAi() {
    if (aiText.trim().length < 5) return;
    setAiBusy(true); setError(null);
    try {
      const plan = await api.scannerAiPlan(aiText, navigator.language?.slice(0, 2) || "en");
      setAiPlan(plan);
      setIndicatorConditions(plan.indicator_conditions);
      setStructureFilters(plan.structure_filters);
      setOptionFilters(plan.option_filters);
      if (plan.timeframe) setTimeframe(plan.timeframe);
      if (plan.symbols.length) setWatchlist(plan.symbols.join(", "));
      setResult(null); setAiRead(null);
    } catch (e) { setError(String(e)); } finally { setAiBusy(false); }
  }

  async function readWithAi() {
    if (!lastRequest || !result) return;
    setAiBusy(true); setError(null);
    try { setAiRead(await api.scannerAiRead(lastRequest, result, navigator.language?.slice(0, 2) || "en")); }
    catch (e) { setError(String(e)); } finally { setAiBusy(false); }
  }

  const needsOptionChain = optionFilters.length > 0;

  function updateStructureFilter(i: number, next: StructureFilter) {
    setStructureFilters(structureFilters.map((f, j) => (j === i ? next : f)));
  }

  function updateOptionFilter(i: number, next: OptionFilter) {
    setOptionFilters(optionFilters.map((f, j) => (j === i ? next : f)));
  }

  async function handleRun() {
    const symbols = watchlist
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    if (symbols.length === 0) {
      setError("Enter at least one symbol.");
      return;
    }
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const fetched = await source.fetch(symbols, timeframe, { count: 300, seedFor: hashSeed, startPriceFor: (s) => 100 + (hashSeed(s) % 900) });
      const withCandles = symbols.filter((s) => fetched.candles[s]);
      const chains = needsOptionChain ? await source.fetchChains(withCandles, (s) => sampleChainFor(s, fetched.candles[s])) : { chains: {} as Record<string, OptionChain>, warnings: [] as string[] };
      setDataWarnings([...fetched.warnings, ...chains.warnings]);
      const request = {
        symbols: withCandles.map((s) => buildSymbolInput(s, timeframe, fetched.candles[s], needsOptionChain ? chains.chains[s] ?? null : null)),
        indicator_conditions: indicatorConditions,
        structure_filters: structureFilters,
        option_filters: optionFilters,
        swing_window: 3,
      };
      const res = await api.runScanner(request);
      setLastRequest(request);
      setAiRead(null);
      setResult(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-indigo-400">Market Scanner</h1>
        <p className="text-sm font-semibold text-indigo-200">
          Filter a watchlist by indicator conditions (same building blocks as the Strategy
          Builder), price-action structure, and option-chain signals - only symbols clearing
          every configured filter are returned.
        </p>
      </div>

      <DataSourceBar source={source} note="Option-chain filters read the broker's live chain in Broker mode and a sample chain otherwise." />
      {dataWarnings.map((w, i) => <div key={i} className="text-xs text-amber-300">{w}</div>)}
      <Disclaimer kind="signals" />

      <Card title="Describe the scan (AI)">
        <div className="grid sm:grid-cols-[1fr_160px] gap-3 items-start">
          <textarea rows={2} className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" placeholder="e.g. NIFTY and BANKNIFTY stocks in an uptrend near support, RSI(14) above 55, PCR over 1.2, 15min"
            value={aiText} onChange={(e) => setAiText(e.target.value)} />
          <button onClick={planWithAi} disabled={aiBusy || aiText.trim().length < 5} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1.5 text-sm disabled:opacity-50">
            {aiBusy ? "Planning…" : "Plan filters with AI"}
          </button>
        </div>
        <p className="text-[11px] text-muted mt-2">The AI only translates your words into the filters below (same building blocks as the Strategy Builder); you review them and press Run. Without an AI provider under Settings the deterministic parser is used.</p>
        {aiPlan && (
          <div className="mt-2 text-xs space-y-1">
            <div className="text-slate-200">{aiPlan.explanation} <span className="text-muted">· {aiPlan.provider}{aiPlan.model ? `/${aiPlan.model}` : ""} · {aiPlan.prompt_version}</span></div>
            {aiPlan.warnings.map((w, i) => <div key={i} className="text-amber-300">{w}</div>)}
          </div>
        )}
      </Card>

      <Card title="Watchlist">
        <div className="grid sm:grid-cols-[1fr_140px] gap-3">
          <div>
            <label className="block text-xs text-muted mb-1">Symbols (comma-separated)</label>
            <input
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={watchlist}
              onChange={(e) => setWatchlist(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Timeframe</label>
            <select
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
            >
              {["1min", "5min", "15min", "30min", "60min"].map((tf) => (
                <option key={tf} value={tf}>{tf}</option>
              ))}
            </select>
          </div>
        </div>
      </Card>

      <Card title="Indicator filters">
        <ConditionListEditor
          title="Indicator conditions"
          conditions={indicatorConditions}
          onChange={setIndicatorConditions}
        />
      </Card>

      <Card title="Price-action / structure filters">
        <div className="space-y-1.5">
          {structureFilters.map((f, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2 rounded border border-border bg-panel2/40 px-2 py-2">
              <select
                className="rounded bg-panel2 border border-border px-1.5 py-1 text-xs"
                value={f.filter_type}
                onChange={(e) => updateStructureFilter(i, { ...f, filter_type: e.target.value as StructureFilterType })}
              >
                {STRUCTURE_TYPES.map((t) => (
                  <option key={t} value={t}>{STRUCTURE_FILTER_LABELS[t]}</option>
                ))}
              </select>
              {(f.filter_type === "NEAR_SUPPORT" || f.filter_type === "NEAR_RESISTANCE") && (
                <label className="flex items-center gap-1 text-xs text-muted">
                  Tolerance %
                  <input
                    type="number" step="0.1"
                    className="w-16 rounded bg-panel2 border border-border px-1.5 py-1 text-xs"
                    value={f.tolerance_pct}
                    onChange={(e) => updateStructureFilter(i, { ...f, tolerance_pct: Number(e.target.value) })}
                  />
                </label>
              )}
              <button
                onClick={() => setStructureFilters(structureFilters.filter((_, j) => j !== i))}
                className="ml-auto text-xs text-danger hover:underline"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
        <button
          onClick={() => setStructureFilters([...structureFilters, defaultStructureFilter()])}
          className="mt-1.5 text-xs text-brand hover:underline"
        >
          + Add structure filter
        </button>
      </Card>

      <Card title="Option-chain filters">
        <p className="text-xs text-muted mb-2">
          A sample option chain is generated per symbol only when at least one option filter is
          configured. A symbol never fabricates a pass when it has no chain data.
        </p>
        <div className="space-y-1.5">
          {optionFilters.map((f, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2 rounded border border-border bg-panel2/40 px-2 py-2">
              <select
                className="rounded bg-panel2 border border-border px-1.5 py-1 text-xs"
                value={f.filter_type}
                onChange={(e) => updateOptionFilter(i, { ...f, filter_type: e.target.value as OptionFilterType })}
              >
                {OPTION_TYPES.map((t) => (
                  <option key={t} value={t}>{OPTION_FILTER_LABELS[t]}</option>
                ))}
              </select>
              {f.filter_type === "PCR" && (
                <>
                  <select
                    className="rounded bg-panel2 border border-border px-1.5 py-1 text-xs"
                    value={f.operator ?? "GT"}
                    onChange={(e) => updateOptionFilter(i, { ...f, operator: e.target.value as ConditionOperator })}
                  >
                    {OPERATORS.map((op) => (
                      <option key={op.value} value={op.value}>{op.label}</option>
                    ))}
                  </select>
                  <input
                    type="number" step="0.1"
                    className="w-20 rounded bg-panel2 border border-border px-1.5 py-1 text-xs"
                    value={f.value ?? 0}
                    onChange={(e) => updateOptionFilter(i, { ...f, value: Number(e.target.value) })}
                  />
                </>
              )}
              {f.filter_type === "NEAR_MAX_PAIN" && (
                <label className="flex items-center gap-1 text-xs text-muted">
                  Tolerance %
                  <input
                    type="number" step="0.1"
                    className="w-16 rounded bg-panel2 border border-border px-1.5 py-1 text-xs"
                    value={f.tolerance_pct}
                    onChange={(e) => updateOptionFilter(i, { ...f, tolerance_pct: Number(e.target.value) })}
                  />
                </label>
              )}
              <button
                onClick={() => setOptionFilters(optionFilters.filter((_, j) => j !== i))}
                className="ml-auto text-xs text-danger hover:underline"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
        <button
          onClick={() => setOptionFilters([...optionFilters, defaultOptionFilter()])}
          className="mt-1.5 text-xs text-brand hover:underline"
        >
          + Add option filter
        </button>
      </Card>

      {error && <div className="text-sm text-danger">{error}</div>}

      <button
        onClick={handleRun}
        disabled={running}
        className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
      >
        {running ? "Scanning…" : "Run Scanner"}
      </button>

      {result && (
        <Card title={`Results - ${result.matched_count} of ${result.scanned_count} matched`}>
          {result.matches.length > 0 && (
            <div className="mb-2 flex items-center gap-3">
              <button onClick={readWithAi} disabled={aiBusy || !lastRequest} className="rounded border border-border hover:bg-panel2 px-3 py-1 text-xs text-slate-200 disabled:opacity-50">
                {aiBusy ? "Reading…" : "AI read of these matches"}
              </button>
              <span className="text-[11px] text-muted">Ranks the matches from the labels and the regime the platform reads; analysis, never an order.</span>
            </div>
          )}
          {aiRead && (
            <div className="mb-3 rounded-lg border border-border bg-panel2/40 p-3 text-xs space-y-2">
              <div className="text-slate-200">{aiRead.summary} <span className="text-muted">· {aiRead.provider}{aiRead.model ? `/${aiRead.model}` : ""}</span></div>
              {aiRead.ranked.map((r) => (
                <div key={r.symbol} className="rounded border border-border/60 p-2">
                  <div className="flex items-center justify-between"><span className="font-bold text-slate-200">{r.symbol} <span className="text-muted font-normal">· regime {r.regime ?? "?"}</span></span><span className={`font-bold ${r.score >= 70 ? "text-accent" : r.score >= 50 ? "text-slate-200" : "text-danger"}`}>{r.score}</span></div>
                  <div className="text-slate-300 mt-0.5">{r.thesis}</div>
                  {r.risks && <div className="text-amber-300 mt-0.5">Risks: {r.risks}</div>}
                  <div className="text-muted mt-0.5">Next: {r.next_step}</div>
                </div>
              ))}
              {aiRead.warnings.map((w, i) => <div key={i} className="text-amber-300">{w}</div>)}
              <div className="text-[11px] text-muted">{aiRead.disclaimer}</div>
            </div>
          )}
          {result.matches.length === 0 ? (
            <div className="text-sm text-muted py-2">No symbols cleared every filter.</div>
          ) : (
            <div className="space-y-1.5">
              {result.matches.map((m) => (
                <div key={m.symbol} className="rounded border border-border px-3 py-2 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-slate-200">{m.symbol}</span>
                    <span className="text-muted text-xs">close {m.close}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {[...m.matched_indicator_labels, ...m.matched_structure_labels, ...m.matched_option_labels].map(
                      (label, i) => (
                        <span key={i} className="rounded border border-accent/40 bg-accent/10 px-1.5 py-0.5 text-[11px] text-accent">
                          {label}
                        </span>
                      ),
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
