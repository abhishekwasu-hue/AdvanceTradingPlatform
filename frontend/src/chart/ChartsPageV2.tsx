import { ExternalLink, Maximize2, Minimize2, Redo2, Undo2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { useDrawingTools } from "../charting/DrawingToolbar";
import type { LightweightEngine } from "../charting/lightweight";
import { useLiveLtp } from "../components/chartHelpers";
import { cx } from "../components/primitives/cx";
import type { PageProps } from "../routes";
import { chartsPath } from "../shell/shellRoutes";
import type { SystemStatus } from "../types";
import { PageIntro } from "../ui/PageIntro";
import { SymbolPicker, recentKeyFor, type PickedSymbol } from "../ui/SymbolPicker";
import { useFeatures } from "../ui/useFeatures";
import { ChartSettingsPopover } from "./ChartSettingsPopover";
import ChartV2 from "./ChartV2";
import { CHART_TYPES_V2, CHART_TYPE_LABELS_V2, type ChartTypeV2 } from "./chartCore";
import {
  chartsSymbol, drawingNote, marketChip, offeredTimeframes, readLastSymbol, readView, saveLastSymbol, saveView, volumeLabelFor,
  type ChartsView,
} from "./chartsPage";
import { IndicatorsMenu } from "./IndicatorsMenu";
import { toggle as toggleIndicator } from "./indicatorsModel";
import { DAILY_OR_LONGER, DEFAULT_TIMEFRAME, timeframeLabel, type TimeframeId } from "./timeframes";
import { useChartCandles } from "./useChartCandles";
import { useChartPrefs } from "./useChartPrefs";
import { useFullscreen } from "./useFullscreen";
import { useTradesLayer } from "./useTradesLayer";

/**
 * UI2-4b2 (UI v2 proposal §6, Charts page plan §3): the Charts page at `/charts` and `/charts/:symbol`, v2 only.
 * - Tab "Chart" (default): one large v2 chart (ChartV2) and one top bar - symbol, market state, timeframe, chart type,
 *   Indicators, undo / redo for drawings, chart settings, full screen (button, F, Escape).
 * - Tab "Multi-chart" (only while `chart_workspace` is on): the existing workspace, in its own tab, until UI2-4d.
 * The v2 shell puts the broker gate in front of this page (src/ui/dataPages.ts): signed out, with no broker or before
 * the day's broker login the trader sees the gate card, never candles or sample data. Candles, the live price and the
 * drawings all come from the trader's own broker session and account.
 */
type Tools = ReturnType<typeof useDrawingTools>;

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const iconBtn = cx("inline-flex min-h-11 min-w-11 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg disabled:opacity-40 disabled:hover:bg-transparent md:min-h-9 md:min-w-9", focus);

/** The market sessions (public status, re-read every minute) and the server's timeframes (read once; a failed read
 * offers the timeframes every server has, so the page never waits on it). */
function useChartsContext() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [timeframes, setTimeframes] = useState<string[] | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    let live = true;
    const load = () => api.systemStatus().then((s) => { if (live) { setStatus(s); setNow(Date.now()); } }).catch(() => undefined);
    void load();
    api.marketDataSources().then((s) => { if (live) setTimeframes(Array.isArray(s.timeframes) ? s.timeframes : []); }).catch(() => { if (live) setTimeframes([]); });
    const id = window.setInterval(load, 60_000);
    return () => { live = false; window.clearInterval(id); };
  }, []);
  return { status, timeframes, now };
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
  // A stored week or month waits for the server's timeframes (it may not offer them) instead of loading 5m first.
  const waiting = timeframes === null && (view.timeframe === "week" || view.timeframe === "month");
  const timeframe: TimeframeId = waiting || offered.includes(view.timeframe) ? view.timeframe : DEFAULT_TIMEFRAME;
  const chip = marketChip(status, exchange, now);

  const prefs = useChartPrefs(userId);
  const data = useChartCandles({ enabled: !waiting, symbol, exchange, timeframe });
  const last = data.candles[data.candles.length - 1];
  // The live price moves the newest bar: intraday while the market is open (or its state is not known), day / week /
  // month only while the server marks that bar as forming.
  const liveOn = DAILY_OR_LONGER.has(timeframe) ? !!last?.forming : chip?.open !== false;
  const live = useLiveLtp(liveOn && data.candles.length > 0, symbol, exchange);

  const [engine, setEngine] = useState<LightweightEngine | null>(null);
  const [tools, setTools] = useState<Tools | null>(null);
  const tradesNote = useTradesLayer(engine, `${exchange}:${symbol}`, data.candles, view.layers.includes("trades") && features.on("chart_trades_layer"));

  const root = useRef<HTMLDivElement>(null);
  const busy = useCallback(() => !!tools?.controller?.view().tool || !!root.current?.querySelector("[role='dialog'], [role='listbox']"), [tools]);
  const fs = useFullscreen(root, busy, tab === "chart");

  const pick = (p: PickedSymbol) => {
    const q = p.exchange && p.exchange !== "NSE" ? `?exchange=${encodeURIComponent(p.exchange)}` : "";
    if (p.symbol !== symbol || p.exchange !== exchange) navigate(`${chartsPath(p.symbol)}${q}`);
  };
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

  const drawingText = drawingNote(tools?.view?.message);
  const emptyText = data.loading || waiting ? "Loading prices…" : data.error ? "" : `No prices for ${symbol} on this timeframe yet.`;
  const workspace = `/?${new URLSearchParams({ workspace: "1", chart: symbol, exchange }).toString()}`;

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

      {tab === "multi" ? (
        <section className="space-y-space-3 rounded-panel border border-subtle bg-surface-1 p-space-5 text-t14 text-fg">
          <h2 className="text-t15 font-semibold">Several charts side by side</h2>
          <p className="text-fg-muted">The multi-chart workspace shows up to eight charts with linked symbols and timeframes. It opens in a new tab with {symbol}.</p>
          <a href={workspace} target="_blank" rel="noopener noreferrer"
            className={cx("inline-flex min-h-11 items-center gap-space-1 rounded-control bg-brand px-space-4 font-semibold text-on-brand hover:bg-brand-strong md:min-h-9", focus)}>
            Open the multi-chart workspace <ExternalLink size={14} aria-hidden />
          </a>
        </section>
      ) : (
        <section aria-label={`Chart of ${symbol}`} className={cx("flex min-h-0 flex-col rounded-panel border border-subtle bg-surface-1", fs.on && "flex-1")}>
          <div role="toolbar" aria-label="Chart controls" data-chart-toolbar
            className="flex flex-wrap items-center gap-x-space-2 gap-y-space-1 border-b border-subtle p-space-2">
            {/* Phone: symbol and the icon buttons, then market state, type and Indicators, then the timeframes. */}
            <SymbolPicker value={symbol} exchange={exchange} onPick={pick} storageKey={recentKeyFor(userId)} className="order-1 min-w-[8.5rem] flex-1 md:order-none md:w-44 md:flex-none" />
            {chip && (
              <span data-market={chip.open ? "open" : "closed"} className="order-3 inline-flex items-center gap-space-1 whitespace-nowrap rounded-pill border border-subtle px-space-2 py-space-1 text-t12 text-fg-muted md:order-none">
                <span aria-hidden className={cx("h-2 w-2 rounded-pill", chip.open ? "bg-up" : "bg-fg-muted")} />
                {chip.text}
              </span>
            )}
            <div role="group" aria-label="Timeframe" className="order-6 flex w-full gap-space-1 overflow-x-auto md:order-none md:w-auto">
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
            <div className="order-5 md:order-none"><IndicatorsMenu state={indicatorState} on={features.on} onToggle={onIndicator} /></div>
            <span className="order-2 ml-auto inline-flex items-center md:order-none">
              <button type="button" aria-label="Undo drawing" title="Undo (Ctrl+Z)" className={iconBtn}
                disabled={!tools?.view?.canUndo} onClick={() => void tools?.controller?.undo()}><Undo2 size={16} aria-hidden /></button>
              <button type="button" aria-label="Redo drawing" title="Redo (Ctrl+Y)" className={iconBtn}
                disabled={!tools?.view?.canRedo} onClick={() => void tools?.controller?.redo()}><Redo2 size={16} aria-hidden /></button>
              <ChartSettingsPopover prefs={prefs.prefs} onChange={prefs.update} error={prefs.error} />
              <button type="button" aria-label={fs.on ? "Leave full screen" : "Full screen"} title={fs.on ? "Leave full screen (Esc)" : "Full screen (F)"}
                aria-pressed={fs.on} className={iconBtn} onClick={fs.toggle}>
                {fs.on ? <Minimize2 size={16} aria-hidden /> : <Maximize2 size={16} aria-hidden />}
              </button>
            </span>
          </div>
          <ChartV2 candles={data.candles} timeframe={timeframe} symbol={symbol} exchange={exchange} chartType={view.type}
            prefs={prefs.prefs} overlays={view.overlays} live={live.ltp} onLoadOlder={data.loadOlder}
            volumeLabel={volumeLabelFor(data.volumeSource)} emptyText={emptyText} onEngine={setEngine} onTools={setTools}
            className={fs.on ? "min-h-[240px] flex-1" : "h-[60vh] min-h-[320px] md:h-[calc(100vh-17rem)] md:min-h-[420px]"} />
          {(data.error || tradesNote || data.loadingOlder || drawingText) && (
            <div role="status" className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1 border-t border-subtle px-space-3 py-space-2 text-t13 text-fg-muted">
              {data.error && <span className="text-fg">{data.error}</span>}
              {data.error && <button type="button" onClick={data.retry} className={cx("min-h-11 font-semibold text-brand hover:underline md:min-h-9", focus)}>Try again</button>}
              {data.loadingOlder && <span>Loading older bars…</span>}
              {tradesNote && <span>{tradesNote}</span>}
              {drawingText && <span>{drawingText}</span>}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
