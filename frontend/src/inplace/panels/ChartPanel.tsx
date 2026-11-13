import EmbeddedChartV2 from "../../chart/EmbeddedChartV2";
import OptionPairChartV2 from "../../chart/OptionPairChartV2";
import { optionRightOf } from "../../chart/optionPair";
import { screenIdOf } from "../../chart/screenParam";
import { isTimeframe } from "../../chart/timeframes";
import { useFeatures } from "../../ui/useFeatures";
import type { PanelProps } from "../registry";

/** NAV-1: a chart in place (side panel on a desktop, bottom sheet on a phone). A new timeframe changes the panel's
 * address in place; the chart's own "Open in Chart tab" link is off (no jump from a panel). M91 C1: `wscreen` - a watchlist's
 * chosen screen, drawn on the chart (the server answers 503 while the flag `watchlist_screen` is off: nothing drawn).
 * CEPE-1 (owner 13 Nov): the chart fills the panel's height (it was a fixed 384 px with the rest blank); an option
 * contract shows its CE and the same-strike PE, one below the other (OptionPairChartV2). */
export default function ChartPanel({ params, replace }: PanelProps) {
  const tf = isTimeframe(params.tf) ? params.tf : undefined;
  const screenOn = useFeatures().on("watchlist_screen");
  const symbol = params.symbol ?? "";
  const exchange = params.exchange || "NSE";
  if (optionRightOf(symbol, exchange)) {
    return <OptionPairChartV2 symbol={symbol} exchange={exchange} timeframe={tf} onTimeframe={(next) => replace({ ...params, tf: next })} />;
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col [&>section]:flex [&>section]:min-h-0 [&>section]:flex-1 [&>section]:flex-col">
      <EmbeddedChartV2 symbol={symbol} exchange={exchange} timeframe={tf} openLink={false}
        screenId={screenOn && screenIdOf(params.wscreen) ? Number(params.wscreen) : null}
        onTimeframe={(next) => replace({ ...params, tf: next })} className="min-h-[18rem] flex-1" />
    </div>
  );
}
