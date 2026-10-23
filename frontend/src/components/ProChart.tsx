import {
  AreaSeries, CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart, createSeriesMarkers,
  type IChartApi, type IPriceLine, type ISeriesApi, type ISeriesMarkersPluginApi, type LogicalRange, type MouseEventParams, type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { ExternalLink, Layers, Maximize2, Minimize2, Radio, Scan, SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { LtpResponse, OHLCVBar, SRZone } from "../types";
import {
  DEFAULT_SETTINGS, INDICATOR_LABELS, OVERLAYS, PANES, adx, applyLivePrice, bollinger, closes, ema, indicatorsForStrategy, rsi, sma,
  supertrend, vwap, type IndicatorId, type IndicatorSettings, type Series,
} from "../utils/indicators";
import type { ChartMarker, PriceLineSpec } from "../charting/markers";
import { CHART_FONT_FAMILY, baseChartOptions } from "../chart/chartOptions";
import { THEME_EVENT, chartColors, chartFontSize, resolveChartColor } from "../theme";
import { useChartStrategies } from "./ChartStrategies";
import type { ChartEngine } from "../charting/engine";
import { LightweightEngine } from "../charting/lightweight";
import { DrawingToolbar, useDrawingTools } from "../charting/DrawingToolbar";
import { CHART_TYPE_LABELS, brickAt, chartView, formatTick, periodSeconds, sessionBreaksCache, venueZone } from "../charting/chartTypes";
import { SessionBreaksPrimitive } from "../charting/sessionBreaksPrimitive";
import type { ChartType } from "../charting/workspace";
import { Sheet } from "./primitives";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";

export type { ChartMarker, PriceLineSpec } from "../charting/markers";

export { directionMarker } from "../charting/markers";
// The light helpers live in chartHelpers (no chart engine) so pages can link to a chart without loading it.
// chartWindowUrl: the URL of the full-window chart for a broker symbol (opened in a new browser tab; see ChartWindow).
export { chartWindowUrl, useLiveLtp } from "./chartHelpers";

/**
 * Phase AN: the Pro Chart. TradingView's open-source Lightweight Charts engine (the same one the
 * plain CandleChart used; that component is gone, its marker types live in charting/markers), now with the things a trader expects from a terminal chart:
 *
 * * indicator overlays (EMA fast/slow, SMA, Bollinger, VWAP, Supertrend) and stacked panes
 *   (volume, RSI with its mid/high/low lines, ADX with ±DI and the threshold), all computed in the
 *   browser from the candles on screen with the backend's own formulas;
 * * the strategy's indicators on by default (`strategyParams`), so the chart shows what the
 *   engine decided on;
 * * a timeframe switcher, a legend that follows the crosshair, panes that scroll and zoom together;
 * * a live last price (`live`): the forming candle moves, the header shows LTP, change and age.
 *
 * Entry / stop / target price lines, support-resistance zones and trade markers are the same
 * props the old chart took, so pages swap it in one line. IST on the time axis.
 */

const IST = "Asia/Kolkata";
// Indicator line colours are fixed hues; up/down, the grid, borders and text follow the theme (P1.2: refreshThemeColors).
const COLORS = {
  up: "#22c55e", down: "#ef4444", emaFast: "#38bdf8", emaSlow: "#f59e0b", sma: "#a78bfa", bb: "#64748b", vwap: "#e879f9",
  stUp: "#22c55e", stDown: "#ef4444", rsi: "#38bdf8", adx: "#f59e0b", plusDi: "#22c55e", minusDi: "#ef4444", volUp: "rgba(34,197,94,0.45)", volDown: "rgba(239,68,68,0.45)",
  grid: "#1a2333", border: "#243044", text: "#c2cad8",
};
const ZONE_COLOR = { SUPPORT: "#22c55e", RESISTANCE: "#ef4444" };

function refreshThemeColors() {
  const c = chartColors();
  Object.assign(COLORS, { up: c.up, down: c.down, stUp: c.up, stDown: c.down, plusDi: c.up, minusDi: c.down,
                          volUp: c.upSoft, volDown: c.downSoft, grid: c.grid, border: c.border, text: c.text });
  ZONE_COLOR.SUPPORT = c.up;
  ZONE_COLOR.RESISTANCE = c.down;
}

const toTime = (iso: string): UTCTimestamp => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
const fmtIst = (t: number, withDate = true) => {
  const d = new Date(t * 1000);
  return d.toLocaleString("en-IN", { timeZone: IST, ...(withDate ? { day: "2-digit", month: "short" } : {}), hour: "2-digit", minute: "2-digit", hour12: false });
};
const fmt = (v: number | null | undefined, digits = 2) => (v == null || !Number.isFinite(v) ? "-" : v.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits }));

/** CH5-b `venueAxis`: tick labels and the crosshair time in the venue's own zone (UTC for 24x7 crypto, IST otherwise),
 * with dates on day marks; without it the axis is exactly as before (IST times). */
function axisOptions(zone: string | null) {
  if (!zone) return { tick: (t: number) => fmtIst(t, false), time: (t: number) => fmtIst(t, true) };
  return {
    tick: (t: number, type: number) => formatTick(t, type, zone),
    time: (t: number) => new Date(t * 1000).toLocaleString("en-IN", { timeZone: zone, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })
      + (zone === "UTC" ? " UTC" : ""),
  };
}

function baseOptions(height: number, showTime: boolean, attribution = true, zone: string | null = null) {
  refreshThemeColors();
  const axis = axisOptions(zone);
  // PW2: touch - pinch to zoom, one-finger drag pans the time axis while a vertical swipe still scrolls the page,
  // and a long press shows the crosshair (the library's tracking mode; the next tap hides it). The gesture, kinetic and
  // axis basics come from the one builder the v2 chart uses too (chart review R1).
  const base = baseChartOptions();
  return {
    ...base,
    height,
    // The TradingView attribution logo stays on the main pane (the library's licence asks for it once); the stacked indicator panes do not repeat it.
    layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: COLORS.text, fontFamily: CHART_FONT_FAMILY, fontSize: chartFontSize(), attributionLogo: attribution },
    grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { ...base.rightPriceScale, borderColor: COLORS.border },
    timeScale: { ...base.timeScale, visible: showTime, timeVisible: true, borderColor: COLORS.border, tickMarkFormatter: axis.tick },
    localization: { timeFormatter: axis.time },
  };
}

function lineData(times: UTCTimestamp[], s: Series, color?: (i: number) => string | undefined) {
  const out: { time: UTCTimestamp; value?: number; color?: string }[] = [];
  for (let i = 0; i < times.length; i++) {
    const v = s[i];
    if (v == null || !Number.isFinite(v)) out.push({ time: times[i] });
    else out.push({ time: times[i], value: v, ...(color ? { color: color(i) } : {}) });
  }
  return out;
}

/** Poll the backend for a symbol's last price while `enabled` and the tab is visible. */
export interface ProChartProps {
  candles: OHLCVBar[];
  symbol?: string;
  timeframe?: string;
  timeframes?: string[];
  onTimeframeChange?: (tf: string) => void;
  priceLines?: PriceLineSpec[];
  zones?: SRZone[];
  markers?: ChartMarker[];
  height?: number;
  /** The strategy's default_params: its indicators come on by default. */
  strategyParams?: Record<string, unknown> | null;
  defaultIndicators?: IndicatorId[];
  /** Last price for the forming candle and the header. */
  live?: LtpResponse | null;
  liveError?: string | null;
  /** Mini mode: no toolbar, no panes, small legend. */
  compact?: boolean;
  title?: string;
  /** Shows an "open in a new tab" button for this URL (broker charts: chartWindowUrl). */
  openUrl?: string;
  /** Fills the window and hides the expand button (the new-tab chart page). */
  fullWindow?: boolean;
  /** The chart is of a real broker symbol: the strategy panel offers Deploy (PAPER) switches. */
  deployable?: boolean;
  exchange?: string;
  /** Broker charts: called when the chart is scrolled back to its oldest bars, to load the page before them. */
  onLoadOlder?: () => void;
  /** Shown while an older page loads, and once the broker has nothing older. */
  loadingOlder?: boolean;
  olderExhausted?: boolean;
  /** CH1 (ADR-0023): the chart behind the engine-neutral `ChartEngine` interface (drawings, layers, events); null on teardown. */
  onEngine?: (engine: ChartEngine | null) => void;
  /** CH2c: the drawing toolbar (saved per user and symbol). On for full charts of a symbol; off in mini mode. */
  drawingTools?: boolean;
  /** CH5-b: candles (default), hollow candles, Heikin Ashi, Renko, range bars, line or area. Renko and range are built
   * from closed bars only and carry no indicators or volume (their bricks have their own times). */
  chartType?: ChartType;
  /** CH5-b: session breaks and the time axis in the venue's own zone (UTC, 24x7 for crypto). Off: the axis as before. */
  venueAxis?: boolean;
}

export default function ProChart({
  candles, symbol, timeframe, timeframes, onTimeframeChange, priceLines: priceLinesProp = [], zones = [], markers: markersProp = [], height = 380,
  strategyParams, defaultIndicators, live, liveError, compact: compactProp = false, title, openUrl, fullWindow = false,
  deployable = false, exchange = "NSE", onLoadOlder, loadingOlder = false, olderExhausted = false, onEngine, drawingTools = true,
  chartType = "candles", venueAxis = false,
}: ProChartProps) {
  const onLoadOlderRef = useRef(onLoadOlder);
  onLoadOlderRef.current = onLoadOlder;
  const onEngineRef = useRef(onEngine);
  onEngineRef.current = onEngine;
  // Expanded: the same chart over the whole screen, with the full toolbar and panes even if it was a mini chart.
  const [expanded, setExpanded] = useState(false);
  const phone = useMediaQuery(PHONE_QUERY);
  const [toolsOpen, setToolsOpen] = useState(false);   // PW2: the phone bottom sheet with indicators and drawing tools
  const [themeTick, setThemeTick] = useState(0);
  const [winH, setWinH] = useState(() => window.innerHeight);
  useEffect(() => {
    const onResize = () => setWinH(window.innerHeight);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setExpanded(false); };
    window.addEventListener("resize", onResize); window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("resize", onResize); window.removeEventListener("keydown", onKey); };
  }, []);
  const big = expanded || fullWindow;
  const compact = compactProp && !big;

  // Phase AO: strategies drawn on (and deployed from) the chart.
  const strat = useChartStrategies({ enabled: !compact, candles, symbol, timeframe, exchange, deployable, onTimeframeChange });
  const markers = useMemo(() => [...markersProp, ...strat.markers], [markersProp, strat.markers]);
  const priceLines = useMemo(() => [...priceLinesProp, ...strat.priceLines], [priceLinesProp, strat.priceLines]);
  useEffect(() => {
    // A strategy switched on brings its own indicators onto the chart (EMA periods, Supertrend, RSI, ADX...).
    if (strat.shownParams.length === 0) return;
    const ids = new Set<IndicatorId>();
    let merged: Partial<IndicatorSettings> = {};
    for (const params of strat.shownParams) {
      const d = indicatorsForStrategy(params);
      d.ids.forEach((i) => ids.add(i));
      // Only what this strategy changes from the defaults, so two strategies do not reset each other.
      for (const k of Object.keys(d.settings) as (keyof IndicatorSettings)[]) {
        if (d.settings[k] !== DEFAULT_SETTINGS[k]) merged = { ...merged, [k]: d.settings[k] };
      }
    }
    setActive((prev) => new Set([...prev, ...ids]));
    setSettings((prev) => ({ ...prev, ...merged }));
  }, [strat.shownParams]);
  const derived = useMemo(() => indicatorsForStrategy(strategyParams), [strategyParams]);
  const [active, setActive] = useState<Set<IndicatorId>>(() => new Set(defaultIndicators ?? derived.ids));
  const [settings, setSettings] = useState<IndicatorSettings>(() => ({ ...DEFAULT_SETTINGS, ...derived.settings }));
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => { setActive(new Set(defaultIndicators ?? derived.ids)); }, [derived, defaultIndicators]);

  const display = useMemo(() => applyLivePrice(candles, live?.ltp, live?.timestamp ?? live?.fetched_at, timeframe), [candles, live, timeframe]);
  const times = useMemo(() => display.map((c) => toTime(c.timestamp)), [display]);
  const timeIndex = useMemo(() => { const m = new Map<number, number>(); times.forEach((t, i) => m.set(t, i)); return m; }, [times]);
  // CH5-b: what the price series shows for the chart type. Renko / range use the bars as received (`candles`), closed
  // ones only; the candle types show the live-priced forming bar like before.
  // The Renko box / range size is kept per (symbol, timeframe, type) once derived, so loading older history (which moves
  // the first bar) does not change it; the bricks are still rebuilt from the new first bar (chartTypes.renko).
  const boxRef = useRef<{ key: string; size: number | null }>({ key: "", size: null });
  const view = useMemo(() => {
    const live = display.map((c, i) => ({ time: times[i] as number, open: c.open, high: c.high, low: c.low, close: c.close }));
    const synthetic = chartType === "renko" || chartType === "range";
    const source = synthetic
      ? candles.map((c) => ({ time: toTime(c.timestamp) as number, open: c.open, high: c.high, low: c.low, close: c.close })) : live;
    const boxKey = `${symbol ?? ""}|${timeframe ?? ""}|${chartType}`;
    if (boxRef.current.key !== boxKey) boxRef.current = { key: boxKey, size: null };
    const v = chartView(chartType, live, source, { periodSec: periodSeconds(timeframe), nowSec: Date.now() / 1000, size: boxRef.current.size,
                                                   up: COLORS.up, down: COLORS.down });
    if (synthetic && boxRef.current.size == null && v.size != null) boxRef.current = { key: boxKey, size: v.size };
    return v;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [display, times, candles, chartType, timeframe, symbol, themeTick]);
  // The times the price series is keyed by: the source bars', or the bricks' own on Renko / range. Every lookup by a
  // chart time (the view shift on reload, the legend, markers, session breaks) goes through these, never `times`.
  const rowTimes = useMemo(() => (view.synthetic ? view.bars.map((b) => b.time as UTCTimestamp) : times), [view, times]);
  const rowIndex = useMemo(() => {
    if (!view.synthetic) return timeIndex;
    const m = new Map<number, number>(); rowTimes.forEach((t, i) => m.set(t, i)); return m;
  }, [view.synthetic, rowTimes, timeIndex]);
  const breaksCache = useRef(sessionBreaksCache());
  const breaks = useMemo(() => breaksCache.current(rowTimes, exchange, periodSeconds(timeframe)), [rowTimes, exchange, timeframe]);

  const ind = useMemo(() => {
    const cl = closes(display);
    const has = (id: IndicatorId) => active.has(id);
    return {
      emaFast: has("ema_fast") ? ema(cl, settings.emaFast) : null,
      emaSlow: has("ema_slow") ? ema(cl, settings.emaSlow) : null,
      sma: has("sma") ? sma(cl, settings.smaPeriod) : null,
      bb: has("bollinger") ? bollinger(cl, settings.bbPeriod, settings.bbK) : null,
      vwap: has("vwap") ? vwap(display) : null,
      st: has("supertrend") ? supertrend(display, settings.stPeriod, settings.stMult) : null,
      rsi: has("rsi") && !compact ? rsi(cl, settings.rsiPeriod) : null,
      adx: has("adx") && !compact ? adx(display, settings.adxPeriod) : null,
    };
  }, [display, active, settings, compact]);

  const showVolume = active.has("volume") && !compact;
  const showRsi = !!ind.rsi;
  const showAdx = !!ind.adx;
  const paneCount = (showVolume ? 1 : 0) + (showRsi ? 1 : 0) + (showAdx ? 1 : 0);
  const chartHeight = big ? Math.max(height, winH - paneCount * 90 - (fullWindow ? 170 : 130)) : height;

  const mainRef = useRef<HTMLDivElement>(null);
  const [toolEngine, setToolEngine] = useState<LightweightEngine | null>(null);
  const tools = useDrawingTools(toolEngine, mainRef.current, symbol, exchange, drawingTools && !compact);
  const volRef = useRef<HTMLDivElement>(null);
  const rsiRef = useRef<HTMLDivElement>(null);
  const adxRef = useRef<HTMLDivElement>(null);
  const charts = useRef<{ main?: IChartApi; vol?: IChartApi; rsi?: IChartApi; adx?: IChartApi }>({});
  const series = useRef<Record<string, ISeriesApi<"Candlestick" | "Line" | "Histogram">>>({});
  const markersPlugin = useRef<ISeriesMarkersPluginApi<Time> | null>(null);   // CH2: v5 markers are a series plugin
  const breaksRef = useRef<SessionBreaksPrimitive | null>(null);              // CH5-b: session breaks (venueAxis)
  const priceLineRefs = useRef<IPriceLine[]>([]);
  const dataKey = useRef<string>("");
  const lastShape = useRef<{ len: number; lastTime: number }>({ len: 0, lastTime: 0 });
  const prevFirstTime = useRef<number>(0);
  const syncing = useRef(false);

  // P1.2: a theme / colour-blind change repaints the existing charts.
  useEffect(() => {
    const repaint = () => {
      refreshThemeColors();
      for (const chart of Object.values(charts.current)) {
        chart?.applyOptions({ layout: { textColor: COLORS.text, fontSize: chartFontSize() }, grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
                              rightPriceScale: { borderColor: COLORS.border }, timeScale: { borderColor: COLORS.border } });
      }
      series.current.candles?.applyOptions({ upColor: COLORS.up, downColor: COLORS.down, wickUpColor: COLORS.up, wickDownColor: COLORS.down });
      series.current.plusDi?.applyOptions({ color: COLORS.plusDi });
      series.current.minusDi?.applyOptions({ color: COLORS.minusDi });
      setThemeTick((t) => t + 1);           // redraws volume, Supertrend, price lines, zones and markers
    };
    window.addEventListener(THEME_EVENT, repaint);
    return () => window.removeEventListener(THEME_EVENT, repaint);
  }, []);

  // Build the charts once per pane layout.
  useEffect(() => {
    const main = mainRef.current;
    if (!main) return;
    const made: IChartApi[] = [];
    const mk = (el: HTMLDivElement | null, h: number, showTime: boolean, attribution = false) => {
      if (!el) return undefined;
      const c = createChart(el, baseOptions(h, showTime, attribution, venueAxis ? venueZone(exchange) : null));
      made.push(c);
      return c;
    };
    const paneH = compact ? 0 : 90;
    const mainChart = mk(main, chartHeight, !(showVolume || showRsi || showAdx), true) as IChartApi;
    const volChart = showVolume ? mk(volRef.current, paneH, !(showRsi || showAdx)) : undefined;
    const rsiChart = showRsi ? mk(rsiRef.current, paneH, !showAdx) : undefined;
    const adxChart = showAdx ? mk(adxRef.current, paneH, true) : undefined;
    charts.current = { main: mainChart, vol: volChart, rsi: rsiChart, adx: adxChart };

    const s: Record<string, ISeriesApi<"Candlestick" | "Line" | "Histogram">> = {};
    s.candles = mainChart.addSeries(CandlestickSeries, { upColor: COLORS.up, downColor: COLORS.down, borderVisible: false, wickUpColor: COLORS.up, wickDownColor: COLORS.down });
    const line = (chart: IChartApi, color: string, width: 1 | 2 = 1, style = LineStyle.Solid) =>
      chart.addSeries(LineSeries, { color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    s.emaFast = line(mainChart, COLORS.emaFast, 2); s.emaSlow = line(mainChart, COLORS.emaSlow, 2); s.sma = line(mainChart, COLORS.sma, 1);
    s.bbUpper = line(mainChart, COLORS.bb, 1, LineStyle.Dotted); s.bbLower = line(mainChart, COLORS.bb, 1, LineStyle.Dotted); s.bbMid = line(mainChart, COLORS.bb, 1, LineStyle.Dashed);
    s.vwap = line(mainChart, COLORS.vwap, 1, LineStyle.Dashed); s.st = line(mainChart, COLORS.stUp, 2);
    if (volChart) s.vol = volChart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
    if (rsiChart) {
      s.rsi = line(rsiChart, COLORS.rsi, 2);
      s.rsi.createPriceLine({ price: settings.rsiHigh, color: "#ef444480", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" });
      s.rsi.createPriceLine({ price: settings.rsiMid, color: "#64748b80", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" });
      s.rsi.createPriceLine({ price: settings.rsiLow, color: "#22c55e80", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" });
    }
    if (adxChart) {
      s.adx = line(adxChart, COLORS.adx, 2); s.plusDi = line(adxChart, COLORS.plusDi, 1); s.minusDi = line(adxChart, COLORS.minusDi, 1);
      s.adx.createPriceLine({ price: settings.adxMin, color: "#64748b80", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" });
    }
    series.current = s;
    markersPlugin.current = createSeriesMarkers(s.candles as ISeriesApi<"Candlestick">, []);
    // CH5-b: line / area chart types draw the closes on their own series (empty otherwise); the candle series stays (made
    // transparent) because drawings, layers and markers are anchored to it.
    s.typeLine = mainChart.addSeries(LineSeries, { color: COLORS.emaFast, lineWidth: 2, priceLineVisible: true, lastValueVisible: true });
    s.typeArea = mainChart.addSeries(AreaSeries, { lineColor: COLORS.emaFast, topColor: "rgba(56,189,248,0.35)", bottomColor: "rgba(56,189,248,0.02)", lineWidth: 2 }) as never;
    if (venueAxis) {
      breaksRef.current = new SessionBreaksPrimitive([]);
      (s.candles as ISeriesApi<"Candlestick">).attachPrimitive(breaksRef.current);
    }
    dataKey.current = "";
    // CH1: the same chart behind the ChartEngine interface - ProChart keeps drawing everything it drew before.
    const engine = new LightweightEngine(mainChart, s.candles as ISeriesApi<"Candlestick">,
      (y) => (s.candles as ISeriesApi<"Candlestick">).coordinateToPrice(y),
      // N4 CH6: study lines from the server, on the price pane (0) or the study pane under it
      { add: (pane, color, title) => mainChart.addSeries(LineSeries, { color, lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title,
                                                                      crosshairMarkerVisible: false }, pane) as unknown as { setData(d: { time: number; value: number }[]): void },
        remove: (line) => mainChart.removeSeries(line as unknown as ISeriesApi<"Line">) });
    onEngineRef.current?.(engine);
    setToolEngine(engine);

    // Scroll/zoom together.
    const all = made;
    const unsubs: (() => void)[] = [];
    for (const src of all) {
      const handler = (range: LogicalRange | null) => {
        if (!range || syncing.current) return;
        syncing.current = true;
        for (const other of all) if (other !== src) other.timeScale().setVisibleLogicalRange(range);
        syncing.current = false;
      };
      src.timeScale().subscribeVisibleLogicalRangeChange(handler);
      unsubs.push(() => src.timeScale().unsubscribeVisibleLogicalRangeChange(handler));
    }
    // Scrolled back to the oldest bars: ask for the page before them (broker charts).
    const nearStart = (range: LogicalRange | null) => {
      if (range && range.from < 10 && onLoadOlderRef.current) onLoadOlderRef.current();
    };
    mainChart.timeScale().subscribeVisibleLogicalRangeChange(nearStart);
    unsubs.push(() => mainChart.timeScale().unsubscribeVisibleLogicalRangeChange(nearStart));
    // Crosshair follows across panes and feeds the legend.
    for (const src of all) {
      const handler = (p: MouseEventParams) => {
        const t = p.time as number | undefined;
        if (t == null) { setHover(null); for (const other of all) if (other !== src) other.clearCrosshairPosition(); return; }
        setHover(t);
        for (const other of all) {
          if (other === src) continue;
          const target = other === mainChart ? s.candles : other === volChart ? s.vol : other === rsiChart ? s.rsi : s.adx;
          if (target) other.setCrosshairPosition(0, t as UTCTimestamp, target);
        }
      };
      src.subscribeCrosshairMove(handler);
      unsubs.push(() => src.unsubscribeCrosshairMove(handler));
    }

    const onResize = () => { const w = main.clientWidth; for (const c of all) c.applyOptions({ width: w }); };
    window.addEventListener("resize", onResize);
    onResize();
    return () => {
      window.removeEventListener("resize", onResize);
      for (const u of unsubs) u();
      setToolEngine(null);
      engine.dispose();
      onEngineRef.current?.(null);
      for (const c of all) c.remove();
      charts.current = {}; series.current = {}; priceLineRefs.current = []; markersPlugin.current = null; breaksRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartHeight, compact, showVolume, showRsi, showAdx, settings.rsiHigh, settings.rsiMid, settings.rsiLow, settings.adxMin, venueAxis, exchange]);

  // Feed data. A fresh symbol/length reset calls setData + fitContent; a live move updates the last bar only.
  useEffect(() => {
    const s = series.current; const main = charts.current.main;
    if (!s.candles || !main) return;
    if (display.length === 0) { dataKey.current = ""; lastShape.current = { len: 0, lastTime: 0 }; return; }
    const chartKey = `${symbol ?? ""}|${timeframe ?? ""}|${chartType}`;
    const key = `${display[0].timestamp}|${chartKey}`;
    // CH5-b: the chart type's bars (the plain candles, Heikin Ashi, hollow colours, or Renko / range bricks).
    const candleData = view.bars.map((b) => ({ ...b, time: b.time as UTCTimestamp }));
    const transparent = "rgba(0,0,0,0)";
    s.candles.applyOptions(view.line
      ? { upColor: transparent, downColor: transparent, wickUpColor: transparent, wickDownColor: transparent, borderVisible: false }
      : { upColor: COLORS.up, downColor: COLORS.down, wickUpColor: COLORS.up, wickDownColor: COLORS.down, borderVisible: chartType === "hollow" });
    const closeLine = view.line ? candleData.map((b) => ({ time: b.time, value: b.close })) : [];
    s.typeLine?.setData(chartType === "line" ? closeLine : []);
    s.typeArea?.setData(chartType === "area" ? closeLine : []);
    breaksRef.current?.setTimes(breaks);
    // Incremental only when this is the same series grown by at most one bar (a live tick moved
    // or opened the forming candle); anything else is a new dataset. Lightweight Charts refuses an
    // `update` older than the last bar, so bars are applied oldest first and a refusal falls back
    // to a full reload rather than taking the page down.
    const prev = lastShape.current;
    const grown = display.length - prev.len;
    const sameSeries = key === dataKey.current && (grown === 0 || grown === 1) && prev.len > 0 && times[prev.len - 1] === prev.lastTime;
    let incremental = sameSeries && !view.synthetic;            // bricks are rebuilt, never updated in place
    if (incremental) {
      try {
        if (grown === 1) s.candles.update(candleData[candleData.length - 2]);
        s.candles.update(candleData[candleData.length - 1]);
      } catch {
        incremental = false;
      }
    }
    // The same chart reloaded (older history prepended, a minute's refresh): keep the user's view,
    // shifted by the bars added on the left. A new symbol or timeframe fits all candles instead.
    const sameChart = !incremental && prev.len > 0 && dataKey.current.endsWith(`|${chartKey}`);
    const keepRange = sameChart ? main.timeScale().getVisibleLogicalRange() : null;
    const keepTimes = sameChart && view.synthetic ? main.timeScale().getVisibleRange() : null;   // bricks rebuilt on a prepend
    const prevFirst = prevFirstTime.current;
    if (!incremental) {
      s.candles.setData(candleData);
      dataKey.current = key;
    }
    prevFirstTime.current = rowTimes[0] ?? 0;
    lastShape.current = { len: display.length, lastTime: times[times.length - 1] };
    const setLine = (name: string, data: Series | null, color?: (i: number) => string | undefined) => {
      const ser = s[name]; if (!ser) return;
      ser.setData(data && !view.synthetic ? lineData(times, data, color) : []);   // no indicators on bricks
    };
    setLine("emaFast", ind.emaFast); setLine("emaSlow", ind.emaSlow); setLine("sma", ind.sma);
    setLine("bbUpper", ind.bb?.upper ?? null); setLine("bbLower", ind.bb?.lower ?? null); setLine("bbMid", ind.bb?.mid ?? null);
    setLine("vwap", ind.vwap);
    setLine("st", ind.st?.line ?? null, (i) => (ind.st?.direction[i] === -1 ? COLORS.stDown : COLORS.stUp));
    if (s.vol) s.vol.setData(view.synthetic ? [] : display.map((c, i) => ({ time: times[i], value: c.volume || 0, color: c.close >= c.open ? COLORS.volUp : COLORS.volDown })));
    if (s.rsi) setLine("rsi", ind.rsi);
    if (s.adx) { setLine("adx", ind.adx?.adx ?? null); setLine("plusDi", ind.adx?.plusDi ?? null); setLine("minusDi", ind.adx?.minusDi ?? null); }

    for (const l of priceLineRefs.current) s.candles.removePriceLine(l);
    priceLineRefs.current = [];
    for (const spec of priceLines) {
      priceLineRefs.current.push(s.candles.createPriceLine({ price: spec.price, color: resolveChartColor(spec.color), lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: spec.title }));
    }
    for (const zone of zones) {
      const color = ZONE_COLOR[zone.kind];
      for (const [price, edge] of [[zone.upper, "upper"], [zone.lower, "lower"]] as const) {
        priceLineRefs.current.push(s.candles.createPriceLine({ price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: `${zone.kind === "SUPPORT" ? "S" : "R"} ${edge} (${zone.source})` }));
      }
    }
    // Markers snap to the last bar at or before their time so they never vanish on a coarser timeframe.
    const snapped = markers.map((m) => {
      const t = toTime(m.timestamp);
      if (view.synthetic) {                                        // a brick's time is when it completed: the brick at or after
        const at = brickAt(t, rowTimes);
        return at == null ? null : { time: at as UTCTimestamp, position: m.position, color: resolveChartColor(m.color), shape: m.shape, text: m.text };
      }
      let lo = 0, hi = rowTimes.length - 1, best = -1;
      while (lo <= hi) { const mid = (lo + hi) >> 1; if (rowTimes[mid] <= t) { best = mid; lo = mid + 1; } else hi = mid - 1; }
      return best < 0 ? null : { time: rowTimes[best], position: m.position, color: resolveChartColor(m.color), shape: m.shape, text: m.text };
    }).filter((m): m is NonNullable<typeof m> => m !== null).sort((a, b) => a.time - b.time);
    markersPlugin.current?.setMarkers(snapped);
    const prevAt = rowIndex.get(prevFirst);
    if (keepRange && view.synthetic && prevAt === undefined && keepTimes) {
      // Renko / range after older history was loaded: every brick was rebuilt (new anchor), so the old first brick is
      // gone - keep the visible time window instead of a brick count.
      try { main.timeScale().setVisibleRange(keepTimes); } catch { main.timeScale().fitContent(); }
    } else if (keepRange) {
      // Shift by the rows added on the left, counted in the series' own rows (bricks on Renko / range: a live tick that
      // adds no brick shifts nothing).
      const added = Math.max(0, prevAt ?? 0);
      const range = { from: keepRange.from + added, to: keepRange.to + added };
      for (const c of Object.values(charts.current)) c?.timeScale().setVisibleLogicalRange(range);
    } else if (!incremental) for (const c of Object.values(charts.current)) c?.timeScale().fitContent();
  }, [display, times, timeIndex, ind, priceLines, zones, markers, symbol, timeframe, themeTick, view, chartType, exchange, rowTimes, rowIndex, breaks]);

  // On Renko / range the legend reads the brick under the crosshair (its own OHLC, no volume, no indicators): a brick's
  // time is not a source bar's, so the source-bar lookup would show another bar.
  const brickIdx = view.synthetic ? (hover != null ? rowIndex.get(hover) ?? null : view.bars.length - 1) : null;
  const brick = brickIdx != null && brickIdx >= 0 ? view.bars[brickIdx] : undefined;
  const idx = view.synthetic ? null : hover != null ? timeIndex.get(hover) ?? null : display.length - 1;
  const bar = idx != null ? display[idx] : undefined;
  const prev = idx != null && idx > 0 ? display[idx - 1] : undefined;
  const dayOpen = useMemo(() => {
    if (display.length === 0) return null;
    const lastDay = new Date(display[display.length - 1].timestamp).toLocaleDateString("en-IN", { timeZone: IST });
    const first = display.find((c) => new Date(c.timestamp).toLocaleDateString("en-IN", { timeZone: IST }) === lastDay);
    return first?.open ?? null;
  }, [display]);
  const lastClose = display.length ? display[display.length - 1].close : null;
  const change = lastClose != null && dayOpen ? lastClose - dayOpen : null;
  const changePct = change != null && dayOpen ? (change / dayOpen) * 100 : null;
  const toggle = (id: IndicatorId) => setActive((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const fit = () => { for (const c of Object.values(charts.current)) c?.timeScale().fitContent(); };

  const indicatorControls = (
    <div className={phone ? "flex flex-wrap items-center gap-2" : "ml-auto flex flex-wrap items-center gap-1"}>
      {[...OVERLAYS, ...PANES].map((id) => (
        <button key={id} onClick={() => toggle(id)} title={INDICATOR_LABELS[id]}
                className={`rounded px-1.5 py-0.5 text-xs font-semibold ${active.has(id) ? "bg-surface-3 text-fg ring-1 ring-brand/40" : "text-fg-muted hover:text-fg"}`}>
          {INDICATOR_LABELS[id]}{id === "ema_fast" ? ` ${settings.emaFast}` : id === "ema_slow" ? ` ${settings.emaSlow}` : id === "sma" ? ` ${settings.smaPeriod}` : id === "rsi" ? ` ${settings.rsiPeriod}` : id === "adx" ? ` ${settings.adxPeriod}` : id === "supertrend" ? ` ${settings.stPeriod}/${settings.stMult}` : ""}
        </button>
      ))}
      <button onClick={() => { strat.setOpen(!strat.open); setToolsOpen(false); }} title="Strategies on this chart: draw their trades, deploy them"
              className={`ml-1 flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-semibold ${strat.open || strat.onCount ? "border-brand/40 bg-brand/15 text-brand" : "border-border text-fg hover:bg-surface-2"}`}>
        <Layers size={11} /> Strategies{strat.onCount ? ` (${strat.onCount})` : ""}
      </button>
      <button onClick={() => { fit(); setToolsOpen(false); }} title="Fit all candles" aria-label="Fit all candles" className="rounded p-1 text-fg-muted hover:text-fg"><Scan size={12} /></button>
    </div>
  );

  return (
    <div className={expanded ? "fixed inset-0 z-50 overflow-auto bg-surface p-4" : "w-full"}>
      {/* Header: symbol, last price, change, live badge, timeframe pills, indicator toggles */}
      <div className={`flex flex-wrap items-center gap-2 ${compact ? "mb-1" : "mb-2"} text-xs`}>
        {(title || symbol) && <span className={`font-bold text-fg ${compact ? "text-sm" : "text-base"}`}>{title ?? symbol}</span>}
        {chartType !== "candles" && (
          <span className="rounded border border-border px-1.5 py-0.5 text-xs font-semibold text-fg-muted"
                title={view.synthetic ? "Built from closed bars only: a brick never changes once shown as bars arrive. Loading older history rebuilds every brick from the new first bar (same box size). Gaps are bridged by bricks at prices that may not have traded. No indicators or volume; a marker sits on the brick completed at or after its time." : undefined}>
            {CHART_TYPE_LABELS[chartType]}{view.size != null ? ` · ${view.synthetic && chartType === "range" ? "range" : "box"} ${view.size}` : ""}
          </span>
        )}
        {lastClose != null && (
          <span className={`font-tabular font-bold ${compact ? "text-sm" : "text-base"} ${change != null && change < 0 ? "text-down" : change != null && change > 0 ? "text-up" : "text-fg"}`}>
            {fmt(lastClose)}{change != null && <span className="ml-1.5 text-xs font-semibold">{change >= 0 ? "+" : ""}{fmt(change)} ({changePct?.toFixed(2)}%)</span>}
          </span>
        )}
        {live && (
          <span className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold ${live.stale ? "border-warn/40 bg-warn/10 text-warn" : "border-up/40 bg-up/10 text-up"}`}
                title={live.stale ? live.stale_reason ?? "stale quote" : `${live.source} via ${live.source_broker}`}>
            <Radio size={10} className={live.stale ? "" : "animate-pulse"} /> {live.stale ? `stale · ${live.age_seconds != null ? Math.round(live.age_seconds / 60) + "m old" : "no exchange time"}` : `LIVE${live.age_seconds != null ? ` · ${Math.round(live.age_seconds)}s` : ""}`}
          </span>
        )}
        {liveError && <span className="max-w-[22rem] truncate text-xs text-down" title={liveError}>live price unavailable: {liveError.replace(/^Error:\s*/, "")}</span>}
        {onLoadOlder && (loadingOlder
          ? <span className="text-xs text-fg-muted">Loading older data…</span>
          : olderExhausted ? <span className="text-xs text-fg-muted" title="The broker has no older history for this timeframe">No older history</span> : null)}
        {timeframes && timeframes.length > 1 && onTimeframeChange && (
          <div className="flex overflow-hidden rounded-md border border-border">
            {timeframes.map((tf) => (
              <button key={tf} onClick={() => onTimeframeChange(tf)} className={`px-2 py-0.5 ${tf === timeframe ? "bg-brand/15 text-brand font-semibold" : "text-fg-muted hover:text-fg"}`}>{tf.replace("min", "m")}</button>
            ))}
          </div>
        )}
        {!compact && phone && (
          <button onClick={() => setToolsOpen(true)} title="Indicators, strategies and drawing tools"
                  className="ml-auto flex items-center gap-1 rounded border border-border px-2 py-1 text-xs font-semibold text-fg hover:bg-surface-2">
            <SlidersHorizontal size={12} /> Tools{active.size || strat.onCount ? ` (${active.size + strat.onCount})` : ""}
          </button>
        )}
        {!compact && !phone && indicatorControls}
        <div className={`${compact ? "ml-auto" : ""} flex items-center gap-1`}>
          {openUrl && (
            <a href={openUrl} target="_blank" rel="noopener" title="Open this chart in a new browser tab"
               className="flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-xs font-semibold text-fg hover:bg-surface-2">
              <ExternalLink size={11} /> New tab
            </a>
          )}
          {!fullWindow && (
            <button onClick={() => setExpanded((v) => !v)} title={expanded ? "Back to normal size (Esc)" : "Open the chart full screen"}
                    className="flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-xs font-semibold text-fg hover:bg-surface-2">
              {expanded ? <><Minimize2 size={11} /> Close</> : <><Maximize2 size={11} /> Full screen</>}
            </button>
          )}
        </div>
      </div>

      {strat.panel}

      {tools.view && tools.controller && !phone && <DrawingToolbar view={tools.view} controller={tools.controller} />}
      {phone && tools.view?.tool && <div className="mb-1 text-xs text-fg-muted">Tap the chart to place it · Tools → the same tool to cancel</div>}
      {phone && !compact && (
        <Sheet side="bottom" open={toolsOpen} onOpenChange={setToolsOpen} title="Chart tools">
          <div className="space-y-4 pb-[env(safe-area-inset-bottom)]">
            <section aria-label="Indicators">{indicatorControls}</section>
            {tools.view && tools.controller && (
              <section aria-label="Drawing"><DrawingToolbar view={tools.view} controller={tools.controller} onPick={() => setToolsOpen(false)} /></section>
            )}
          </div>
        </Sheet>
      )}

      {/* Legend */}
      {brick && !compact && (
        <div className="mb-1 flex flex-wrap gap-x-3 gap-y-0.5 font-tabular text-xs text-fg-muted">
          <span className="text-fg-muted">{fmtIst(brick.time, true)}</span>
          <span>{chartType === "range" ? "Range bar" : "Brick"}</span>
          <span>O <b className="text-fg">{fmt(brick.open)}</b></span><span>H <b className="text-fg">{fmt(brick.high)}</b></span>
          <span>L <b className="text-fg">{fmt(brick.low)}</b></span>
          <span>C <b className={brick.close < brick.open ? "text-down" : "text-up"}>{fmt(brick.close)}</b></span>
        </div>
      )}
      {bar && !compact && (
        <div className="mb-1 flex flex-wrap gap-x-3 gap-y-0.5 font-tabular text-xs text-fg-muted">
          <span className="text-fg-muted">{fmtIst(times[idx as number], true)}</span>
          <span>O <b className="text-fg">{fmt(bar.open)}</b></span><span>H <b className="text-fg">{fmt(bar.high)}</b></span>
          <span>L <b className="text-fg">{fmt(bar.low)}</b></span>
          <span>C <b className={prev && bar.close < prev.close ? "text-down" : "text-up"}>{fmt(bar.close)}</b></span>
          {bar.volume > 0 && <span>Vol <b className="text-fg">{bar.volume.toLocaleString("en-IN")}</b></span>}
          {ind.emaFast && <span style={{ color: COLORS.emaFast }}>EMA{settings.emaFast} {fmt(ind.emaFast[idx as number])}</span>}
          {ind.emaSlow && <span style={{ color: COLORS.emaSlow }}>EMA{settings.emaSlow} {fmt(ind.emaSlow[idx as number])}</span>}
          {ind.sma && <span style={{ color: COLORS.sma }}>SMA{settings.smaPeriod} {fmt(ind.sma[idx as number])}</span>}
          {ind.vwap && <span style={{ color: COLORS.vwap }}>VWAP {fmt(ind.vwap[idx as number])}</span>}
          {ind.st && <span style={{ color: ind.st.direction[idx as number] === -1 ? COLORS.stDown : COLORS.stUp }}>ST {fmt(ind.st.line[idx as number])}</span>}
          {ind.bb && <span style={{ color: COLORS.bb }}>BB {fmt(ind.bb.lower[idx as number])} / {fmt(ind.bb.upper[idx as number])}</span>}
          {ind.rsi && <span style={{ color: COLORS.rsi }}>RSI {fmt(ind.rsi[idx as number], 1)}</span>}
          {ind.adx && <span style={{ color: COLORS.adx }}>ADX {fmt(ind.adx.adx[idx as number], 1)} <span style={{ color: COLORS.plusDi }}>+DI {fmt(ind.adx.plusDi[idx as number], 1)}</span> <span style={{ color: COLORS.minusDi }}>-DI {fmt(ind.adx.minusDi[idx as number], 1)}</span></span>}
        </div>
      )}

      <div ref={mainRef} className="w-full" />
      {showVolume && <div className="relative"><span className="absolute left-1 top-0 z-10 text-xs text-fg-muted">Volume</span><div ref={volRef} className="w-full" /></div>}
      {showRsi && <div className="relative"><span className="absolute left-1 top-0 z-10 text-xs" style={{ color: COLORS.rsi }}>RSI {settings.rsiPeriod}</span><div ref={rsiRef} className="w-full" /></div>}
      {showAdx && <div className="relative"><span className="absolute left-1 top-0 z-10 text-xs" style={{ color: COLORS.adx }}>ADX {settings.adxPeriod} · threshold {settings.adxMin}</span><div ref={adxRef} className="w-full" /></div>}
      {display.length === 0 && <div className="flex h-24 items-center justify-center text-xs text-fg-muted">No candles yet - the broker returned none for this window (market closed, or the session token has expired).</div>}
    </div>
  );
}
