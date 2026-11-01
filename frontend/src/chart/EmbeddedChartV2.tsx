import { ExternalLink } from "lucide-react";
import { useMemo, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useLiveLtp } from "../components/chartHelpers";
import type { LtpResponse } from "../types";
import { cx } from "../components/primitives/cx";
import { useInPlace } from "../inplace/InPlaceProvider";
import ChartV2 from "./ChartV2";
import type { LevelLine, OverlayId } from "./chartCore";
import { ChartToolbar } from "./ChartToolbar";
import { toggle as toggleIndicator } from "./indicatorsModel";
import { chartTabPath, marketChip, offeredTimeframes } from "./chartsPage";
import { DAILY_OR_LONGER, DEFAULT_TIMEFRAME, type TimeframeId } from "./timeframes";
import { useChartCandles } from "./useChartCandles";
import { useChartPrefs } from "./useChartPrefs";
import { useMarketStatus } from "./useMarketStatus";
import { usePageVisible } from "../utils/usePageVisible";
import { recentKeyFor, type PickedSymbol } from "../ui/SymbolPicker";

/**
 * UI2-4e (Charts page plan §3, CHP-10): the v2 chart inside another v2 page (the Signals detail today), the same
 * ChartV2 as the Charts page with the trader's chart settings (theme, grid, candle colours) from the one settings
 * place, the shared timeframe list, the shared candle and live-price reads (no reads of its own), the "Live" / "Price
 * delayed" chip and "Open in Chart tab" with its timeframe (or the host's own link with it). Compact: the price pane only (volume and RSI, and the
 * drawings, are the Chart tab's). Week and month are not offered here (they need the server's support, which the Chart
 * tab checks).
 * CT-1 C1: its controls are the one ChartToolbar in compact mode - the timeframe, Indicators (the price-pane lines only:
 * still no lower pane here), the chart settings and "Open in Chart tab"; with `onSymbol` also the SymbolPicker (a host
 * that lets the trader change the symbol, e.g. the Terminal on a phone). Every older prop works as before. The timeframe
 * control stays the shared <TimeframePicker> (TF-1), drawn by ChartToolbar.
 */
const TIMEFRAMES = offeredTimeframes(null);
const NO_PANES = { volume: false, rsi: false };
const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
/** The price-pane lines only: an embedded chart shows no lower pane. */
const EMBEDDED_GROUPS = ["popular"] as const;

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
  /** The host's own live price for this symbol (the Terminal: the one spot its chain shows too, T1-3). Given (even null),
   *  the chart makes no live read of its own. */
  live?: (Pick<LtpResponse, "ltp" | "timestamp" | "fetched_at">) | null;
  /** CT-1 C1: given, the toolbar shows the SymbolPicker and a pick is handed to the host (which owns the symbol). */
  onSymbol?: (pick: PickedSymbol) => void;
}

export default function EmbeddedChartV2({
  symbol, exchange = "NSE", levels, timeframe: given, onTimeframe, openLink = true, className = "h-64", live: hostLive, onSymbol,
}: EmbeddedChartV2Props) {
  const { user } = useAuth();
  const [overlays, setOverlays] = useState<OverlayId[]>([]);
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
  const ownLive = useLiveLtp(hostLive === undefined && liveOn && data.candles.length > 0, sym, exchange);
  const live = hostLive === undefined ? ownLive : { ltp: liveOn ? hostLive : null };
  const indicatorState = { panes: NO_PANES, overlays, layers: [] };
  const onIndicator = (id: string) => {
    const change = toggleIndicator(id, indicatorState);
    if ("overlays" in change) setOverlays(change.overlays);
  };
  // M75 NAV-3: the link opens the chart in place (a larger chart in a side panel / bottom sheet, the tab stays); it names
  // the Chart tab only where it still goes there (ui_v2 off).
  const inPlace = useInPlace();
  const openTo = chartTabPath(sym, exchange, timeframe);
  const openLabel = inPlace.handles(openTo) ? "Open a larger chart" : "Open in Chart tab";
  const emptyText = data.loading ? "Loading prices…" : data.error ? "" : data.empty ?? `No prices for ${sym} on this timeframe yet.`;

  return (
    <section aria-label={`Chart of ${sym}`} data-embedded-chart={sym} className="overflow-hidden rounded-panel border border-subtle bg-surface-1">
      <ChartToolbar mode="compact" label={`Chart controls for ${sym}`}
        symbol={onSymbol ? { value: sym, exchange, onPick: onSymbol, storageKey: recentKeyFor(user?.id ?? null) } : undefined}
        timeframe={{ offered: TIMEFRAMES, value: timeframe, onChange: setTimeframe,
          favourites: prefs.prefs.timeframe_favourites, onFavourites: (next) => prefs.update({ timeframe_favourites: next }) }}
        indicators={{ state: indicatorState, on: () => false, onToggle: onIndicator, groups: EMBEDDED_GROUPS }}
        settings={{ prefs: prefs.prefs, onChange: prefs.update, error: prefs.error }}
        endActions={openLink ? [{ id: "open", label: openLabel, icon: <ExternalLink size={16} aria-hidden />, to: openTo }] : []} />
      <ChartV2 candles={data.candles} timeframe={timeframe} symbol={sym} exchange={exchange} prefs={shown} overlays={overlays} live={live.ltp} marketOpen={marketOpen}
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
