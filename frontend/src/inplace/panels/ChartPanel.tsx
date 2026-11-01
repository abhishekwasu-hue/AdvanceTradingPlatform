import EmbeddedChartV2 from "../../chart/EmbeddedChartV2";
import { isTimeframe } from "../../chart/timeframes";
import type { PanelProps } from "../registry";

/** NAV-1: a chart in place (side panel on a desktop, bottom sheet on a phone). A new timeframe changes the panel's
 * address in place; the chart's own "Open in Chart tab" link is off (no jump from a panel). */
export default function ChartPanel({ params, replace }: PanelProps) {
  const tf = isTimeframe(params.tf) ? params.tf : undefined;
  return (
    <EmbeddedChartV2 symbol={params.symbol ?? ""} exchange={params.exchange || "NSE"} timeframe={tf} openLink={false}
      onTimeframe={(next) => replace({ ...params, tf: next })} className="h-96" />
  );
}
