import { useMemo } from "react";
import { useAuth } from "../auth/AuthContext";
import type { ChartMarker } from "../charting/markers";
import type { OHLCVBar } from "../types";
import ChartV2 from "./ChartV2";
import { backtestChartPlan, backtestTimeframe } from "./backtestChart";
import { useChartPrefs } from "./useChartPrefs";

/**
 * Chart review PR 3 (CC-5): the "Test a strategy" chart under `ui_v2` - the same v2 chart as the Charts page (both axes,
 * Auto / Reset / Latest, the crosshair readout) with the backtest's entries and exits as markers, the trader's chart
 * settings (theme, grid, candle colours) and the lines the strategy reads (`backtestChartPlan`); what the v2 chart does
 * not draw is named under it. The bars are the backtest's own, so nothing is read here and there is no live price. No
 * drawing tools: they are saved per symbol for the Charts page. ProChart stays for the pages without `ui_v2`.
 */
export default function BacktestChartV2({ candles, symbol, timeframe, markers, strategyParams }: {
  candles: OHLCVBar[];
  symbol: string;
  timeframe?: string;
  markers: readonly ChartMarker[];
  strategyParams?: Record<string, unknown> | null;
}) {
  const { user } = useAuth();
  const { prefs } = useChartPrefs(user?.id ?? null);
  const plan = useMemo(() => backtestChartPlan(strategyParams), [strategyParams]);
  const shown = useMemo(() => ({ ...prefs, indicators: plan.panes }), [prefs, plan.panes]);
  return (
    <div data-backtest-chart className="space-y-space-2">
      <ChartV2 candles={candles} timeframe={backtestTimeframe(timeframe)} symbol={symbol} prefs={shown} overlays={plan.overlays}
        markers={markers} drawingTools={false} emptyText="Run a test to see its trades on the chart." className="h-[440px] overflow-hidden rounded-control md:h-[520px]" />
      {plan.named.length > 0 && (
        <p data-backtest-named className="text-t13 text-fg-muted">
          This strategy also reads {plan.named.join(", ")}. The chart does not draw {plan.named.length === 1 ? "it" : "them"}.
        </p>
      )}
    </div>
  );
}
