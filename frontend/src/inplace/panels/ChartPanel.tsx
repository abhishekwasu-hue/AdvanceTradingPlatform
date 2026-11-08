import EmbeddedChartV2 from "../../chart/EmbeddedChartV2";
import { screenIdOf } from "../../chart/screenParam";
import { isTimeframe } from "../../chart/timeframes";
import { useFeatures } from "../../ui/useFeatures";
import type { PanelProps } from "../registry";

/** NAV-1: a chart in place (side panel on a desktop, bottom sheet on a phone). A new timeframe changes the panel's
 * address in place; the chart's own "Open in Chart tab" link is off (no jump from a panel). M91 C1: `wscreen` - a watchlist's
 * chosen screen, drawn on the chart (the server answers 503 while the flag `watchlist_screen` is off: nothing drawn). */
export default function ChartPanel({ params, replace }: PanelProps) {
  const tf = isTimeframe(params.tf) ? params.tf : undefined;
  const screenOn = useFeatures().on("watchlist_screen");
  return (
    <EmbeddedChartV2 symbol={params.symbol ?? ""} exchange={params.exchange || "NSE"} timeframe={tf} openLink={false}
      screenId={screenOn && screenIdOf(params.wscreen) ? Number(params.wscreen) : null}
      onTimeframe={(next) => replace({ ...params, tf: next })} className="h-96" />
  );
}
