/**
 * N4 CH4a: the user's own PAPER trades on the chart, as of `at` (replay: the replayed bar's end). A small read-only panel
 * says how many are drawn and lets the user hide them; there is no control here that could place or change an order.
 * Flag `chart_trades_layer`: while it is off the backend answers 503 and nothing is shown.
 */
import { useEffect, useMemo, useState } from "react";
import type { ChartEngine } from "./engine";
import { TRADES_LAYER_ID, TradesLoader, layerBars, toTradesLayer, tradesApi, type TradesResponse, type TradesState } from "./tradesLayer";

const btn = "rounded px-1.5 py-0.5 text-[10px] font-semibold";

export function TradesPanel({ resp, shown, onShown }: { resp: TradesResponse; shown: boolean; onShown: (on: boolean) => void }) {
  const n = resp.trades.length;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-muted" data-testid="trades-panel" title={resp.note}>
      <span className="font-semibold text-fg">My trades</span>
      <button type="button" aria-pressed={shown} onClick={() => onShown(!shown)}
              className={`${btn} ${shown ? "bg-brand/15 text-brand ring-1 ring-brand/40" : "text-fg-muted hover:text-fg"}`}>
        {shown ? "Shown" : "Hidden"}
      </button>
      <span>{n === 0 ? "none on this chart" : `${n} ${resp.mode} trade${n === 1 ? "" : "s"}${resp.open ? ` · ${resp.open} open` : ""}`}{resp.capped ? " (most recent only)" : ""}</span>
      <span className="opacity-70">read-only · places no orders</span>
    </div>
  );
}

export function TradesLayer({ engine, instrument, candles, at = null, refreshKey = null }: {
  engine: ChartEngine | null; instrument: string; candles: readonly { timestamp: string; high: number; low: number }[]; at?: string | null;
  /** Live view: changes when a new candle starts, so a trade entered since is picked up. */
  refreshKey?: string | number | null;
}) {
  const [state, setState] = useState<TradesState>({ resp: null, failed: null, available: null });
  const [shown, setShown] = useState(true);
  const loader = useMemo(() => new TradesLoader(tradesApi.layer, setState), []);
  const bars = useMemo(() => layerBars(candles), [candles]);
  // the window starts at the first bar on screen: a trade before it could not be drawn anyway
  const start = bars.length ? new Date(bars[0].time * 1000).toISOString() : null;
  useEffect(() => loader.load(instrument || null, at, start), [loader, instrument, at, start, refreshKey]);

  const { resp, failed } = state;
  const data = useMemo(() => (resp && shown ? toTradesLayer(resp, bars) : null), [resp, shown, bars]);
  useEffect(() => {
    if (!engine) return;
    engine.setLayer(TRADES_LAYER_ID, data);
    return () => engine.setLayer(TRADES_LAYER_ID, null);
  }, [engine, data]);

  if (failed) return <div className="text-xs text-fg-muted">Your trades could not be loaded: {failed}</div>;
  if (!resp) return null;
  return <TradesPanel resp={resp} shown={shown} onShown={setShown} />;
}

export default TradesLayer;
