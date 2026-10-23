/**
 * Chart review PR 3 (docs/design/CHART_COLOUR_REVIEW.md, CC-5): what "Test a strategy" draws on the v2 chart. The v2
 * chart has four price lines (EMA 9, EMA 21, VWAP, SMA 50) and the volume and RSI 14 panes, each at its standard period.
 * A line the strategy reads at that period is drawn; everything else it reads (Bollinger Bands, Supertrend, ADX, or an
 * EMA / RSI at another period) is named under the chart instead, so the chart never shows a line the strategy did not
 * use. Pure.
 */
import { DEFAULT_SETTINGS, indicatorsForStrategy } from "../utils/indicators";
import type { OverlayId, PaneChoice } from "./chartCore";
import { DEFAULT_TIMEFRAME, isTimeframe, type TimeframeId } from "./timeframes";

export interface BacktestChartPlan {
  overlays: OverlayId[];
  panes: PaneChoice;
  /** What the strategy also reads, in plain names with their settings ("Bollinger Bands 20 / 2"). */
  named: string[];
}

export function backtestChartPlan(params?: Record<string, unknown> | null): BacktestChartPlan {
  const { ids, settings: s } = indicatorsForStrategy(params);
  const d = DEFAULT_SETTINGS;
  const overlays: OverlayId[] = [];
  const named: string[] = [];
  const panes: PaneChoice = { volume: ids.includes("volume"), rsi: false };
  for (const id of ids) {
    if (id === "ema_fast") { if (s.emaFast === d.emaFast) overlays.push("ema_fast"); else named.push(`EMA ${s.emaFast}`); }
    else if (id === "ema_slow") { if (s.emaSlow === d.emaSlow) overlays.push("ema_slow"); else named.push(`EMA ${s.emaSlow}`); }
    else if (id === "sma") { if (s.smaPeriod === d.smaPeriod) overlays.push("sma"); else named.push(`SMA ${s.smaPeriod}`); }
    else if (id === "vwap") overlays.push("vwap");
    else if (id === "rsi") { if (s.rsiPeriod === d.rsiPeriod) panes.rsi = true; else named.push(`RSI ${s.rsiPeriod}`); }
    else if (id === "bollinger") named.push(`Bollinger Bands ${s.bbPeriod} / ${s.bbK}`);
    else if (id === "supertrend") named.push(`Supertrend ${s.stPeriod} / ${s.stMult}`);
    else if (id === "adx") named.push(`ADX ${s.adxPeriod}`);
  }
  return { overlays, panes, named: [...new Set(named)] };
}

/** The strategy's first timeframe as a chart timeframe (an unknown one: the default). */
export function backtestTimeframe(tf: string | null | undefined): TimeframeId {
  return isTimeframe(tf) ? tf : DEFAULT_TIMEFRAME;
}
