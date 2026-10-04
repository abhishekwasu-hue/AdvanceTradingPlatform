import {
  ColorType, CrosshairMode, LineStyle, createChart,
  type IChartApi, type IPriceLine, type ISeriesApi, type LogicalRange, type MouseEventParams, type UTCTimestamp,
} from "lightweight-charts";
import { ExternalLink, Maximize2, Minimize2, Radio, Scan } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { LtpResponse, OHLCVBar, SRZone } from "../types";
import {
  DEFAULT_SETTINGS, INDICATOR_LABELS, OVERLAYS, PANES, adx, applyLivePrice, bollinger, closes, ema, indicatorsForStrategy, rsi, sma,
  supertrend, vwap, type IndicatorId, type IndicatorSettings, type Series,
} from "../utils/indicators";
import type { ChartMarker, PriceLineSpec } from "./CandleChart";

export type { ChartMarker, PriceLineSpec } from "./CandleChart";

/** URL of the full-window chart for a broker symbol (opened in a new browser tab; see ChartWindow). */
export function chartWindowUrl(symbol: string, timeframe = "5min", exchange = "NSE", broker?: string): string {
  const q = new URLSearchParams({ chart: symbol, tf: timeframe, exchange });
  if (broker) q.set("broker_name", broker);
  return `${window.location.pathname}?${q.toString()}`;
}
export { directionMarker } from "./CandleChart";

/**
 * Phase AN: the Pro Chart. TradingView's open-source Lightweight Charts engine (the same one the
 * plain CandleChart used), now with the things a trader expects from a terminal chart:
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
const COLORS = {
  up: "#22c55e", down: "#ef4444", emaFast: "#38bdf8", emaSlow: "#f59e0b", sma: "#a78bfa", bb: "#64748b", vwap: "#e879f9",
  stUp: "#22c55e", stDown: "#ef4444", rsi: "#38bdf8", adx: "#f59e0b", plusDi: "#22c55e", minusDi: "#ef4444", volUp: "rgba(34,197,94,0.45)", volDown: "rgba(239,68,68,0.45)",
  grid: "#1a2333", border: "#243044", text: "#c2cad8",
};
const ZONE_COLOR = { SUPPORT: "#22c55e", RESISTANCE: "#ef4444" } as const;

const toTime = (iso: string): UTCTimestamp => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
const fmtIst = (t: number, withDate = true) => {
  const d = new Date(t * 1000);
  return d.toLocaleString("en-IN", { timeZone: IST, ...(withDate ? { day: "2-digit", month: "short" } : {}), hour: "2-digit", minute: "2-digit", hour12: false });
};
const fmt = (v: number | null | undefined, digits = 2) => (v == null || !Number.isFinite(v) ? "-" : v.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits }));

function baseOptions(height: number, showTime: boolean, attribution = true) {
  return {
    height,
    // The TradingView attribution logo stays on the main pane (the library's licence asks for it once); the stacked indicator panes do not repeat it.
    layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: COLORS.text, fontFamily: "'JetBrains Mono', ui-monospace, monospace", fontSize: 11, attributionLogo: attribution },
    grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { borderColor: COLORS.border, minimumWidth: 72 },
    timeScale: { visible: showTime, timeVisible: true, secondsVisible: false, borderColor: COLORS.border, rightOffset: 4,
                 tickMarkFormatter: (t: number) => fmtIst(t, false) },
    localization: { timeFormatter: (t: number) => fmtIst(t, true) },
    handleScroll: true, handleScale: true,
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
export function useLiveLtp(enabled: boolean, symbol: string | undefined, exchange = "NSE", broker?: string, intervalMs = 5000) {
  const [ltp, setLtp] = useState<LtpResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setLtp(null); setError(null);
    if (!enabled || !symbol) return;
    let cancelled = false;
    const tick = async () => {
      if (document.visibilityState !== "visible") return;
      try { const r = await api.marketDataLtp(symbol, exchange, broker); if (!cancelled) { setLtp(r); setError(null); } }
      catch (e) { if (!cancelled) setError(String(e)); }
    };
    void tick();
    const id = window.setInterval(tick, intervalMs);
    return () => { cancelled = true; window.clearInterval(id); };
  }, [enabled, symbol, exchange, broker, intervalMs]);
  return { ltp, error };
}

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
}

export default function ProChart({
  candles, symbol, timeframe, timeframes, onTimeframeChange, priceLines = [], zones = [], markers = [], height = 380,
  strategyParams, defaultIndicators, live, liveError, compact: compactProp = false, title, openUrl, fullWindow = false,
}: ProChartProps) {
  // Expanded: the same chart over the whole screen, with the full toolbar and panes even if it was a mini chart.
  const [expanded, setExpanded] = useState(false);
  const [winH, setWinH] = useState(() => window.innerHeight);
  useEffect(() => {
    const onResize = () => setWinH(window.innerHeight);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setExpanded(false); };
    window.addEventListener("resize", onResize); window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("resize", onResize); window.removeEventListener("keydown", onKey); };
  }, []);
  const big = expanded || fullWindow;
  const compact = compactProp && !big;
  const derived = useMemo(() => indicatorsForStrategy(strategyParams), [strategyParams]);
  const [active, setActive] = useState<Set<IndicatorId>>(() => new Set(defaultIndicators ?? derived.ids));
  const [settings] = useState<IndicatorSettings>(() => ({ ...DEFAULT_SETTINGS, ...derived.settings }));
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => { setActive(new Set(defaultIndicators ?? derived.ids)); }, [derived, defaultIndicators]);

  const display = useMemo(() => applyLivePrice(candles, live?.ltp, live?.timestamp ?? live?.fetched_at, timeframe), [candles, live, timeframe]);
  const times = useMemo(() => display.map((c) => toTime(c.timestamp)), [display]);
  const timeIndex = useMemo(() => { const m = new Map<number, number>(); times.forEach((t, i) => m.set(t, i)); return m; }, [times]);

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
  const volRef = useRef<HTMLDivElement>(null);
  const rsiRef = useRef<HTMLDivElement>(null);
  const adxRef = useRef<HTMLDivElement>(null);
  const charts = useRef<{ main?: IChartApi; vol?: IChartApi; rsi?: IChartApi; adx?: IChartApi }>({});
  const series = useRef<Record<string, ISeriesApi<"Candlestick" | "Line" | "Histogram">>>({});
  const priceLineRefs = useRef<IPriceLine[]>([]);
  const dataKey = useRef<string>("");
  const lastShape = useRef<{ len: number; lastTime: number }>({ len: 0, lastTime: 0 });
  const syncing = useRef(false);

  // Build the charts once per pane layout.
  useEffect(() => {
    const main = mainRef.current;
    if (!main) return;
    const made: IChartApi[] = [];
    const mk = (el: HTMLDivElement | null, h: number, showTime: boolean, attribution = false) => {
      if (!el) return undefined;
      const c = createChart(el, baseOptions(h, showTime, attribution));
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
    s.candles = mainChart.addCandlestickSeries({ upColor: COLORS.up, downColor: COLORS.down, borderVisible: false, wickUpColor: COLORS.up, wickDownColor: COLORS.down });
    const line = (chart: IChartApi, color: string, width: 1 | 2 = 1, style = LineStyle.Solid) =>
      chart.addLineSeries({ color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    s.emaFast = line(mainChart, COLORS.emaFast, 2); s.emaSlow = line(mainChart, COLORS.emaSlow, 2); s.sma = line(mainChart, COLORS.sma, 1);
    s.bbUpper = line(mainChart, COLORS.bb, 1, LineStyle.Dotted); s.bbLower = line(mainChart, COLORS.bb, 1, LineStyle.Dotted); s.bbMid = line(mainChart, COLORS.bb, 1, LineStyle.Dashed);
    s.vwap = line(mainChart, COLORS.vwap, 1, LineStyle.Dashed); s.st = line(mainChart, COLORS.stUp, 2);
    if (volChart) s.vol = volChart.addHistogramSeries({ priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
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
    dataKey.current = "";

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
      for (const c of all) c.remove();
      charts.current = {}; series.current = {}; priceLineRefs.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartHeight, compact, showVolume, showRsi, showAdx, settings.rsiHigh, settings.rsiMid, settings.rsiLow, settings.adxMin]);

  // Feed data. A fresh symbol/length reset calls setData + fitContent; a live move updates the last bar only.
  useEffect(() => {
    const s = series.current; const main = charts.current.main;
    if (!s.candles || !main || display.length === 0) return;
    const key = `${display[0].timestamp}|${symbol ?? ""}|${timeframe ?? ""}`;
    const candleData = display.map((c, i) => ({ time: times[i], open: c.open, high: c.high, low: c.low, close: c.close }));
    // Incremental only when this is the same series grown by at most one bar (a live tick moved
    // or opened the forming candle); anything else is a new dataset. Lightweight Charts refuses an
    // `update` older than the last bar, so bars are applied oldest first and a refusal falls back
    // to a full reload rather than taking the page down.
    const prev = lastShape.current;
    const grown = display.length - prev.len;
    const sameSeries = key === dataKey.current && (grown === 0 || grown === 1) && prev.len > 0 && times[prev.len - 1] === prev.lastTime;
    let incremental = sameSeries;
    if (incremental) {
      try {
        if (grown === 1) s.candles.update(candleData[candleData.length - 2]);
        s.candles.update(candleData[candleData.length - 1]);
      } catch {
        incremental = false;
      }
    }
    if (!incremental) {
      s.candles.setData(candleData);
      dataKey.current = key;
    }
    lastShape.current = { len: display.length, lastTime: times[times.length - 1] };
    const setLine = (name: string, data: Series | null, color?: (i: number) => string | undefined) => {
      const ser = s[name]; if (!ser) return;
      ser.setData(data ? lineData(times, data, color) : []);
    };
    setLine("emaFast", ind.emaFast); setLine("emaSlow", ind.emaSlow); setLine("sma", ind.sma);
    setLine("bbUpper", ind.bb?.upper ?? null); setLine("bbLower", ind.bb?.lower ?? null); setLine("bbMid", ind.bb?.mid ?? null);
    setLine("vwap", ind.vwap);
    setLine("st", ind.st?.line ?? null, (i) => (ind.st?.direction[i] === -1 ? COLORS.stDown : COLORS.stUp));
    if (s.vol) s.vol.setData(display.map((c, i) => ({ time: times[i], value: c.volume || 0, color: c.close >= c.open ? COLORS.volUp : COLORS.volDown })));
    if (s.rsi) setLine("rsi", ind.rsi);
    if (s.adx) { setLine("adx", ind.adx?.adx ?? null); setLine("plusDi", ind.adx?.plusDi ?? null); setLine("minusDi", ind.adx?.minusDi ?? null); }

    for (const l of priceLineRefs.current) s.candles.removePriceLine(l);
    priceLineRefs.current = [];
    for (const spec of priceLines) {
      priceLineRefs.current.push(s.candles.createPriceLine({ price: spec.price, color: spec.color, lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: spec.title }));
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
      let lo = 0, hi = times.length - 1, best = -1;
      while (lo <= hi) { const mid = (lo + hi) >> 1; if (times[mid] <= t) { best = mid; lo = mid + 1; } else hi = mid - 1; }
      return best < 0 ? null : { time: times[best], position: m.position, color: m.color, shape: m.shape, text: m.text };
    }).filter((m): m is NonNullable<typeof m> => m !== null).sort((a, b) => a.time - b.time);
    (s.candles as ISeriesApi<"Candlestick">).setMarkers(snapped);
    if (!incremental) for (const c of Object.values(charts.current)) c?.timeScale().fitContent();
  }, [display, times, ind, priceLines, zones, markers, symbol, timeframe]);

  const idx = hover != null ? timeIndex.get(hover) ?? null : display.length - 1;
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

  return (
    <div className={expanded ? "fixed inset-0 z-50 overflow-auto bg-bg p-4" : "w-full"}>
      {/* Header: symbol, last price, change, live badge, timeframe pills, indicator toggles */}
      <div className={`flex flex-wrap items-center gap-2 ${compact ? "mb-1" : "mb-2"} text-xs`}>
        {(title || symbol) && <span className={`font-bold text-slate-100 ${compact ? "text-sm" : "text-base"}`}>{title ?? symbol}</span>}
        {lastClose != null && (
          <span className={`font-tabular font-bold ${compact ? "text-sm" : "text-base"} ${change != null && change < 0 ? "text-rose-300" : "text-emerald-300"}`}>
            {fmt(lastClose)}{change != null && <span className="ml-1.5 text-xs font-semibold">{change >= 0 ? "+" : ""}{fmt(change)} ({changePct?.toFixed(2)}%)</span>}
          </span>
        )}
        {live && (
          <span className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${live.stale ? "border-amber-400/40 bg-amber-500/10 text-amber-300" : "border-emerald-400/40 bg-emerald-500/10 text-emerald-300"}`}
                title={live.stale ? live.stale_reason ?? "stale quote" : `${live.source} via ${live.source_broker}`}>
            <Radio size={10} className={live.stale ? "" : "animate-pulse"} /> {live.stale ? `stale · ${live.age_seconds != null ? Math.round(live.age_seconds / 60) + "m old" : "no exchange time"}` : `LIVE${live.age_seconds != null ? ` · ${Math.round(live.age_seconds)}s` : ""}`}
          </span>
        )}
        {liveError && <span className="max-w-[22rem] truncate text-[10px] text-rose-300" title={liveError}>live price unavailable: {liveError.replace(/^Error:\s*/, "")}</span>}
        {timeframes && timeframes.length > 1 && onTimeframeChange && (
          <div className="flex overflow-hidden rounded-md border border-border">
            {timeframes.map((tf) => (
              <button key={tf} onClick={() => onTimeframeChange(tf)} className={`px-2 py-0.5 ${tf === timeframe ? "bg-sky-500/20 text-sky-200" : "text-muted hover:text-slate-200"}`}>{tf.replace("min", "m")}</button>
            ))}
          </div>
        )}
        {!compact && (
          <div className="ml-auto flex flex-wrap items-center gap-1">
            {[...OVERLAYS, ...PANES].map((id) => (
              <button key={id} onClick={() => toggle(id)} title={INDICATOR_LABELS[id]}
                      className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${active.has(id) ? "bg-panel3 text-slate-100 ring-1 ring-sky-500/40" : "text-muted hover:text-slate-300"}`}>
                {INDICATOR_LABELS[id]}{id === "ema_fast" ? ` ${settings.emaFast}` : id === "ema_slow" ? ` ${settings.emaSlow}` : id === "sma" ? ` ${settings.smaPeriod}` : id === "rsi" ? ` ${settings.rsiPeriod}` : id === "adx" ? ` ${settings.adxPeriod}` : id === "supertrend" ? ` ${settings.stPeriod}/${settings.stMult}` : ""}
              </button>
            ))}
            <button onClick={fit} title="Fit all candles" className="rounded p-1 text-muted hover:text-slate-200"><Scan size={12} /></button>
          </div>
        )}
        <div className={`${compact ? "ml-auto" : ""} flex items-center gap-1`}>
          {openUrl && (
            <a href={openUrl} target="_blank" rel="noopener" title="Open this chart in a new browser tab"
               className="flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[10px] font-semibold text-slate-200 hover:bg-panel2">
              <ExternalLink size={11} /> New tab
            </a>
          )}
          {!fullWindow && (
            <button onClick={() => setExpanded((v) => !v)} title={expanded ? "Back to normal size (Esc)" : "Open the chart full screen"}
                    className="flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[10px] font-semibold text-slate-200 hover:bg-panel2">
              {expanded ? <><Minimize2 size={11} /> Close</> : <><Maximize2 size={11} /> Full screen</>}
            </button>
          )}
        </div>
      </div>

      {/* Legend */}
      {bar && !compact && (
        <div className="mb-1 flex flex-wrap gap-x-3 gap-y-0.5 font-tabular text-[11px] text-muted">
          <span className="text-slate-300">{fmtIst(times[idx as number], true)}</span>
          <span>O <b className="text-slate-200">{fmt(bar.open)}</b></span><span>H <b className="text-slate-200">{fmt(bar.high)}</b></span>
          <span>L <b className="text-slate-200">{fmt(bar.low)}</b></span>
          <span>C <b className={prev && bar.close < prev.close ? "text-rose-300" : "text-emerald-300"}>{fmt(bar.close)}</b></span>
          {bar.volume > 0 && <span>Vol <b className="text-slate-200">{bar.volume.toLocaleString("en-IN")}</b></span>}
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
      {showVolume && <div className="relative"><span className="absolute left-1 top-0 z-10 text-[10px] text-muted">Volume</span><div ref={volRef} className="w-full" /></div>}
      {showRsi && <div className="relative"><span className="absolute left-1 top-0 z-10 text-[10px]" style={{ color: COLORS.rsi }}>RSI {settings.rsiPeriod}</span><div ref={rsiRef} className="w-full" /></div>}
      {showAdx && <div className="relative"><span className="absolute left-1 top-0 z-10 text-[10px]" style={{ color: COLORS.adx }}>ADX {settings.adxPeriod} · threshold {settings.adxMin}</span><div ref={adxRef} className="w-full" /></div>}
      {display.length === 0 && <div className="flex h-24 items-center justify-center text-xs text-muted">No candles yet - the broker returned none for this window (market closed, or the session token has expired).</div>}
    </div>
  );
}
