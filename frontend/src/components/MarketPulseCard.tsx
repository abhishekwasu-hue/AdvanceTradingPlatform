import { Plus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import ProChart, { chartWindowUrl, useLiveLtp } from "./ProChart";
import { useCandleSource } from "./DataSource";
import type { Page } from "./Sidebar";
import { Card } from "./ui";
import type { OHLCVBar } from "../types";
import { FNO_INDICES, FNO_STOCKS } from "../utils/fnoSymbols";

const KEY = "atp_pulse_symbols";
const DEFAULT = ["NIFTY 50", "NIFTY BANK"];

function readSymbols(): string[] {
  try { const raw = localStorage.getItem(KEY); const v = raw ? JSON.parse(raw) : null; return Array.isArray(v) && v.length ? v.slice(0, 4) : DEFAULT; } catch { return DEFAULT; }
}

function PulseChart({ symbol, candles, broker, onRemove }: { symbol: string; candles: OHLCVBar[]; broker?: string; onRemove: () => void }) {
  const live = useLiveLtp(true, symbol, "NSE", broker, 5000);
  return (
    <div className="relative rounded-xl border border-border bg-panel2 p-3">
      <button onClick={onRemove} title="Remove" className="absolute -right-2 -top-2 z-10 rounded-full border border-border bg-panel p-0.5 text-muted hover:text-rose-300"><X size={12} /></button>
      <ProChart candles={candles} symbol={symbol} timeframe="5min" live={live.ltp} liveError={live.error} compact height={160} defaultIndicators={["ema_fast", "vwap"]}
                openUrl={chartWindowUrl(symbol, "5min", "NSE", broker)} deployable />
    </div>
  );
}

/** Phase AN: live index charts on the Dashboard - 5-minute candles for the day through the tenant's
 * broker session, the forming candle moving on the live price. Symbols are a per-viewer convenience
 * kept in the browser (the broker's spelling, e.g. "NIFTY 50", "NIFTY BANK", "RELIANCE"). */
export default function MarketPulseCard({ onNavigate }: { onNavigate?: (page: Page) => void }) {
  const source = useCandleSource(1);
  const [symbols, setSymbols] = useState<string[]>(readSymbols);
  const [draft, setDraft] = useState("");
  const [candles, setCandles] = useState<Record<string, OHLCVBar[]>>({});
  const [error, setError] = useState<string | null>(null);
  const broker = source.broker || undefined;
  const usable = source.usable && !!source.broker;

  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(symbols)); } catch { /* per-viewer convenience only */ } }, [symbols]);

  useEffect(() => {
    if (!usable || symbols.length === 0) { setCandles({}); return; }
    let cancelled = false;
    const load = async () => {
      try {
        // 5 calendar days: the last session is always in it, also on a weekend or after a holiday
        // (1 day returned nothing on a Sunday or a Monday morning).
        const r = await api.marketDataCandles(symbols, "5min", 5, "NSE", broker);
        if (cancelled) return;
        const next: Record<string, OHLCVBar[]> = {};
        for (const s of symbols) next[s] = r.symbols[s.toUpperCase()]?.bars ?? [];
        setCandles(next);
        const failed = symbols.filter((s) => r.symbols[s.toUpperCase()]?.error);
        setError(failed.length ? `${failed.join(", ")}: ${r.symbols[failed[0].toUpperCase()]?.error}` : null);
      } catch (e) { if (!cancelled) setError(String(e)); }
    };
    void load();
    const id = window.setInterval(load, 60_000);
    return () => { cancelled = true; window.clearInterval(id); };
  }, [usable, symbols, broker]);

  const add = (picked?: string) => {
    const s = (picked ?? draft).trim().toUpperCase();
    if (!s || symbols.includes(s) || symbols.length >= 4) return;
    setSymbols([...symbols, s]); setDraft("");
  };

  return (
    <Card title="Market pulse · live 5-minute charts">
      {!usable ? (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          Live index charts need a broker with a valid session today.
          {onNavigate && <button onClick={() => onNavigate("settings")} className="text-sky-300 hover:underline">Log in under Settings &gt; Brokers</button>}
        </div>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-2">
            {symbols.map((s) => <PulseChart key={s} symbol={s} candles={candles[s] ?? []} broker={broker} onRemove={() => setSymbols(symbols.filter((x) => x !== s))} />)}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            {symbols.length < 4 && (
              <>
                <select value="" onChange={(e) => { if (e.target.value) add(e.target.value); }} title="NSE indices and F&O stocks"
                        className="w-48 rounded bg-panel2 border border-border px-2 py-1 text-slate-200">
                  <option value="">Pick index / F&amp;O stock…</option>
                  <optgroup label="Indices">{FNO_INDICES.filter((x) => !symbols.includes(x)).map((x) => <option key={x} value={x}>{x}</option>)}</optgroup>
                  <optgroup label="F&O stocks">{FNO_STOCKS.filter((x) => !symbols.includes(x)).map((x) => <option key={x} value={x}>{x}</option>)}</optgroup>
                </select>
                <input value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} list="fno-symbols"
                       placeholder="…or type to search (any broker symbol)"
                       className="w-56 rounded bg-panel2 border border-border px-2 py-1 text-slate-200" />
                <datalist id="fno-symbols">{[...FNO_INDICES, ...FNO_STOCKS].map((x) => <option key={x} value={x} />)}</datalist>
                <button onClick={() => add()} className="flex items-center gap-1 rounded border border-border px-2 py-1 text-slate-200 hover:bg-panel2"><Plus size={12} /> Add</button>
              </>
            )}
            {error && <span className="text-rose-300">{error}</span>}
            <span className="ml-auto text-muted">candles via {source.broker} · last price every 5 s</span>
          </div>
        </>
      )}
    </Card>
  );
}
