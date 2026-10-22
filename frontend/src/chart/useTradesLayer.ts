/**
 * UI2-4b2: the Indicators menu's "My trades" layer on the v2 chart - the trader's own paper trades (CH4a, flag
 * `chart_trades_layer`), drawn through the chart engine as on today's chart, without today's panel text. Read only:
 * nothing here can place or change an order.
 */
import { useEffect, useMemo, useState } from "react";
import type { LightweightEngine } from "../charting/lightweight";
import { TRADES_LAYER_ID, TradesLoader, layerBars, toTradesLayer, tradesApi, type TradesState } from "../charting/tradesLayer";
import type { ChartBar } from "./timeframes";

/** Draws the layer while `on`; returns a plain line when the trades could not be read (null otherwise). */
export function useTradesLayer(engine: LightweightEngine | null, instrument: string, candles: readonly ChartBar[], on: boolean): string | null {
  const [state, setState] = useState<TradesState>({ resp: null, failed: null, available: null });
  const loader = useMemo(() => new TradesLoader(tradesApi.layer, setState), []);
  const bars = useMemo(() => layerBars(candles), [candles]);
  const start = bars.length ? new Date(bars[0].time * 1000).toISOString() : null;
  const newest = bars.length ? bars[bars.length - 1].time : null;          // a new bar: a trade entered since shows up
  useEffect(() => loader.load(on && start ? instrument : null, null, start), [loader, on, instrument, start, newest]);

  const data = useMemo(() => (on && state.resp ? toTradesLayer(state.resp, bars) : null), [on, state.resp, bars]);
  useEffect(() => {
    if (!engine) return undefined;
    engine.setLayer(TRADES_LAYER_ID, data);
    return () => engine.setLayer(TRADES_LAYER_ID, null);
  }, [engine, data]);
  return on && state.failed ? "Your trades could not be loaded. Turn My trades off and on again to retry." : null;
}
