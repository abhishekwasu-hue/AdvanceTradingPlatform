import { ListTree, Maximize2, Minimize2, Redo2, ScanSearch, Undo2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { DrawingKind } from "../charting/drawings";
import type { useDrawingTools } from "../charting/DrawingToolbar";
import type { LightweightEngine } from "../charting/lightweight";
import type { DrawingController } from "../charting/tools";
import { LtpHub } from "../charting/ltpHub";
import { useLiveLtp } from "../components/chartHelpers";
import { Sheet } from "../components/primitives/Dialog";
import { cx } from "../components/primitives/cx";
import { pathFor, type PageProps } from "../routes";
import { chartsPath } from "../shell/shellRoutes";
import { PageIntro } from "../ui/PageIntro";
import { SymbolPicker, recentKeyFor, type PickedSymbol } from "../ui/SymbolPicker";
import { useFeatures } from "../ui/useFeatures";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import { ChartSettingsPopover } from "./ChartSettingsPopover";
import ChartV2 from "./ChartV2";
import { CHART_TYPES_V2, CHART_TYPE_LABELS_V2, type ChartTypeV2 } from "./chartCore";
import { DrawingMenu, DrawingRail } from "./DrawingRail";
import { placeHint } from "./drawingRail";
import {
  addressTimeframe, chartsSymbol, drawingNote, marketChip, offeredTimeframes, readLastSymbol, readView, saveLastSymbol, saveView, volumeLabelFor,
  type ChartsView,
} from "./chartsPage";
import { IndicatorsMenu } from "./IndicatorsMenu";
import { toggle as toggleIndicator } from "./indicatorsModel";
import { CandlePool, START_PARAMS, startChartFrom } from "./multiChart";
import MultiChartV2 from "./MultiChartV2";
import { DAILY_OR_LONGER, DEFAULT_TIMEFRAME, timeframeLabel, type TimeframeId } from "./timeframes";
import { useChartCandles } from "./useChartCandles";
import { useChartPrefs } from "./useChartPrefs";
import { useFullscreen } from "./useFullscreen";
import { useMarketStatus } from "./useMarketStatus";
import { useTradesLayer } from "./useTradesLayer";
import { WatchlistPanel } from "./WatchlistPanel";
import type { WatchItem } from "./watchlistsV2";

/**
 * UI2-4b2 (UI v2 proposal §6, Charts page plan §3): the Charts page at `/charts` and `/charts/:symbol`, v2 only.
 * - Tab "Chart" (default): one large v2 chart (ChartV2) and one top bar - symbol, market state, timeframe, chart type,
 *   Indicators, undo / redo for drawings, chart settings, full screen (button, F, Escape).
 * - Tab "Multi-chart" (only while `chart_workspace` is on): UI2-4d, the CH5 workspace with a v2 chart per cell
 *   (MultiChartV2). Each tab reads nothing while the other shows; both keep their bars, and the cells' shared reads
 *   (one candle pool, one last-price hub) belong to the page, so switching tabs does not read everything again.
 * The v2 shell puts the broker gate in front of this page (src/ui/dataPages.ts): signed out, with no broker or before
 * the day's broker login the trader sees the gate card, never candles or sample data. Candles, the live price and the
 * drawings all come from the trader's own broker session and account.
 *
 * UI2-4c: the drawing rail left of the chart (a "Draw" menu on a phone) and, while `watchlists` is on, the watchlist -
 * a right column from 1024 px (it can be hidden), a bottom sheet below. A watchlist row opens its symbol and keeps the
 * timeframe, chart type, indicators and whether the drawings are hidden.
 */
type Tools = ReturnType<typeof useDrawingTools>;

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const iconBtn = cx("inline-flex min-h-11 min-w-11 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg disabled:opacity-40 disabled:hover:bg-transparent md:min-h-9 md:min-w-9", focus);

/** The market sessions (the app's one shared status read, every minute; UI2-4e) and the server's timeframes (read
 * once; a failed read offers the timeframes every server has, so the page never waits on it). */
function useChartsContext() {
  const { status, at } = useMarketStatus();
  const [timeframes, setTimeframes] = useState<string[] | null>(null);
  useEffect(() => {
    let live = true;
    api.marketDataSources().then((s) => { if (live) setTimeframes(Array.isArray(s.timeframes) ? s.timeframes : []); }).catch(() => { if (live) setTimeframes([]); });
    return () => { live = false; };
  }, []);
  return { status, timeframes, now: at || Date.now() };
}

export default function ChartsPageV2(_props: PageProps) {
  const { symbol: fromPath } = useParams<{ symbol?: string }>();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const features = useFeatures();
  const symbol = chartsSymbol(fromPath?.trim() ? fromPath : null, readLastSymbol(userId));
  const exchange = (params.get("exchange") ?? "NSE").trim().toUpperCase() || "NSE";
  const multi = features.on("chart_workspace");
  const watchOn = features.on("watchlists");
  const wide = useMediaQuery("(min-width: 1024px)");
  const phone = useMediaQuery(PHONE_QUERY);
  const [sheet, setSheet] = useState(false);
  const tab = multi && params.get("tab") === "multi" ? "multi" : "chart";
  useEffect(() => { saveLastSymbol(userId, symbol); }, [userId, symbol]);

  const { status, timeframes, now } = useChartsContext();
  const offered = useMemo(() => offeredTimeframes(timeframes), [timeframes]);
  const [view, setViewState] = useState<ChartsView>(() => readView(userId));
  // Another user without a remount: that user's own view, so nothing is saved under the wrong key.
  const [viewUser, setViewUser] = useState(userId);
  if (viewUser !== userId) { setViewUser(userId); setViewState(readView(userId)); }
  const setView = useCallback((patch: Partial<ChartsView>) => setViewState((v) => {
    const next = { ...v, ...patch };
    saveView(userId, next);
    return next;
  }), [userId]);
  // UI2-4e: a timeframe in the address (an embedded chart's "Open in Chart tab", an old chart window link) is taken
  // before anything is read, then leaves the address.
  const fromAddress = tab === "chart" ? addressTimeframe(params) : null;
  if (fromAddress && fromAddress !== view.timeframe) setView({ timeframe: fromAddress });
  useEffect(() => {
    if (fromAddress) setParams((prev) => { const next = new URLSearchParams(prev); next.delete("tf"); return next; }, { replace: true });
  }, [fromAddress, setParams]);
  // A stored week or month waits for the server's timeframes (it may not offer them) instead of loading 5m first.
  const waiting = timeframes === null && (view.timeframe === "week" || view.timeframe === "month");
  const timeframe: TimeframeId = waiting || offered.includes(view.timeframe) ? view.timeframe : DEFAULT_TIMEFRAME;
  const chip = marketChip(status, exchange, now);

  const prefs = useChartPrefs(userId);
  // While the Multi-chart tab shows, the Chart tab keeps its bars and reads nothing (one refresh when it shows again).
  const data = useChartCandles({ enabled: !waiting, paused: tab !== "chart", symbol, exchange, timeframe });
  const last = data.candles[data.candles.length - 1];
  // The live price moves the newest bar: intraday while the market is open (or its state is not known), day / week /
  // month only while the server marks that bar as forming.
  const liveOn = DAILY_OR_LONGER.has(timeframe) ? !!last?.forming : chip?.open !== false;
  const live = useLiveLtp(liveOn && data.candles.length > 0 && tab === "chart", symbol, exchange);

  const [engine, setEngine] = useState<LightweightEngine | null>(null);
  const [tools, setTools] = useState<Tools | null>(null);
  const tradesNote = useTradesLayer(engine, `${exchange}:${symbol}`, data.candles, view.layers.includes("trades") && features.on("chart_trades_layer"));

  // CHP-2: a tool picked and not yet placed stays picked when the symbol (and so the drawing controller) changes.
  const armed = useRef<{ kind: DrawingKind; text?: string; ctl: DrawingController } | null>(null);
  useEffect(() => {
    const ctl = tools?.controller;
    if (view.drawings_hidden) { armed.current = null; return; }          // Hide all puts the tools down
    if (!ctl || !tools?.view) return;
    const last = armed.current;
    if (last && last.ctl !== ctl) {
      armed.current = { ...last, ctl };
      if (!ctl.armed()) ctl.setTool(last.kind, last.text);
      return;
    }
    const now = ctl.armed();
    armed.current = now ? { ...now, ctl } : null;
  }, [tools, view.drawings_hidden]);

  const root = useRef<HTMLDivElement>(null);
  const busy = useCallback(() => !!tools?.controller?.view().tool || !!root.current?.querySelector("[role='dialog'], [role='alertdialog'], [role='listbox']"), [tools]);
  const fs = useFullscreen(root, busy, tab === "chart");

  const pick = (p: PickedSymbol) => {
    const q = p.exchange && p.exchange !== "NSE" ? `?exchange=${encodeURIComponent(p.exchange)}` : "";
    if (p.symbol !== symbol || p.exchange !== exchange) navigate(`${chartsPath(p.symbol)}${q}`);
  };
  const openItem = (item: WatchItem) => { setSheet(false); pick(item); };
  const toggleWatchlist = () => (wide ? setView({ watchlist_open: !view.watchlist_open }) : setSheet(true));
  const column = watchOn && wide && view.watchlist_open;
  const watchlist = (touch: boolean, onClose?: () => void) => (
    <WatchlistPanel symbol={symbol} exchange={exchange} onOpen={openItem} listId={view.watchlist_id} onListId={(id) => setView({ watchlist_id: id })}
      storageKey={recentKeyFor(userId)} onClose={onClose} touch={touch} className="h-full" />
  );
  const setTab = (t: "chart" | "multi") => setParams((prev) => {
    const next = new URLSearchParams(prev);
    if (t === "multi") next.set("tab", "multi"); else next.delete("tab");
    return next;
  }, { replace: true });
  const indicatorState = { panes: prefs.prefs.indicators, overlays: view.overlays, layers: view.layers };
  const onIndicator = (id: string) => {
    const change = toggleIndicator(id, indicatorState);
    if ("panes" in change) prefs.update({ indicators: change.panes });
    else setView(change);
  };

  const drawingText = drawingNote(tools?.view?.message) ?? placeHint(tools?.view?.tool, phone);
  const emptyText = data.loading || waiting ? "Loading prices…" : data.error ? "" : `No prices for ${symbol} on this timeframe yet.`;
  // UI2-4d: a cell's "Open in Chart tab" - its symbol on the Chart tab, with its timeframe.
  const openChart = (sym: string, exch: string, tf: TimeframeId) => {
    setView({ timeframe: tf });
    navigate(`${chartsPath(sym)}${exch && exch !== "NSE" ? `?exchange=${encodeURIComponent(exch)}` : ""}`);
  };
  // An old workspace link (`/?workspace=1&chart=..`, redirected under v2): its symbol starts the tab, then leaves the address.
  const clearStart = useCallback(() => setParams((prev) => {
    const next = new URLSearchParams(prev);
    for (const k of START_PARAMS) next.delete(k);
    return next;
  }, { replace: true }), [setParams]);
  // Without the Multi-chart tab those parameters mean nothing here: they leave the address.
  const stray = tab === "chart" && params.has("symbol");
  useEffect(() => { if (stray) clearStart(); }, [stray, clearStart]);
  // The Multi-chart tab's shared reads, for the page's life; the tab itself stays mounted (hidden) after its first visit.
  const pool = useMemo(() => new CandlePool(api.marketDataCandles), [userId]);  // eslint-disable-line react-hooks/exhaustive-deps
  const ltpHub = useMemo(() => new LtpHub((s, e) => api.marketDataLtp(s, e)), [userId]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => ltpHub.dispose(), [ltpHub]);
  const [multiSeen, setMultiSeen] = useState(tab === "multi");
  if (tab === "multi" && !multiSeen) setMultiSeen(true);

  return (
    <div ref={root} data-page="charts-v2" data-fullscreen={fs.on ? "on" : "off"}
      className={cx("flex flex-col gap-space-3", fs.on && "h-full bg-surface p-space-2 md:p-space-3", fs.fallback && "fixed inset-0 z-[60] overflow-y-auto")}>
      {!fs.on && <PageIntro id="charts" lines={[
        "See what a symbol is doing: candles, volume and the live price from your broker.",
        "Search a symbol at the top left. Scroll the chart left for older history; press F for full screen.",
      ]} />}

      {multi && !fs.on && (
        <div role="tablist" aria-label="Charts" className="flex gap-space-1 rounded-panel bg-surface-2 p-space-1 sm:w-fit">
          {(["chart", "multi"] as const).map((t) => (
            <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
              className={cx("min-h-11 flex-1 rounded-control px-space-4 text-t14 sm:flex-none md:min-h-9", focus,
                tab === t ? "bg-surface-1 font-semibold text-fg shadow-card" : "text-fg-muted hover:text-fg")}>
              {t === "chart" ? "Chart" : "Multi-chart"}
            </button>
          ))}
        </div>
      )}

      {tab === "chart" && (
        <div className={cx("flex min-h-0 gap-space-3", fs.on && "flex-1")}>
          <section aria-label={`Chart of ${symbol}`} className="flex min-h-0 min-w-0 flex-1 flex-col rounded-panel border border-subtle bg-surface-1">
            <div role="toolbar" aria-label="Chart controls" data-chart-toolbar data-tour="charts-toolbar"
              className="relative flex flex-wrap items-center gap-x-space-2 gap-y-space-1 border-b border-subtle p-space-2">
              {/* Phone: symbol and the icon buttons, then market state, type and Indicators, then the timeframes. */}
              <SymbolPicker value={symbol} exchange={exchange} onPick={pick} storageKey={recentKeyFor(userId)} className="order-1 min-w-[8.5rem] flex-1 md:order-none md:w-44 md:flex-none" />
              {chip && (
                <span data-market={chip.open ? "open" : "closed"} className="order-3 inline-flex items-center gap-space-1 whitespace-nowrap rounded-pill border border-subtle px-space-2 py-space-1 text-t12 text-fg-muted md:order-none">
                  <span aria-hidden className={cx("h-2 w-2 rounded-pill", chip.open ? "bg-up" : "bg-fg-muted")} />
                  {chip.text}
                </span>
              )}
              <div role="group" aria-label="Timeframe" data-tour="charts-timeframes" className="order-6 flex w-full gap-space-1 overflow-x-auto md:order-none md:w-auto">
                {offered.map((tf) => (
                  <button key={tf} type="button" aria-pressed={tf === timeframe} onClick={() => setView({ timeframe: tf })}
                    className={cx("min-h-11 min-w-11 shrink-0 rounded-control px-space-2 text-t13 md:min-h-9 md:min-w-9", focus,
                      tf === timeframe ? "bg-surface-3 font-semibold text-fg" : "text-fg-muted hover:bg-surface-2 hover:text-fg")}>
                    {timeframeLabel(tf)}
                  </button>
                ))}
              </div>
              <label className="sr-only" htmlFor="charts-type">Chart type</label>
              <select id="charts-type" value={view.type} onChange={(e) => setView({ type: e.target.value as ChartTypeV2 })}
                className={cx("order-4 h-11 w-[6.5rem] rounded-control border border-border bg-surface-2 px-space-2 text-t13 text-fg md:order-none md:h-9 md:w-auto", focus)}>
                {CHART_TYPES_V2.map((t) => <option key={t} value={t}>{CHART_TYPE_LABELS_V2[t]}</option>)}
              </select>
              <div className="order-5 md:order-none" data-tour="charts-indicators"><IndicatorsMenu state={indicatorState} on={features.on} onToggle={onIndicator} /></div>
              <span className="order-2 ml-auto inline-flex items-center md:order-none">
                <DrawingMenu tools={tools} symbol={symbol} hidden={view.drawings_hidden} onHidden={(h) => setView({ drawings_hidden: h })} />
                <button type="button" aria-label="Undo drawing" title="Undo (Ctrl+Z)" className={iconBtn}
                  disabled={!tools?.view?.canUndo} onClick={() => void tools?.controller?.undo()}><Undo2 size={16} aria-hidden /></button>
                <button type="button" aria-label="Redo drawing" title="Redo (Ctrl+Y)" className={iconBtn}
                  disabled={!tools?.view?.canRedo} onClick={() => void tools?.controller?.redo()}><Redo2 size={16} aria-hidden /></button>
                {/* Analyze a chart (flag chart_analyze, off by default): a short way from the chart to the reading page. */}
                {features.on("chart_analyze") && !fs.on && (
                  <Link to={pathFor("chart-analyze")} aria-label="Analyze a chart" title="Analyze a chart" className={iconBtn}>
                    <ScanSearch size={16} aria-hidden />
                  </Link>
                )}
                <ChartSettingsPopover prefs={prefs.prefs} onChange={prefs.update} error={prefs.error} />
                <button type="button" aria-label={fs.on ? "Leave full screen" : "Full screen"} title={fs.on ? "Leave full screen (Esc)" : "Full screen (F)"}
                  aria-pressed={fs.on} className={iconBtn} onClick={fs.toggle}>
                  {fs.on ? <Minimize2 size={16} aria-hidden /> : <Maximize2 size={16} aria-hidden />}
                </button>
                {/* The sheet is laid over the document, which full screen does not show: no sheet button in full screen. */}
                {watchOn && (wide || !fs.on) && (
                  <button type="button" aria-label="Watchlist" title={wide ? (view.watchlist_open ? "Hide the watchlist" : "Show the watchlist") : "Open the watchlist"}
                    aria-pressed={wide ? view.watchlist_open : undefined} aria-haspopup={wide ? undefined : "dialog"} className={iconBtn} onClick={toggleWatchlist}>
                    <ListTree size={16} aria-hidden />
                  </button>
                )}
              </span>
            </div>
            <div data-tour="charts-chart" className={cx("flex min-h-0", fs.on && "flex-1")}>
              <DrawingRail tools={tools} symbol={symbol} hidden={view.drawings_hidden} onHidden={(h) => setView({ drawings_hidden: h })} />
              <ChartV2 candles={data.candles} timeframe={timeframe} symbol={symbol} exchange={exchange} chartType={view.type}
                prefs={prefs.prefs} overlays={view.overlays} live={live.ltp} marketOpen={chip ? chip.open : null} onLoadOlder={data.loadOlder} drawingTools={!view.drawings_hidden}
                volumeLabel={volumeLabelFor(data.volumeSource)} emptyText={emptyText} onEngine={setEngine} onTools={setTools}
                className={cx("min-w-0 flex-1", fs.on ? "min-h-[240px]" : "h-[60vh] min-h-[320px] md:h-[calc(100vh-17rem)] md:min-h-[420px]")} />
            </div>
            {(data.error || tradesNote || data.loadingOlder || drawingText) && (
              <div role="status" className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1 border-t border-subtle px-space-3 py-space-2 text-t13 text-fg-muted">
                {data.error && <span className="text-fg">{data.error}</span>}
                {data.error && <button type="button" onClick={data.retry} className={cx("min-h-11 font-semibold text-brand hover:underline md:min-h-9", focus)}>Try again</button>}
                {data.loadingOlder && <span>Loading older history…</span>}
                {tradesNote && <span>{tradesNote}</span>}
                {drawingText && <span>{drawingText}</span>}
              </div>
            )}
          </section>
          {column && (
            // As tall as the chart's card (the inner box takes no height of its own); its rows scroll inside.
            <aside aria-label="Watchlist column" className="relative w-[300px] shrink-0 rounded-panel border border-subtle bg-surface-1">
              <div className="absolute inset-0 flex flex-col">{watchlist(false, () => setView({ watchlist_open: false }))}</div>
            </aside>
          )}
        </div>
      )}
      {multi && multiSeen && (
        <MultiChartV2 key={String(userId)} shown={tab === "multi"} pool={pool} ltpHub={ltpHub}
          symbol={symbol} exchange={exchange} userId={userId} offered={offered} timeframesKnown={timeframes !== null}
          prefs={prefs.prefs} drawingsHidden={view.drawings_hidden} marketOpen={(ex) => marketChip(status, ex, now)?.open ?? null}
          start={startChartFrom(params)} onStarted={clearStart} onOpenChart={openChart} />
      )}
      {watchOn && !wide && (
        <Sheet side="bottom" open={sheet} onOpenChange={setSheet} title="Watchlist">
          {watchlist(true)}
        </Sheet>
      )}
    </div>
  );
}
