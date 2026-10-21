import { ExternalLink } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { useLiveLtp } from "../components/chartHelpers";
import { cx } from "../components/primitives/cx";
import { ChartSettingsPopover } from "./ChartSettingsPopover";
import ChartV2 from "./ChartV2";
import type { LevelLine } from "./chartCore";
import { chartTabPath, marketChip, offeredTimeframes } from "./chartsPage";
import { DAILY_OR_LONGER, DEFAULT_TIMEFRAME, timeframeLabel, type TimeframeId } from "./timeframes";
import { useChartCandles } from "./useChartCandles";
import { useChartPrefs } from "./useChartPrefs";
import { useMarketStatus } from "./useMarketStatus";

/**
 * UI2-4e (Charts page plan §3, CHP-10): the v2 chart inside another v2 page (the Signals detail today), the same
 * ChartV2 as the Charts page with the trader's chart settings (theme, grid, candle colours) from the one settings
 * place, the shared timeframe list, the shared candle and live-price reads (no reads of its own), the "Live" / "Price
 * delayed" chip and "Open in Chart tab" with its timeframe. Compact: the price pane only (volume and RSI, and the
 * drawings, are the Chart tab's). Week and month are not offered here (they need the server's support, which the Chart
 * tab checks).
 */
const TIMEFRAMES = offeredTimeframes(null);
const NO_PANES = { volume: false, rsi: false };
const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const iconBtn = cx("inline-flex min-h-11 min-w-11 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg md:min-h-9 md:min-w-9", focus);

export interface EmbeddedChartV2Props {
  symbol: string;
  exchange?: string;
  /** Levels to mark (entry, stop, target). */
  levels?: readonly LevelLine[];
  timeframe?: TimeframeId;
  className?: string;
}

export default function EmbeddedChartV2({ symbol, exchange = "NSE", levels, timeframe: initial = DEFAULT_TIMEFRAME, className = "h-64" }: EmbeddedChartV2Props) {
  const { user } = useAuth();
  const prefs = useChartPrefs(user?.id ?? null);
  // The price pane alone: in a chart this small a lower pane would sit under the library's attribution mark.
  const shown = useMemo(() => ({ ...prefs.prefs, indicators: NO_PANES }), [prefs.prefs]);
  const [timeframe, setTimeframe] = useState<TimeframeId>(initial);
  const { status, at } = useMarketStatus();
  const market = marketChip(status, exchange, at || Date.now());
  const marketOpen = market ? market.open : null;
  const sym = symbol.trim().toUpperCase();
  const data = useChartCandles({ enabled: !!sym, symbol: sym, exchange, timeframe });
  const last = data.candles[data.candles.length - 1];
  // As on the Chart tab: intraday while the market is not known to be closed; day only while its bar is forming.
  const liveOn = DAILY_OR_LONGER.has(timeframe) ? !!last?.forming : marketOpen !== false;
  const live = useLiveLtp(liveOn && data.candles.length > 0, sym, exchange);
  const emptyText = data.loading ? "Loading prices…" : data.error ? "" : `No prices for ${sym} on this timeframe yet.`;

  return (
    <section aria-label={`Chart of ${sym}`} data-embedded-chart={sym} className="overflow-hidden rounded-panel border border-subtle bg-surface-1">
      <div role="toolbar" aria-label={`Chart controls for ${sym}`} className="flex items-center gap-space-1 border-b border-subtle p-space-1">
        <div role="group" aria-label="Timeframe" className="flex min-w-0 flex-1 gap-space-1 overflow-x-auto">
          {TIMEFRAMES.map((tf) => (
            <button key={tf} type="button" aria-pressed={tf === timeframe} onClick={() => setTimeframe(tf)}
              className={cx("min-h-11 min-w-11 shrink-0 rounded-control px-space-1 text-t13 md:min-h-9 md:min-w-9", focus,
                tf === timeframe ? "bg-surface-3 font-semibold text-fg" : "text-fg-muted hover:bg-surface-2 hover:text-fg")}>
              {timeframeLabel(tf)}
            </button>
          ))}
        </div>
        <ChartSettingsPopover prefs={prefs.prefs} onChange={prefs.update} error={prefs.error} />
        <Link to={chartTabPath(sym, exchange, timeframe)} aria-label="Open in Chart tab" title="Open in Chart tab" className={iconBtn}>
          <ExternalLink size={16} aria-hidden />
        </Link>
      </div>
      <ChartV2 candles={data.candles} timeframe={timeframe} symbol={sym} exchange={exchange} prefs={shown} live={live.ltp} marketOpen={marketOpen}
        levels={levels} onLoadOlder={data.loadOlder} drawingTools={false} emptyText={emptyText}
        className={className} />
      {data.error && (
        <p role="status" className="flex flex-wrap items-center gap-x-space-2 border-t border-subtle px-space-3 py-space-1 text-t13 text-fg">
          {data.error}
          <button type="button" onClick={data.retry} className={cx("min-h-11 font-semibold text-brand hover:underline md:min-h-9", focus)}>Try again</button>
        </p>
      )}
    </section>
  );
}
