import { useEffect, useState } from "react";
import { Database, FlaskConical } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { CandleSource, OHLCVBar, OptionChain } from "../types";
import { generateSampleCandles } from "../utils/sampleData";

/** Phase AA: one switch for every research page - deterministic sample candles, or the tenant's
 * own broker candles through the backend (`POST /api/market-data/candles`). The hook owns the
 * choice and does the fetching; the bar renders it. Sample mode never touches the network. */

export type CandleMode = "sample" | "broker";

export interface FetchedCandles {
  candles: Record<string, OHLCVBar[]>;
  warnings: string[];
  /** "sample" or "broker:<name>" - the label recorded on backtest runs. */
  label: string;
}

export interface CandleSourceState {
  mode: CandleMode;
  setMode: (m: CandleMode) => void;
  broker: string;
  setBroker: (b: string) => void;
  lookbackDays: number;
  setLookbackDays: (d: number) => void;
  sources: CandleSource[];
  usable: boolean;
  loading: boolean;
  label: string;
  fetch: (symbols: string[], timeframe: string, sample?: { count?: number; startPriceFor?: (symbol: string) => number; seedFor?: (symbol: string) => number; daily?: boolean }) => Promise<FetchedCandles>;
  /** Phase AD: the broker's live option chains in broker mode; `sampleFor` builds the sample chain otherwise. */
  fetchChains: (underlyings: string[], sampleFor: (symbol: string) => OptionChain, expiry?: string) => Promise<{ chains: Record<string, OptionChain>; warnings: string[] }>;
}

export function hashSymbol(symbol: string): number {
  let h = 0;
  for (let i = 0; i < symbol.length; i++) h = (h * 31 + symbol.charCodeAt(i)) | 0;
  return Math.abs(h) || 1;
}

export function useCandleSource(defaultLookbackDays = 5): CandleSourceState {
  const { user } = useAuth();
  const [mode, setMode] = useState<CandleMode>("sample");
  const [broker, setBroker] = useState("");
  const [lookbackDays, setLookbackDays] = useState(defaultLookbackDays);
  const [sources, setSources] = useState<CandleSource[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!user) { setSources([]); setMode("sample"); return; }
    let cancelled = false;
    api.marketDataSources().then((r) => {
      if (cancelled) return;
      setSources(r.sources);
      const first = r.sources.find((s) => s.usable);
      if (first && !broker) setBroker(first.broker);
    }).catch(() => { if (!cancelled) setSources([]); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  const usable = sources.some((s) => s.usable);
  const label = mode === "broker" ? `broker:${broker || "auto"}` : "sample";

  async function fetch(symbols: string[], timeframe: string, sample?: { count?: number; startPriceFor?: (s: string) => number; seedFor?: (s: string) => number; daily?: boolean }): Promise<FetchedCandles> {
    const clean = Array.from(new Set(symbols.map((s) => s.trim().toUpperCase()).filter(Boolean)));
    if (mode === "sample") {
      const candles: Record<string, OHLCVBar[]> = {};
      for (const s of clean) {
        const seed = sample?.seedFor ? sample.seedFor(s) : hashSymbol(s);
        const start = sample?.startPriceFor ? sample.startPriceFor(s) : 100 + (hashSymbol(s) % 900);
        candles[s] = generateSampleCandles(sample?.count ?? 300, start, seed, { daily: sample?.daily });
      }
      return { candles, warnings: [], label: "sample" };
    }
    setLoading(true);
    try {
      const res = await api.marketDataCandles(clean, timeframe, lookbackDays, "NSE", broker || undefined);
      const candles: Record<string, OHLCVBar[]> = {};
      const warnings = [...res.warnings];
      for (const [symbol, entry] of Object.entries(res.symbols)) {
        if (entry.error) warnings.push(`${symbol}: ${entry.error}`);
        else if (entry.bars.length) candles[symbol] = entry.bars;
      }
      if (Object.keys(candles).length === 0) throw new Error(`No candles from ${res.source.broker}: ${warnings.join("; ") || "empty response"}`);
      return { candles, warnings, label: `broker:${res.source.broker}` };
    } finally {
      setLoading(false);
    }
  }

  async function fetchChains(underlyings: string[], sampleFor: (symbol: string) => OptionChain, expiry?: string) {
    const clean = Array.from(new Set(underlyings.map((s) => s.trim().toUpperCase()).filter(Boolean)));
    if (mode === "sample") {
      return { chains: Object.fromEntries(clean.map((s) => [s, sampleFor(s)])), warnings: [] as string[] };
    }
    setLoading(true);
    try {
      const res = await api.marketDataOptionChains(clean, expiry, broker || undefined);
      const chains: Record<string, OptionChain> = {};
      const warnings = [...res.warnings];
      for (const [symbol, entry] of Object.entries(res.symbols)) {
        if (entry.chain && entry.rows > 0) chains[symbol] = entry.chain;
        else if (entry.error) warnings.push(`${symbol} chain: ${entry.error}`);
      }
      return { chains, warnings };
    } finally {
      setLoading(false);
    }
  }

  return { mode, setMode, broker, setBroker, lookbackDays, setLookbackDays, sources, usable, loading, label, fetch, fetchChains };
}

export function DataSourceBar({ source, note }: { source: CandleSourceState; note?: string }) {
  const { user } = useAuth();
  const brokerMode = source.mode === "broker";
  return (
    <div className={`mb-4 flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2 text-xs ${brokerMode ? "border-up/30 bg-up/[0.06] text-up" : "border-warn/30 bg-warn/[0.07] text-warn"}`}>
      {brokerMode ? <Database size={14} className="shrink-0" /> : <FlaskConical size={14} className="shrink-0" />}
      <span className="font-semibold">Data</span>
      <div className="flex rounded border border-border overflow-hidden">
        <button onClick={() => source.setMode("sample")} className={`px-2 py-0.5 ${!brokerMode ? "bg-surface-2 text-fg" : "text-fg-muted"}`}>Sample</button>
        <button onClick={() => source.setMode("broker")} disabled={!source.usable} title={source.usable ? "" : user ? "No broker with a valid session today - add the API key under Settings > Brokers and log in" : "Log in first"}
          className={`px-2 py-0.5 border-l border-border ${brokerMode ? "bg-surface-2 text-fg" : "text-fg-muted"} disabled:opacity-40`}>Broker candles</button>
      </div>
      {brokerMode && (
        <>
          <select value={source.broker} onChange={(e) => source.setBroker(e.target.value)} className="rounded bg-surface-2 border border-border px-2 py-0.5 text-xs text-fg">
            {source.sources.filter((s) => s.usable).map((s) => <option key={`${s.broker}:${s.account_label}`} value={s.broker}>{s.broker} ({s.account_label})</option>)}
          </select>
          <label className="flex items-center gap-1 text-fg-muted">lookback
            <input type="number" min={1} max={730} value={source.lookbackDays} onChange={(e) => source.setLookbackDays(Math.max(1, Number(e.target.value) || 1))} className="w-16 rounded bg-surface-2 border border-border px-1 py-0.5 text-xs text-fg" /> days
          </label>
          {source.loading && <span className="text-fg-muted">fetching…</span>}
        </>
      )}
      <span className="text-fg-muted">
        {brokerMode
          ? "Real candles through your own broker session (history + today, cached 60 s). The backend computes on them exactly as on sample data."
          : `Deterministic sample OHLCV; the backend computes everything for real on it. Switch to broker candles once a broker is logged in under Settings.${note ? " " + note : ""}`}
      </span>
    </div>
  );
}
