import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Database, FlaskConical, Plug } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { CandleSource, DataTierBadgeInfo, OHLCVBar, OptionChain } from "../types";
import { DataTierBadge } from "./DataTierBadge";
import { useFeatures } from "../ui/useFeatures";
import { generateSampleCandles } from "../utils/sampleData";
import { pricesLine, STORED_HISTORY_NOTE } from "../backtest/pricesLine";

/** Phase AA: one switch for every research page - deterministic sample candles, or the tenant's
 * own broker candles through the backend (`POST /api/market-data/candles`). The hook owns the
 * choice and does the fetching; the bar renders it. Sample mode never touches the network. */

export type CandleMode = "sample" | "broker";

/**
 * UX3 fix 7 (UX audit §1 problem 6, U4 "no sample data"): with `ui_v2` on, sample candles are a testing tool for the
 * platform admin only. Everyone else runs on their broker's prices: the bar shows no Sample switch and no developer
 * text, the broker is picked by itself, and without a usable broker a run stops with this one sentence (the bar says
 * the same, with a link to the Broker page). With the flag off nothing changes.
 */
export const NEEDS_BROKER_FOR_DATA = "Connect your broker on the Broker page to run this on real prices.";

/** True when this user must not get sample data: `ui_v2` on and not the platform admin. */
export function sampleHiddenFor(uiV2: boolean, role: string | null | undefined): boolean {
  return uiV2 && role !== "SUPER_ADMIN";
}

export interface FetchedCandles {
  candles: Record<string, OHLCVBar[]>;
  warnings: string[];
  /** "sample", "broker:<name>" or "lake:daily" - the label recorded on backtest runs. */
  label: string;
  /** Owner order 23 Oct: where the prices came from and the range, in plain words (null for sample data). */
  line: string | null;
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
  /** BT1 (flag byob_data_tier): the data tier of the last broker fetch; undefined with the flag off or in sample mode. */
  dataTier?: DataTierBadgeInfo;
  /** UX3 fix 7: no sample data for this user (sampleHiddenFor). */
  sampleHidden?: boolean;
  /** Owner order 23 Oct (flag backtest_stored_history, ui_v2): no usable broker, so a backtest page runs on ATP's stored
   * Daily history (the server picks the bars; intraday is refused with a plain sentence). */
  stored?: boolean;
  /** Phase AD: the broker's live option chains in broker mode; `sampleFor` builds the sample chain otherwise. */
  fetchChains: (underlyings: string[], sampleFor: (symbol: string) => OptionChain, expiry?: string) => Promise<{ chains: Record<string, OptionChain>; warnings: string[] }>;
}

export function hashSymbol(symbol: string): number {
  let h = 0;
  for (let i = 0; i < symbol.length; i++) h = (h * 31 + symbol.charCodeAt(i)) | 0;
  return Math.abs(h) || 1;
}

export function useCandleSource(defaultLookbackDays = 5, opts?: { storedHistory?: boolean }): CandleSourceState {
  const { user } = useAuth();
  const features = useFeatures();
  const sampleHidden = sampleHiddenFor(features.on("ui_v2"), user?.role);
  const storedOn = !!opts?.storedHistory && sampleHidden && features.on("backtest_stored_history");
  const [mode, setMode] = useState<CandleMode>("sample");
  const [broker, setBroker] = useState("");
  const [lookbackDays, setLookbackDays] = useState(defaultLookbackDays);
  const [sources, setSources] = useState<CandleSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [dataTier, setDataTier] = useState<DataTierBadgeInfo | undefined>(undefined);

  useEffect(() => {
    if (!user) { setSources([]); setMode("sample"); return; }
    let cancelled = false;
    api.marketDataSources().then((r) => {
      if (cancelled) return;
      // BT1: with byob_data_tier on, a broker whose data may not be shown live (execution-only / pending) is not a candle
      // source for this user even with a valid token; with the flag off `tier_a` is absent and nothing changes.
      const list = r.sources.map((s) => (s.tier_a === false ? { ...s, usable: false } : s));
      setSources(list);
      const first = list.find((s) => s.usable);
      if (first && !broker) setBroker(first.broker);
    }).catch(() => { if (!cancelled) setSources([]); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  const usable = sources.some((s) => s.usable);
  // UX3 fix 7: a user without sample data runs on the broker as soon as one is usable.
  useEffect(() => { if (sampleHidden && usable && mode === "sample") setMode("broker"); }, [sampleHidden, usable, mode]);
  const stored = storedOn && !usable;
  const label = mode === "broker" ? `broker:${broker || "auto"}` : stored ? "lake:daily" : "sample";

  async function fetch(symbols: string[], timeframe: string, sample?: { count?: number; startPriceFor?: (s: string) => number; seedFor?: (s: string) => number; daily?: boolean }): Promise<FetchedCandles> {
    const clean = Array.from(new Set(symbols.map((s) => s.trim().toUpperCase()).filter(Boolean)));
    if (mode === "sample" && stored) {
      // Owner order 23 Oct: the server chooses the bars (stored Daily history here) and says where they came from.
      setLoading(true);
      try {
        const candles: Record<string, OHLCVBar[]> = {};
        let line: string | null = null;
        let label = "lake:daily";
        for (const s of clean) {
          const res = await api.backtestBars(s, timeframe, lookbackDays);
          candles[s] = res.candles;
          line = line ?? res.line;
          label = res.source;
        }
        return { candles, warnings: [], label, line };
      } finally {
        setLoading(false);
      }
    }
    if (mode === "sample" && sampleHidden) throw new Error(NEEDS_BROKER_FOR_DATA);
    if (mode === "sample") {
      const candles: Record<string, OHLCVBar[]> = {};
      for (const s of clean) {
        const seed = sample?.seedFor ? sample.seedFor(s) : hashSymbol(s);
        const start = sample?.startPriceFor ? sample.startPriceFor(s) : 100 + (hashSymbol(s) % 900);
        candles[s] = generateSampleCandles(sample?.count ?? 300, start, seed, { daily: sample?.daily });
      }
      return { candles, warnings: [], label: "sample", line: null };
    }
    setLoading(true);
    try {
      const res = await api.marketDataCandles(clean, timeframe, lookbackDays, "NSE", broker || undefined);
      setDataTier(res.data_tier);
      const candles: Record<string, OHLCVBar[]> = {};
      const warnings = [...res.warnings];
      for (const [symbol, entry] of Object.entries(res.symbols)) {
        if (entry.error) warnings.push(`${symbol}: ${entry.error}`);
        else if (entry.bars.length) candles[symbol] = entry.bars;
      }
      if (Object.keys(candles).length === 0) throw new Error(`No candles from ${res.source.broker}: ${warnings.join("; ") || "empty response"}`);
      const label = `broker:${res.source.broker}`;
      return { candles, warnings, label, line: pricesLine(label, candles[clean[0]] ?? Object.values(candles)[0] ?? []) };
    } finally {
      setLoading(false);
    }
  }

  async function fetchChains(underlyings: string[], sampleFor: (symbol: string) => OptionChain, expiry?: string) {
    const clean = Array.from(new Set(underlyings.map((s) => s.trim().toUpperCase()).filter(Boolean)));
    if (mode === "sample" && sampleHidden) throw new Error(NEEDS_BROKER_FOR_DATA);
    if (mode === "sample") {
      return { chains: Object.fromEntries(clean.map((s) => [s, sampleFor(s)])), warnings: [] as string[] };
    }
    setLoading(true);
    try {
      const res = await api.marketDataOptionChains(clean, expiry, broker || undefined);
      setDataTier(res.data_tier);
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

  return { mode, setMode, broker, setBroker, lookbackDays, setLookbackDays, sources, usable, loading, label, fetch, fetchChains, sampleHidden, stored,
           dataTier: mode === "broker" ? dataTier : undefined };
}

/** UX3 fix 7: the bar a user without sample data sees - where the prices come from, in plain words. */
export function PlainDataSourceBar({ source }: { source: CandleSourceState }) {
  const usable = source.sources.filter((s) => s.usable);
  if (!source.usable && source.stored) {
    return (
      <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm text-fg" data-plain-data-source="stored">
        <Database size={16} aria-hidden className="shrink-0 text-fg-muted" />
        <span className="min-w-0 flex-1">{STORED_HISTORY_NOTE}</span>
        <label className="flex items-center gap-1 text-fg-muted">History
          <input type="number" min={1} max={3650} value={source.lookbackDays} onChange={(e) => source.setLookbackDays(Math.max(1, Number(e.target.value) || 1))}
            className="w-16 rounded border border-border bg-surface-2 px-1 py-0.5 text-fg" /> days
        </label>
        {source.loading && <span className="text-fg-muted">Loading prices…</span>}
        <Link to="/broker" className="inline-flex min-h-11 items-center font-semibold text-brand hover:underline md:min-h-0">Open Broker</Link>
      </div>
    );
  }
  if (!source.usable) {
    return (
      <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm text-fg" data-plain-data-source="no-broker">
        <Plug size={16} aria-hidden className="shrink-0 text-fg-muted" />
        <span className="min-w-0 flex-1">{NEEDS_BROKER_FOR_DATA}</span>
        <Link to="/broker" className="inline-flex min-h-11 items-center font-semibold text-brand hover:underline md:min-h-0">Open Broker</Link>
      </div>
    );
  }
  return (
    <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm text-fg" data-plain-data-source="broker">
      <Database size={16} aria-hidden className="shrink-0 text-fg-muted" />
      <span>Prices from your broker</span>
      {usable.length > 1 && (
        <label className="flex items-center gap-1 text-fg-muted">
          <span className="sr-only">Broker</span>
          <select value={source.broker} onChange={(e) => source.setBroker(e.target.value)} className="rounded border border-border bg-surface-2 px-2 py-0.5 text-fg">
            {usable.map((s) => <option key={`${s.broker}:${s.account_label}`} value={s.broker}>{s.broker} ({s.account_label})</option>)}
          </select>
        </label>
      )}
      <label className="flex items-center gap-1 text-fg-muted">History
        <input type="number" min={1} max={730} value={source.lookbackDays} onChange={(e) => source.setLookbackDays(Math.max(1, Number(e.target.value) || 1))}
          className="w-16 rounded border border-border bg-surface-2 px-1 py-0.5 text-fg" /> days
      </label>
      {source.loading && <span className="text-fg-muted">Loading prices…</span>}
    </div>
  );
}

export function DataSourceBar({ source, note }: { source: CandleSourceState; note?: string }) {
  const { user } = useAuth();
  if (source.sampleHidden) return <PlainDataSourceBar source={source} />;
  const brokerMode = source.mode === "broker";
  return (
    <div className={`mb-4 flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2 text-xs ${brokerMode ? "border-info/30 bg-info/[0.06] text-info" : "border-warn/30 bg-warn/[0.07] text-warn"}`}>
      {brokerMode ? <Database size={14} className="shrink-0" /> : <FlaskConical size={14} className="shrink-0" />}
      <span className="font-semibold">Data</span>
      <div className="flex rounded border border-border overflow-hidden">
        <button onClick={() => source.setMode("sample")} className={`px-2 py-0.5 ${!brokerMode ? "bg-surface-2 text-fg" : "text-fg-muted"}`}>Sample</button>
        <button onClick={() => source.setMode("broker")} disabled={!source.usable} title={source.usable ? "" : user ? "No broker with a valid session today - connect it on the Broker page" : "Log in first"}
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
          <DataTierBadge badge={source.dataTier} />
        </>
      )}
      <span className="text-fg-muted">
        {brokerMode
          ? "Real candles through your own broker session (history + today, cached 60 s). The backend computes on them exactly as on sample data."
          : `Deterministic sample OHLCV; the backend computes everything for real on it. Switch to broker candles once a broker is logged in on the Broker page.${note ? " " + note : ""}`}
      </span>
    </div>
  );
}
