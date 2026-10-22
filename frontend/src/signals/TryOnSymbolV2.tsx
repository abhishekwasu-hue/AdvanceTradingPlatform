import { useId, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { useCandleSource } from "../components/DataSource";
import { Sheet } from "../components/primitives";
import { chartsPath } from "../shell/shellRoutes";
import type { EnrichedSignal, OHLCVBar, StrategyInfo } from "../types";
import { buildTimeframeData, generateSampleCandles } from "../utils/sampleData";
import type { SignalFeedItem } from "./feed";
import { Direction, LevelTiles } from "./SignalPartsV2";

const FIELD = "h-11 w-full rounded-control border border-border bg-surface-2 px-space-3 text-[16px] text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9 md:text-t14";
const BUTTON = "inline-flex min-h-11 items-center justify-center rounded-control px-space-4 text-t14 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:cursor-not-allowed disabled:opacity-50 md:min-h-9";

/**
 * UI2-3 (UI v2 proposal §3 M6): the signal generator that used to fill the Signals page, moved to the Library's strategy
 * drawer as "Try it on a symbol" (shown only while `ui_v2` is on; today's Signals page keeps it with the flag off).
 * It runs the strategy on the trader's own broker candles; sample data is only under Advanced, labelled as such (owner
 * brief: no sample data where a trader sees data); a result made from sample data says so and offers no trade.
 * "Paper trade this signal" (broker candles only) is today's paper-execute: under
 * approval-only entries it raises a proposal that then waits in Signals - nothing here places a live order.
 */
export function TryOnSymbolBody({ strategy }: { strategy: StrategyInfo }) {
  const source = useCandleSource();
  const [symbol, setSymbol] = useState("NIFTY 50");
  const [sample, setSample] = useState(false);
  const [bars, setBars] = useState(356);
  const [seed, setSeed] = useState(7);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EnrichedSignal | null>(null);
  // How the shown result was made - its label and its paper-trade button follow this, not the checkbox now.
  const [made, setMade] = useState<{ symbol: string; data: Record<string, OHLCVBar[]>; sample: boolean } | null>(null);
  const [executeMsg, setExecuteMsg] = useState<string | null>(null);
  const ids = { symbol: useId(), bars: useId(), seed: useId(), sample: useId() };

  async function generate(e: FormEvent) {
    e.preventDefault();
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    setBusy(true); setError(null); setExecuteMsg(null);
    const useSample = sample;
    try {
      if (!useSample && !source.usable) throw new Error("Log in to your broker on the Broker page to use real candles, or turn on sample data under Advanced.");
      // Strategies read every timeframe off one-minute bars (as the old page did).
      const broker = source.sources.find((s) => s.usable)?.broker;
      const fetched = useSample
        ? { candles: { [sym]: generateSampleCandles(bars, 100, seed) } as Record<string, OHLCVBar[]> }
        : await brokerBars(sym, broker);
      const base = fetched.candles[sym];
      if (!base?.length) throw new Error(`No candles for ${sym}.`);
      const data = buildTimeframeData(base, strategy.timeframes);
      setResult(await api.enrichSignal(strategy.id, sym, data));
      setMade({ symbol: sym, data, sample: useSample });
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }

  async function brokerBars(sym: string, broker: string | undefined): Promise<{ candles: Record<string, OHLCVBar[]> }> {
    const res = await api.marketDataCandles([sym], "1min", source.lookbackDays, "NSE", broker);
    const entry = res.symbols[sym];
    if (!entry || entry.error || entry.bars.length === 0) throw new Error(`No candles for ${sym} from your broker${entry?.error ? `: ${entry.error}` : "."}`);
    return { candles: { [sym]: entry.bars } };
  }

  async function paperTrade() {
    if (!made || made.sample) return;                 // never a trade from made-up candles
    setExecuteMsg(null);
    try {
      const res = await api.paperExecute(strategy.id, made.symbol, made.data);
      setExecuteMsg(res.executed ? `Paper order placed: ${res.reasons.join("; ")}` : `Not placed: ${res.reasons.join("; ")}`);
    } catch (err) {
      setExecuteMsg(String(err));
    }
  }

  const signal = result?.signal;
  const item: SignalFeedItem | null = signal ? {
    id: "try", at: signal.timestamp, symbol: signal.symbol, exchange: "NSE", direction: signal.direction === "NO_TRADE" ? null : signal.direction,
    setup: signal.strategy_name, entry: signal.entry, stop: signal.stop_loss, target: signal.target1, rr: signal.risk_reward,
    source: { kind: "library", name: "Signal generator" }, mode: null, status: "info", expires_at: null, snoozed_until: null,
    reasons: signal.reasons.slice(0, 3), action_id: null, outcome: null,
  } : null;

  return (
    <div className="space-y-space-4" data-try-on-symbol>
      <p className="text-t14 text-fg-muted">Run {strategy.name} on one symbol now and see what it would signal, with its levels and reasons.</p>
      <form onSubmit={generate} className="space-y-space-3">
        <div>
          <label htmlFor={ids.symbol} className="mb-space-1 block text-t12 font-medium text-fg-muted">Symbol</label>
          <input id={ids.symbol} className={FIELD} value={symbol} onChange={(e) => setSymbol(e.target.value)} autoComplete="off" autoCapitalize="characters" spellCheck={false} />
        </div>
        <details className="rounded-panel border border-subtle px-space-3">
          <summary className="flex min-h-11 cursor-pointer items-center text-t14 font-medium text-fg">Advanced</summary>
          <div className="space-y-space-3 pb-space-3">
            <label htmlFor={ids.sample} className="flex min-h-11 items-center gap-space-2 text-t14 text-fg">
              <input id={ids.sample} type="checkbox" checked={sample} onChange={(e) => setSample(e.target.checked)} className="h-5 w-5" />
              Use sample data instead of your broker&apos;s candles
            </label>
            {sample && (
              <div className="grid grid-cols-2 gap-space-3">
                <div>
                  <label htmlFor={ids.bars} className="mb-space-1 block text-t12 font-medium text-fg-muted">Sample bars</label>
                  <input id={ids.bars} type="number" min={60} max={5000} className={FIELD} value={bars} onChange={(e) => setBars(Number(e.target.value) || 356)} />
                </div>
                <div>
                  <label htmlFor={ids.seed} className="mb-space-1 block text-t12 font-medium text-fg-muted">Sample data seed</label>
                  <input id={ids.seed} type="number" className={FIELD} value={seed} onChange={(e) => setSeed(Number(e.target.value) || 1)} />
                </div>
                <p className="col-span-2 text-t12 text-fg-muted">Sample data is made up. Use it to see how the strategy reads, never to decide a trade.</p>
              </div>
            )}
          </div>
        </details>
        <button type="submit" disabled={busy || !symbol.trim()} aria-busy={busy || undefined} className={`${BUTTON} w-full bg-brand text-on-brand hover:bg-brand-strong`}>
          {busy ? "Working…" : "Try it"}
        </button>
      </form>
      {error && <p role="alert" className="rounded-panel border border-warn/40 bg-warn-tint px-space-3 py-space-2 text-t14 text-fg">{error}</p>}
      {signal && item && (
        <section aria-label="Result" className="space-y-space-3 rounded-panel border border-subtle bg-surface-1 p-space-4">
          <div className="flex flex-wrap items-baseline gap-space-2">
            <h3 className="text-t18 font-semibold text-fg">{signal.symbol}</h3>
            {item.direction ? <Direction value={item.direction} /> : <span className="font-semibold text-fg-muted">No trade now</span>}
            <span className="text-t12 text-fg-muted">Score {result?.composite_score ?? signal.score} · grade {result?.grade ?? signal.grade}{made?.sample ? " · sample data" : ""}</span>
          </div>
          <LevelTiles item={item} />
          {item.reasons.length > 0 && (
            <ol className="list-decimal space-y-space-1 pl-space-5 text-t14 text-fg">{item.reasons.map((r) => <li key={r}>{r}</li>)}</ol>
          )}
          <div className="flex flex-wrap gap-space-2">
            {item.direction && made && !made.sample && (
              <button type="button" onClick={() => void paperTrade()} className={`${BUTTON} border border-border bg-surface-1 text-fg hover:bg-surface-2`}>Paper trade this signal</button>
            )}
            <Link to={chartsPath(signal.symbol)} className={`${BUTTON} text-brand hover:underline`}>Open chart</Link>
          </div>
          {executeMsg && <p role="status" className="text-t14 text-fg-muted">{executeMsg}</p>}
        </section>
      )}
    </div>
  );
}

/** The Library's strategy drawer with "Try it on a symbol" (M6). */
export default function TryOnSymbolV2({ strategy, onClose }: { strategy: StrategyInfo | null; onClose: () => void }) {
  return (
    <Sheet open={strategy !== null} onOpenChange={(o) => { if (!o) onClose(); }} title={strategy ? strategy.name : "Strategy"}
      description="Try it on a symbol">
      {strategy && <TryOnSymbolBody strategy={strategy} />}
    </Sheet>
  );
}
