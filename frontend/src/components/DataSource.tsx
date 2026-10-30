import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Database, FlaskConical, Plug } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { CandleSource, DataRequest, DataTierBadgeInfo, OHLCVBar, OptionChain } from "../types";
import { DataTierBadge } from "./DataTierBadge";
import { useFeatures } from "../ui/useFeatures";
import { generateSampleCandles } from "../utils/sampleData";
import { pricesLine, STORED_HISTORY_CHOICES, STORED_HISTORY_DEFAULT_DAYS, STORED_HISTORY_NOTE, storedHistoryLoginNote } from "../backtest/pricesLine";
import { brokerDisplayName } from "../ui/brokerSession";
import { StoredLoginAction } from "../ui/BrokerGate";

/** Phase AA: one switch for every research page - deterministic sample candles, or the tenant's
 * own broker candles through the backend (`POST /api/market-data/candles`). The hook owns the
 * choice and does the fetching; the bar renders it. Sample mode never touches the network. */

export type CandleMode = "sample" | "broker";

/**
 * M55 T3 (data honesty): sample data is never the default. Mode "broker" - the default for everyone - is the server's
 * own candles for the chosen symbol / timeframe: the broker session when one is usable, else ATP's stored Daily history
 * where the page allows it (flag backtest_stored_history), else one plain sentence. Sample candles only behind the
 * labelled switch SAMPLE_TOGGLE (never for a user under ui_v2, see sampleHiddenFor), and everything built on them shows
 * the SAMPLE chip. A backtest / scan / S&R request names symbol + timeframe and the server loads the bars itself
 * (`dataRequestFor`); posted candles are "paste your own data", which the server records as `uploaded`.
 */
export const SAMPLE_TOGGLE = "Use sample data (practice only)";
export const SAMPLE_CHIP = "SAMPLE";
/** The run history's words for a recorded data source. */
export function sourceWords(source: string | null | undefined): string {
  const s = source ?? "";
  if (s === "uploaded") return "Your own data";
  if (s === "sample") return "Sample";
  if (s.startsWith("broker")) return "Your broker";
  if (s.startsWith("lake")) return "Stored history";
  return s || "Unknown";
}

export type { DataRequest };

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
  /** M55 T3: what a backtest / scan request sends instead of trusting a label (server mode posts no candles). */
  request?: DataRequest;
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
  /** Day-one fix 3: the stored Daily history a run reads, in days (its own choice: the broker's lookback is not touched). */
  storedDays?: number;
  setStoredDays?: (d: number) => void;
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
  // M55 T3: stored Daily history is the server's own data for everyone on a backtest page, not only without sample data.
  const storedOn = !!opts?.storedHistory && features.on("backtest_stored_history");
  const [mode, setMode] = useState<CandleMode>("broker");
  const [broker, setBroker] = useState("");
  const [lookbackDays, setLookbackDays] = useState(defaultLookbackDays);
  const [storedDays, setStoredDays] = useState(STORED_HISTORY_DEFAULT_DAYS);
  const [sources, setSources] = useState<CandleSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [dataTier, setDataTier] = useState<DataTierBadgeInfo | undefined>(undefined);

  useEffect(() => {
    if (!user) { setSources([]); return; }
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
  // UX3 fix 7: a user without sample data never runs on it.
  useEffect(() => { if (sampleHidden && mode === "sample") setMode("broker"); }, [sampleHidden, mode]);
  const stored = storedOn && !usable;
  const label = mode === "sample" ? "sample" : usable ? `broker:${broker || "auto"}` : stored ? "lake:daily" : "none";

  async function fetch(symbols: string[], timeframe: string, sample?: { count?: number; startPriceFor?: (s: string) => number; seedFor?: (s: string) => number; daily?: boolean }): Promise<FetchedCandles> {
    const clean = Array.from(new Set(symbols.map((s) => s.trim().toUpperCase()).filter(Boolean)));
    if (mode !== "sample" && !usable && stored) {
      // Owner order 23 Oct: the server chooses the bars (stored Daily history here) and says where they came from.
      setLoading(true);
      try {
        const candles: Record<string, OHLCVBar[]> = {};
        let line: string | null = null;
        let label = "lake:daily";
        for (const s of clean) {
          const res = await api.backtestBars(s, timeframe, storedDays);
          candles[s] = res.candles;
          line = line ?? res.line;
          label = res.source;
        }
        return { candles, warnings: [], label, line, request: { data_mode: "server", lookback_days: storedDays } };
      } finally {
        setLoading(false);
      }
    }
    if (mode === "sample" && sampleHidden) throw new Error(NEEDS_BROKER_FOR_DATA);
    if (mode !== "sample" && !usable) throw new Error(NEEDS_BROKER_FOR_DATA);
    if (mode === "sample") {
      const candles: Record<string, OHLCVBar[]> = {};
      for (const s of clean) {
        const seed = sample?.seedFor ? sample.seedFor(s) : hashSymbol(s);
        const start = sample?.startPriceFor ? sample.startPriceFor(s) : 100 + (hashSymbol(s) % 900);
        candles[s] = generateSampleCandles(sample?.count ?? 300, start, seed, { daily: sample?.daily });
      }
      return { candles, warnings: [], label: "sample", line: null, request: { data_mode: "paste" } };
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
      return { candles, warnings, label, line: pricesLine(label, candles[clean[0]] ?? Object.values(candles)[0] ?? []),
               request: { data_mode: "server", lookback_days: lookbackDays, broker: res.source.broker || broker || null } };
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
           storedDays, setStoredDays,
           dataTier: mode === "broker" ? dataTier : undefined };
}

/** M55 T3: what a scan / S&R request sends for the current choice, without fetching candles in the browser. Server mode
 * needs a usable broker or stored history (else NEEDS_BROKER_FOR_DATA, the sentence the bar shows); sample = paste. */
export function dataRequestFor(source: Pick<CandleSourceState, "mode" | "usable" | "stored" | "lookbackDays" | "broker" | "storedDays">): DataRequest {
  if (source.mode === "sample") return { data_mode: "paste" };
  if (source.usable) return { data_mode: "server", lookback_days: source.lookbackDays, broker: source.broker || null };
  if (source.stored) return { data_mode: "server", lookback_days: source.storedDays ?? STORED_HISTORY_DEFAULT_DAYS };
  throw new Error(NEEDS_BROKER_FOR_DATA);
}

/** M55 T3: the chip on every chart or table built on sample data. */
export function SampleChip({ className = "" }: { className?: string }) {
  return (
    <span data-sample-chip title="Practice prices made up by ATP - not real market prices"
      className={`inline-flex items-center rounded border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-t12 font-semibold tracking-wide text-warn ${className}`}>
      {SAMPLE_CHIP}
    </span>
  );
}

/** UX3 fix 7: the bar a user without sample data sees - where the prices come from, in plain words. */
export function PlainDataSourceBar({ source, bare }: { source: CandleSourceState; bare?: boolean }) {
  const mb = bare ? "" : "mb-4 ";
  const usable = source.sources.filter((s) => s.usable);
  if (!source.usable && source.stored) {
    // Day-one fix 3: a stored broker that is only not logged in today is named as such; the sentence has its own line
    // on the phone (it wrapped one word per line beside the controls); History is a plain choice of how far back.
    const stored = source.sources[0];
    const note = stored ? storedHistoryLoginNote(brokerDisplayName(stored.broker)) : STORED_HISTORY_NOTE;
    return (
      <div className={`${mb}flex flex-col gap-2 rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm text-fg md:flex-row md:items-center md:gap-3`} data-plain-data-source="stored">
        <p className="flex min-w-0 items-start gap-2 md:flex-1">
          <Database size={16} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
          <span>{note}</span>
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <div role="group" aria-label="History" className="flex items-center gap-2 text-fg-muted" data-stored-history>
            <span aria-hidden>History</span>
            <span className="inline-flex overflow-hidden rounded border border-border">
              {STORED_HISTORY_CHOICES.map((c, i) => {
                const on = (source.storedDays ?? STORED_HISTORY_DEFAULT_DAYS) === c.days;
                return (
                  <button key={c.days} type="button" aria-pressed={on} onClick={() => source.setStoredDays?.(c.days)}
                    className={`min-h-11 px-3 md:min-h-0 md:py-0.5 ${i > 0 ? "border-l border-border" : ""} ${on ? "bg-surface-2 font-semibold text-fg" : "text-fg-muted"}`}>
                    {c.label}
                  </button>
                );
              })}
            </span>
          </div>
          {source.loading && <span className="text-fg-muted">Loading prices…</span>}
          <StoredLoginAction />
        </div>
      </div>
    );
  }
  if (!source.usable) {
    return (
      <div className={`${mb}flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm text-fg`} data-plain-data-source="no-broker">
        <Plug size={16} aria-hidden className="shrink-0 text-fg-muted" />
        <span className="min-w-0 flex-1">{NEEDS_BROKER_FOR_DATA}</span>
        <Link to="/broker" className="inline-flex min-h-11 items-center font-semibold text-brand hover:underline md:min-h-0">Open Broker</Link>
      </div>
    );
  }
  return (
    <div className={`${mb}flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm text-fg`} data-plain-data-source="broker">
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

/**
 * The data bar of a research page. A user without sample data (ui_v2, not the platform admin) sees only the plain bar.
 * Everyone else (ui_v2 off, or the platform admin) gets the same server prices by default, plus the labelled switch
 * SAMPLE_TOGGLE; with it on, the bar shows the SAMPLE chip and says the prices are made up (M55 T3).
 */
export function DataSourceBar({ source, note }: { source: CandleSourceState; note?: string }) {
  if (source.sampleHidden) return <PlainDataSourceBar source={source} />;
  const sample = source.mode === "sample";
  return (
    <div className="mb-4 space-y-2" data-data-source-bar={sample ? "sample" : "server"}>
      {sample ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-warn/30 bg-warn/[0.07] px-3 py-2 text-sm text-fg" data-sample-bar>
          <FlaskConical size={16} aria-hidden className="shrink-0 text-warn" />
          <SampleChip />
          <span className="min-w-0 flex-1 text-fg-muted">Practice prices made up by ATP, not real market prices. Results on them show the SAMPLE chip.{note ? " " + note : ""}</span>
        </div>
      ) : (
        <>
          <PlainDataSourceBar source={source} bare />
          {source.mode === "broker" && source.usable && <DataTierBadge badge={source.dataTier} />}
        </>
      )}
      <label className="inline-flex min-h-11 cursor-pointer items-center gap-2 text-sm text-fg-muted md:min-h-0">
        <input type="checkbox" role="switch" aria-checked={sample} checked={sample} data-sample-toggle
          onChange={(e) => source.setMode(e.target.checked ? "sample" : "broker")} className="h-4 w-4 accent-warn" />
        <span>{SAMPLE_TOGGLE}</span>
      </label>
    </div>
  );
}
