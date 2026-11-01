import FuturesChainV2 from "../../fno/FuturesChainV2";
import { useHubInfo } from "../../fno/SymbolHubTabsV2";
import { isTimeframe } from "../../chart/timeframes";
import type { PanelProps } from "../registry";

/** NAV-4: the symbol hub's Futures tab in place (a position's Adjust for a future) - the same futures chain the Chart
 * tab shows at `/charts/<symbol>?hub=futures`, for the same underlying. */
export default function FuturesPanel({ params }: PanelProps) {
  const symbol = params.symbol ?? "";
  const info = useHubInfo(symbol);
  return <FuturesChainV2 symbol={info?.underlying ?? symbol} timeframe={isTimeframe(params.tf) ? params.tf : null} />;
}
