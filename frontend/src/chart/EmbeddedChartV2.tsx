import { ExternalLink, Filter } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useLiveLtp } from "../components/chartHelpers";
import type { LtpResponse, OHLCVBar } from "../types";
import { cx } from "../components/primitives/cx";
import { useInPlace } from "../inplace/context";
import ChartV2 from "./ChartV2";
import { ScreenLegend, useScreenOverlay } from "./ScreenOverlay";
import type { LevelLine, OverlayId } from "./chartCore";
import type { LayerBand } from "../charting/engine";
import { ChartToolbar } from "./ChartToolbar";
import { toggle as toggleIndicator } from "./indicatorsModel";
import { chartTabPath, marketChip, offeredTimeframes } from "./chartsPage";
import { DAILY_OR_LONGER, DEFAULT_TIMEFRAME, type TimeframeId } from "./timeframes";
import { EMBED_HINT_TEXT, useEmbedActivation } from "./embedActivation";
import { useChartCandles } from "./useChartCandles";
import { useNoBroker } from "../ui/noBroker";
import { useChartPrefs } from "./useChartPrefs";
import { useMarketStatus } from "./useMarketStatus";
import { usePageVisible } from "../utils/usePageVisible";
import { recentKeyFor, type PickedSymbol } from "../ui/SymbolPicker";
import type { SymbolUniverse } from "../ui/symbolUniverse";
import type { LightweightEngine } from "../charting/lightweight";
import type { useDrawingTools } from "../charting/DrawingToolbar";
import { CollapsibleDrawingRail, DrawingMenu } from "./DrawingRail";

type DrawingTools = ReturnType<typeof useDrawingTools>;

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
 * P1-4 (2.3): on a touch screen it works like a map embed (embedActivation.ts): a vertical swipe scrolls the page until a
 * tap activates the chart (an outline; then ChartV2 captures every touch); a tap outside or 3 s idle deactivates it; the
 * first time, the hint "Tap to move the chart".
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
  /** M75 W-1: areas to draw as filled bands (a Copilot answer's nearest area). */
  areas?: readonly LayerBand[];
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
  /** M75 W-2: the picker's universe (the Copilot's chart workspace offers futures and option contracts too). */
  symbolUniverse?: SymbolUniverse;
  /** M75 W-2: the chart's engine for the host's own layers (the workspace draws the reading on it). */
  onEngine?: (engine: LightweightEngine | null) => void;
  /** M75 W-2: the host's own words when there are no prices (a contract: "No prices for this contract ..."). */
  emptyNote?: string;
  /** M91 C1: the watchlist's chosen screen (his own saved screen) drawn on the chart with its legend and one toggle. */
  screenId?: number | null;
  /** Owner bug report 13 Nov: the read's state for the host (a position's contract chart shows its underlying, labelled,
   * when the contract has no candles). Left out: nothing is reported. */
  onRead?: (state: { loading: boolean; count: number; empty: string | null; error: string | null }) => void;
  /** CEPE-1: false hides this chart's own control bar (a host that shows one bar for two linked charts). Default on. */
  toolbar?: boolean;
  /** CEPE-1: the price-pane lines kept by the host (two linked charts share them); left out, the chart keeps its own. */
  overlays?: OverlayId[];
  onOverlays?: (overlays: OverlayId[]) => void;
  /** CEPE-1: the candles shown, handed to the host (its header's last price and change; no read of its own). */
  onCandles?: (candles: readonly OHLCVBar[]) => void;
  /** Owner report 13 Nov: the drawing tools on this chart (the Terminal's chart panel) - the Charts page's own rail
   * (foldable) from 768 px and its Draw menu on a phone, on the same drawings (saved per user and symbol), so a line
   * drawn here shows on the Charts page and back. Owner 14 Nov ("it must be on every chart window"): on by default, so
   * every chart window has them (the chart sheet in all its modes, Signals, the Copilot workspace); false only for a
   * chart that is not a window of its own (a chat answer's preview, a reading figure). Without the control bar
   * (`toolbar={false}`, the lower chart of a CE / PE pair) the phone's Draw menu sits over this chart itself. */
  drawing?: boolean;
}

export default function EmbeddedChartV2({
  symbol, exchange = "NSE", levels, areas, timeframe: given, onTimeframe, openLink = true, className = "h-64", live: hostLive, onSymbol, symbolUniverse,
  onEngine, emptyNote, screenId = null, drawing = true, toolbar = true, overlays: hostOverlays, onOverlays, onCandles, onRead,
}: EmbeddedChartV2Props) {
  const { user } = useAuth();
  const [ownOverlays, setOwnOverlays] = useState<OverlayId[]>([]);
  const overlays = hostOverlays ?? ownOverlays;
  const setOverlays = (next: OverlayId[]) => { setOwnOverlays(next); onOverlays?.(next); };
  const prefs = useChartPrefs(user?.id ?? null);
  // The price pane alone: in a chart this small a lower pane would sit under the library's attribution mark.
  const shown = useMemo(() => ({ ...prefs.prefs, indicators: NO_PANES }), [prefs.prefs]);
  const [own, setOwn] = useState<TimeframeId>(given ?? DEFAULT_TIMEFRAME);
  // M86 D2: without a broker an intraday chart has no bars - D (NSE's end-of-day files) until a timeframe is picked here.
  const noBroker = useNoBroker();
  const [picked, setPicked] = useState(false);
  const chosen = onTimeframe && given ? given : own;
  const timeframe: TimeframeId = noBroker && !picked && !DAILY_OR_LONGER.has(chosen) ? "day" : chosen;
  const setTimeframe = (tf: TimeframeId) => { setPicked(true); setOwn(tf); onTimeframe?.(tf); };
  // UI2-4e review: nothing is read while the browser tab is hidden (one refresh when it shows again), as the cells do.
  const visible = usePageVisible();
  const { status, at } = useMarketStatus();
  const market = marketChip(status, exchange, at || Date.now());
  const marketOpen = market ? market.open : null;
  const sym = symbol.trim().toUpperCase();
  const data = useChartCandles({ enabled: !!sym, paused: !visible, symbol: sym, exchange, timeframe });
  const reportRef = useRef(onRead);
  reportRef.current = onRead;
  const count = data.candles.length;
  useEffect(() => { reportRef.current?.({ loading: data.loading, count, empty: data.empty, error: data.error }); }, [data.loading, count, data.empty, data.error]);
  const last = data.candles[data.candles.length - 1];
  const candlesTo = useRef(onCandles);
  candlesTo.current = onCandles;
  useEffect(() => { candlesTo.current?.(data.candles); }, [data.candles]);
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
  const box = useRef<HTMLDivElement>(null);
  const activation = useEmbedActivation(box);
  // M91 C1: the watchlist's screen on this chart (its own engine handle; the host's onEngine still gets it too)
  const [engine, setEngine] = useState<LightweightEngine | null>(null);
  const takeEngine = useCallback((e: LightweightEngine | null) => { setEngine(e); onEngine?.(e); }, [onEngine]);
  const [screenShown, setScreenShown] = useState(true);
  const screen = useScreenOverlay(engine, { screenId, symbol: sym, exchange, timeframe, candles: data.candles, on: screenShown && screenId != null });
  const allLevels = useMemo(() => (screenId != null && screen.levels.length ? [...(levels ?? []), ...screen.levels] : levels), [levels, screen.levels, screenId]);
  const [tools, setTools] = useState<DrawingTools | null>(null);
  const [drawingsHidden, setDrawingsHidden] = useState(false);
  const drawingProps = drawing ? { tools, symbol: sym, hidden: drawingsHidden, onHidden: setDrawingsHidden } : undefined;
  const emptyText = data.loading ? "Loading prices…" : data.error ? "" : data.empty ?? emptyNote ?? `No prices for ${sym} on this timeframe yet.`;

  return (
    <section aria-label={`Chart of ${sym}`} data-embedded-chart={sym} className="overflow-hidden rounded-panel border border-subtle bg-surface-1">
      {toolbar && <ChartToolbar mode="compact" label={`Chart controls for ${sym}`}
        symbol={onSymbol ? { value: sym, exchange, onPick: onSymbol, storageKey: recentKeyFor(user?.id ?? null), universe: symbolUniverse } : undefined}
        timeframe={{ offered: TIMEFRAMES, value: timeframe, onChange: setTimeframe,
          favourites: prefs.prefs.timeframe_favourites, onFavourites: (next) => prefs.update({ timeframe_favourites: next }) }}
        indicators={{ state: indicatorState, on: () => false, onToggle: onIndicator, groups: EMBEDDED_GROUPS }}
        drawing={drawingProps}
        settings={{ prefs: prefs.prefs, onChange: prefs.update, error: prefs.error }}
        endActions={[
          ...(screenId != null ? [{ id: "screen-overlay", label: "Screen overlay", title: screenShown ? "Hide the screen overlay" : "Show the screen overlay",
            moreLabel: screenShown ? "Hide the screen overlay" : "Show the screen overlay", pressed: screenShown, testId: "screen-overlay-button",
            icon: <Filter size={16} aria-hidden />, onClick: () => setScreenShown((v) => !v) }] : []),
          ...(openLink ? [{ id: "open", label: openLabel, icon: <ExternalLink size={16} aria-hidden />, to: openTo }] : []),
        ]} />}
      {!toolbar && drawingProps && (
        <div className="flex justify-end border-b border-subtle px-space-2 py-space-1 md:hidden" data-drawing-menu-own>
          <DrawingMenu {...drawingProps} sheet />
        </div>
      )}
      {/* a column that fills a flex host (the Terminal's panel), so a host's `flex-1` on the chart still works */}
      <div className="flex min-h-0 flex-1">
      {drawingProps && <CollapsibleDrawingRail {...drawingProps} />}
      <div ref={box} data-chart-active={activation.active ? "on" : "off"} {...activation.handlers}
           className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        <ChartV2 candles={data.candles} timeframe={timeframe} symbol={sym} exchange={exchange} prefs={shown} overlays={overlays} live={live.ltp} marketOpen={marketOpen}
          levels={allLevels} areas={areas} markers={screenId != null ? screen.markers : undefined} onLoadOlder={data.loadOlder} drawingTools={drawing && !drawingsHidden}
          onTools={drawing ? setTools : undefined} emptyText={emptyText} onEngine={takeEngine}
          captureTouch={activation.active} className={className} />
        {/* the outline over the canvases (an inset ring on this box would sit under the chart's own background) */}
        {activation.active && <span data-chart-outline aria-hidden className="pointer-events-none absolute inset-0 z-20 ring-2 ring-inset ring-brand" />}
        {activation.hint && data.candles.length > 0 && (
          <p data-chart-hint aria-hidden className="pointer-events-none absolute inset-x-0 bottom-space-6 z-20 flex justify-center">
            <span className="rounded-pill bg-surface-3 px-space-3 py-space-1 text-t12 font-semibold text-fg shadow-raised">{EMBED_HINT_TEXT}</span>
          </p>
        )}
      </div>
      </div>
      {screenId != null && screenShown && <ScreenLegend state={screen} onShowTf={setTimeframe} />}
      {data.error && (
        <p role="status" className="flex flex-wrap items-center gap-x-space-2 border-t border-subtle px-space-3 py-space-1 text-t13 text-fg">
          {data.error}
          <button type="button" onClick={data.retry} className={cx("min-h-11 font-semibold text-brand hover:underline md:min-h-9", focus)}>Try again</button>
        </p>
      )}
    </section>
  );
}
