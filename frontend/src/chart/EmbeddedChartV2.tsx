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
import { DAILY_OR_LONGER, DEFAULT_TIMEFRAME, timeframeLabel, timeframeShort, type TimeframeId } from "./timeframes";
import { useChartCandles } from "./useChartCandles";
import { useChartPrefs } from "./useChartPrefs";
import { useMarketStatus } from "./useMarketStatus";
import { usePageVisible } from "../utils/usePageVisible";
import { TimeframeTabs } from "../ui/TimeframeTabs";

/**
 * UI2-4e (Charts page plan §3, CHP-10): the v2 chart inside another v2 page (the Signals detail today), the same
 * ChartV2 as the Charts page with the trader's chart settings (theme, grid, candle colours) from the one settings
 * place, the shared timeframe list, the shared candle and live-price reads (no reads of its own), the "Live" / "Price
 * delayed" chip and "Open in Chart tab" with its timeframe (or the host's own link with it). Compact: the price pane only (volume and RSI, and the
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
  /** The timeframe shown; with `onTimeframe` the host keeps it (Signals: its "Open chart" carries it). */
  timeframe?: TimeframeId;
  onTimeframe?: (tf: TimeframeId) => void;
  /** The chart's own "Open in Chart tab" link (default on); off when the host page shows one with the timeframe. */
  openLink?: boolean;
  className?: string;
}

export default function EmbeddedChartV2({
  symbol, exchange = "NSE", levels, timeframe: given, onTimeframe, openLink = true, className = "h-64",
}: EmbeddedChartV2Props) {
  const { user } = useAuth();
  const prefs = useChartPrefs(user?.id ?? null);
  // The price pane alone: in a chart this small a lower pane would sit under the library's attribution mark.
  const shown = useMemo(() => ({ ...prefs.prefs, indicators: NO_PANES }), [prefs.prefs]);
  const [own, setOwn] = useState<TimeframeId>(given ?? DEFAULT_TIMEFRAME);
  const timeframe = onTimeframe && given ? given : own;
  const setTimeframe = (tf: TimeframeId) => { setOwn(tf); onTimeframe?.(tf); };
  // UI2-4e review: nothing is read while the browser tab is hidden (one refresh when it shows again), as the cells do.
  const visible = usePageVisible();
  const { status, at } = useMarketStatus();
  const market = marketChip(status, exchange, at || Date.now());
  const marketOpen = market ? market.open : null;
  const sym = symbol.trim().toUpperCase();
  const data = useChartCandles({ enabled: !!sym, paused: !visible, symbol: sym, exchange, timeframe });
  const last = data.candles[data.candles.length - 1];
  // As on the Chart tab: intraday while the market is not known to be closed; day only while its bar is forming.
  const liveOn = DAILY_OR_LONGER.has(timeframe) ? !!last?.forming : marketOpen !== false;
  const live = useLiveLtp(liveOn && data.candles.length > 0, sym, exchange);
  const emptyText = data.loading ? "Loading prices…" : data.error ? "" : `No prices for ${sym} on this timeframe yet.`;

  return (
    <section aria-label={`Chart of ${sym}`} data-embedded-chart={sym} className="overflow-hidden rounded-panel border border-subtle bg-surface-1">
      <div role="toolbar" aria-label={`Chart controls for ${sym}`} className="flex items-center gap-space-1 border-b border-subtle p-space-1">
        <div className="min-w-0 flex-1">
          <TimeframeTabs value={timeframe} onChange={setTimeframe}
            options={TIMEFRAMES.map((tf) => ({ key: tf, label: timeframeLabel(tf), short: timeframeShort(tf) }))} />
        </div>
        <ChartSettingsPopover prefs={prefs.prefs} onChange={prefs.update} error={prefs.error} />
        {openLink && (
          <Link to={chartTabPath(sym, exchange, timeframe)} aria-label="Open in Chart tab" title="Open in Chart tab" className={iconBtn}>
            <ExternalLink size={16} aria-hidden />
          </Link>
        )}
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
