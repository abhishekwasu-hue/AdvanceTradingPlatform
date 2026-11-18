import { useEffect, useMemo, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import type { LayerData, LayerSegment } from "../charting/engine";
import type { LightweightEngine } from "../charting/lightweight";
import type { ChartMarker } from "../charting/markers";
import type { OHLCVBar, Trade } from "../types";
import ChartV2 from "./ChartV2";
import { backtestChartPlan, backtestTimeframe } from "./backtestChart";
import type { StrategyLines } from "./templateLines";
import { useChartPrefs } from "./useChartPrefs";

const TRADES_LAYER = "backtest-trades";
const INVALIDATION_COLOR = "#dc2626";

/** BT-DRAW: each trade's own stop (where its idea is wrong) as a dashed line from its entry to its exit. Pure. */
export function tradeLayer(trades: readonly Trade[]): LayerData {
  const sec = (iso: string | null) => (iso ? Math.floor(Date.parse(iso) / 1000) : NaN);
  const segments: LayerSegment[] = [];
  for (const t of trades) {
    const t1 = sec(t.entry_time), t2 = sec(t.exit_time);
    if (!Number.isFinite(t1) || !Number.isFinite(t2) || !(t.stop_loss > 0)) continue;
    segments.push({ t1, p1: t.stop_loss, t2, p2: t.stop_loss, color: INVALIDATION_COLOR, width: 1, style: "dashed" });
  }
  return { segments };
}

/**
 * Chart review PR 3 (CC-5): the "Test a strategy" chart under `ui_v2` - the same v2 chart as the Charts page (both axes,
 * Auto / Reset / Latest, the crosshair readout) with the backtest's entries and exits as markers, the trader's chart
 * settings (theme, grid, candle colours) and the lines the strategy reads (`backtestChartPlan`); what the v2 chart does
 * not draw is named under it. The bars are the backtest's own, so nothing is read here and there is no live price. No
 * drawing tools: they are saved per symbol for the Charts page. ProChart stays for the pages without `ui_v2`.
 */
export default function BacktestChartV2({ candles, symbol, timeframe, markers, strategyParams, lines = null, trades = [] }: {
  candles: OHLCVBar[];
  symbol: string;
  timeframe?: string;
  markers: readonly ChartMarker[];
  strategyParams?: Record<string, unknown> | null;
  /** BT-DRAW: a template's own lines (`templateLines`); when given, only these are drawn - never the default EMA pair. */
  lines?: StrategyLines | null;
  /** BT-DRAW: the test's trades, for each one's stop line between its entry and exit. */
  trades?: readonly Trade[];
}) {
  const { user } = useAuth();
  const { prefs } = useChartPrefs(user?.id ?? null);
  const [engine, setEngine] = useState<LightweightEngine | null>(null);
  const plan = useMemo(() => (lines ? { overlays: [], panes: { volume: true, rsi: false }, named: lines.named } : backtestChartPlan(strategyParams)),
    [lines, strategyParams]);
  const layer = useMemo(() => tradeLayer(trades), [trades]);
  useEffect(() => {
    if (!engine) return undefined;
    const studies = lines?.studies ?? [];
    for (const st of studies) engine.setStudy?.(st.id, st.line);
    engine.setLayer(TRADES_LAYER, layer);
    return () => {
      for (const st of studies) engine.setStudy?.(st.id, null);
      engine.setLayer(TRADES_LAYER, null);
    };
  }, [engine, lines, layer]);
  const shown = useMemo(() => ({ ...prefs, indicators: plan.panes }), [prefs, plan.panes]);
  return (
    <div data-backtest-chart className="space-y-space-2">
      <ChartV2 candles={candles} timeframe={backtestTimeframe(timeframe)} symbol={symbol} prefs={shown} overlays={plan.overlays}
        markers={markers} drawingTools={false} onEngine={setEngine} emptyText="Run a test to see its trades on the chart." className="h-[440px] overflow-hidden rounded-control md:h-[520px]" />
      {(lines?.studies.length || trades.length > 0) ? (
        <ul data-backtest-legend className="flex flex-wrap gap-x-space-3 gap-y-space-1 text-t13 text-fg-muted">
          {lines?.studies.filter((st) => st.line.title).map((st) => (
            <li key={st.id} className="inline-flex items-center gap-space-1">
              <span aria-hidden className="inline-block h-0.5 w-4" style={{ backgroundColor: st.line.color }} />{st.line.title}{st.line.pane === "own" ? " (own pane)" : ""}
            </li>
          ))}
          {trades.some((t) => t.setup_time) && (
            <li className="inline-flex items-center gap-space-1">
              <span aria-hidden className="inline-block h-2 w-2" style={{ backgroundColor: "#7c3aed" }} />Setup: where the idea began
            </li>
          )}
          {trades.length > 0 && (
            <li className="inline-flex items-center gap-space-1">
              <span aria-hidden className="inline-block w-4 border-t border-dashed" style={{ borderColor: INVALIDATION_COLOR }} />Each trade&apos;s stop, from entry to exit
            </li>
          )}
        </ul>
      ) : null}
      {lines && lines.studies.length === 0 && lines.named.length === 0 && (
        <p data-backtest-structure className="text-t13 text-fg-muted">This strategy reads price structure only; no indicator lines are drawn.</p>
      )}
      {plan.named.length > 0 && (
        <p data-backtest-named className="text-t13 text-fg-muted">
          This strategy also reads {plan.named.join(", ")}. The chart does not draw {plan.named.length === 1 ? "it" : "them"}.
        </p>
      )}
    </div>
  );
}
