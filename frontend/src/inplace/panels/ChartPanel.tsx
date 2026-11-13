import EmbeddedChartV2 from "../../chart/EmbeddedChartV2";
import PositionChartV2 from "../../chart/PositionChartV2";
import { NO_CANDLES_PARAM, POSITION_PARAM, VIEW_PARAM, viewOf } from "../../chart/positionChart";
import { screenIdOf } from "../../chart/screenParam";
import { isTimeframe } from "../../chart/timeframes";
import { useFeatures } from "../../ui/useFeatures";
import type { PanelProps } from "../registry";

/** NAV-1: a chart in place (side panel on a desktop, bottom sheet on a phone). A new timeframe changes the panel's
 * address in place; the chart's own "Open in Chart tab" link is off (no jump from a panel). M91 C1: `wscreen` - a watchlist's
 * chosen screen, drawn on the chart (the server answers 503 while the flag `watchlist_screen` is off: nothing drawn).
 * Owner bug report 13 Nov: `position` - a position's Chart button: the contract held, with its lines and the switch to
 * its underlying (src/chart/PositionChartV2.tsx). */
export default function ChartPanel({ params, replace }: PanelProps) {
  const tf = isTimeframe(params.tf) ? params.tf : undefined;
  const screenOn = useFeatures().on("watchlist_screen");
  const positionId = Number(params[POSITION_PARAM]);
  if (Number.isInteger(positionId) && positionId > 0) {
    return (
      <PositionChartV2 positionId={positionId} view={viewOf(params[VIEW_PARAM])} noCandles={params[NO_CANDLES_PARAM] === "1"}
        symbol={params.symbol ?? ""} exchange={params.exchange || "NSE"} timeframe={tf} onTimeframe={(next) => replace({ ...params, tf: next })}
        onChange={(next) => {
          const rest = Object.fromEntries(Object.entries(params).filter(([k]) => k !== VIEW_PARAM && k !== NO_CANDLES_PARAM));
          replace({ ...rest, symbol: next.symbol, exchange: next.exchange, ...(next.view === "underlying" ? { [VIEW_PARAM]: "underlying" } : {}),
            ...(next.noCandles ? { [NO_CANDLES_PARAM]: "1" } : {}) });
        }} />
    );
  }
  return (
    <EmbeddedChartV2 symbol={params.symbol ?? ""} exchange={params.exchange || "NSE"} timeframe={tf} openLink={false}
      screenId={screenOn && screenIdOf(params.wscreen) ? Number(params.wscreen) : null}
      onTimeframe={(next) => replace({ ...params, tf: next })} className="h-96" />
  );
}
