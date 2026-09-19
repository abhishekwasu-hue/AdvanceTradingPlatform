import { useState } from "react";
import { api } from "../api/client";
import { Card, DemoDataBanner } from "../components/ui";
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
  type ScannerResult,
  type ScannerSymbolInput,
  type StructureFilter,
  type StructureFilterType,
} from "../types";
import { generateSampleCandles, generateSampleOptionChain } from "../utils/sampleData";

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
function buildSymbolInput(symbol: string, timeframe: string, includeOptionChain: boolean): ScannerSymbolInput {
  const seed = hashSeed(symbol);
  const startPrice = 100 + (seed % 900);
  const candles = generateSampleCandles(300, startPrice, seed);
  const lastClose = candles[candles.length - 1].close;
  const tilt = OPTION_TILTS[seed % OPTION_TILTS.length];
  return {
    symbol,
    timeframe,
    candles,
    option_chain: includeOptionChain ? generateSampleOptionChain(symbol, lastClose, tilt) : null,
  };
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
      const request = {
        symbols: symbols.map((s) => buildSymbolInput(s, timeframe, needsOptionChain)),
        indicator_conditions: indicatorConditions,
        structure_filters: structureFilters,
        option_filters: optionFilters,
        swing_window: 3,
      };
      const res = await api.runScanner(request);
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
        <h1 className="text-xl font-semibold text-slate-100">Market Scanner</h1>
        <p className="text-sm text-muted">
          Filter a watchlist by indicator conditions (same building blocks as the Strategy
          Builder), price-action structure, and option-chain signals - only symbols clearing
          every configured filter are returned.
        </p>
      </div>

      <DemoDataBanner />

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
