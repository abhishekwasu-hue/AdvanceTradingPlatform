/**
 * UI2-4b1 (Charts page plan §3): the v2 chart itself, free of React so every rule has a unit test with a mocked
 * lightweight-charts. One `IChartApi` with v5 native panes - the price pane (0), then volume, then RSI, then the CH6
 * study pane - instead of ProChart's one chart per pane synced by hand.
 *
 * It reuses what exists: `chartTypes.chartView` (Heikin Ashi), the indicators of `utils/indicators`, and
 * `LightweightEngine`, so the drawing tools, layers and studies work on it as on ProChart. The candle series always
 * holds the bars (drawings and layers are anchored to it); for Bars and Line it is made transparent and another series
 * draws the price. Theme, grid, chart type, panes and overlays change in place: the chart is built once.
 */
import {
  BarSeries, CandlestickSeries, HistogramSeries, LineSeries, LineStyle, createChart, createSeriesMarkers, type IChartApi, type IPriceLine, type ISeriesApi,
  type ISeriesMarkersPluginApi, type SeriesMarker,
  type AutoscaleInfo, type IPrimitivePaneRenderer, type MouseEventParams, type IPrimitivePaneView, type ISeriesPrimitive, type LogicalRange, type SeriesDefinition, type SeriesPartialOptionsMap, type SeriesType, type Time, type UTCTimestamp,
} from "lightweight-charts";
import { chartView, formatTick } from "../charting/chartTypes";
import type { ChartMarker } from "../charting/markers";
import type { LayerBand } from "../charting/engine";
import type { Rect } from "../charting/geometry";
import { LightweightEngine, type LineSeriesLike } from "../charting/lightweight";
import { DEFAULT_SETTINGS, closes, ema, rsi, sma, vwap, type Series } from "../utils/indicators";
import { chartFontSize } from "../theme";
import { CHART_FONT_FAMILY, DEFAULT_INTERACTION, baseChartOptions, interactionOptions, trackingOptions, type InteractionState } from "./chartOptions";
import { INITIAL_VIEW, PriceDrag, latestVisible, sameView, shiftWheelPosition, type Readout, type ViewState } from "./gestures";
import { applyChartTheme, candleOptions, chartThemeOptions, type ChartPalette } from "./chartTheme";
import { DAILY_OR_LONGER, type ChartBar, type TimeframeId } from "./timeframes";

/** The brief's chart types; Renko, range and the others stay in the Multi-chart workspace (CH5-b). */
export const CHART_TYPES_V2 = ["candles", "bars", "heikin_ashi", "line"] as const;
export type ChartTypeV2 = (typeof CHART_TYPES_V2)[number];
export const CHART_TYPE_LABELS_V2: Record<ChartTypeV2, string> = { candles: "Candles", bars: "Bars", heikin_ashi: "Heikin Ashi", line: "Line" };

/** Price overlays, in the order of their colour tokens (chart-ov-1..4). */
export const OVERLAYS_V2 = ["ema_fast", "ema_slow", "vwap", "sma"] as const;
export type OverlayId = (typeof OVERLAYS_V2)[number];
export const OVERLAY_LABELS_V2: Record<OverlayId, string> = {
  ema_fast: `EMA ${DEFAULT_SETTINGS.emaFast}`, ema_slow: `EMA ${DEFAULT_SETTINGS.emaSlow}`, vwap: "VWAP", sma: `SMA ${DEFAULT_SETTINGS.smaPeriod}`,
};

export interface PaneChoice { volume: boolean; rsi: boolean }

/** UI2-4e: a level the chart marks (a signal's entry, stop and target): dashed, labelled on the price axis, in the
 * chart's up / down / text colour, and always kept in view. */
export interface LevelLine { price: number; title: string; tone: "up" | "down" | "neutral" }

/** M75 W-1: the layer id the chart's own areas (a Copilot answer's nearest area) are drawn on. */
export const AREAS_LAYER_ID = "chart-areas";

export interface ChartCoreOptions {
  palette: ChartPalette;
  grid: boolean;
  panes: PaneChoice;
  type: ChartTypeV2;
  overlays: readonly OverlayId[];
  /** The zone of the time axis (IST for Indian venues, UTC for 24x7 crypto: `chartTypes.venueZone`). */
  zone: string;
  /** Day, week and month bars show dates without a time of day (crosshair label, time axis). Default 5m. */
  timeframe?: TimeframeId;
  /** Called when the chart is scrolled back to within 10 bars of its oldest bar. */
  onLoadOlder?: () => void;
  /** PR 2: the floating controls' state changed (Auto on / off, the latest bar on / off screen). */
  onView?: (view: ViewState) => void;
  /** PR 2: where the floating controls sit - pixels from the plot's right and bottom edges to the price pane's corner
   * (the price axis on the right; the lower panes and the time axis below). */
  onInsets?: (insets: PlotInsets) => void;
  /** PR 2: the bar under the crosshair (null: the crosshair left the chart). */
  onReadout?: (readout: Readout | null) => void;
}

export interface PlotInsets { right: number; bottom: number }

/** M75 W-1: buyer (demand) areas in the chart's up colour, seller (supply) areas in its down colour, others in its
 * text colour - all from the chart tokens (the trader's candle colours when set). */
export const zoneColors = (p: ChartPalette) => ({ demand: p.up, supply: p.down, neutral: p.text });

/** A pane under the price pane takes this share of the price pane's height. */
const LOWER_PANE_STRETCH = 0.25;
const RSI_LEVELS = [DEFAULT_SETTINGS.rsiHigh, DEFAULT_SETTINGS.rsiMid, DEFAULT_SETTINGS.rsiLow];

const toTime = (iso: string) => Math.floor(Date.parse(iso) / 1000) as UTCTimestamp;

/** The crosshair's time label: "02 Mar 10:15" intraday; "Mon, 02 Mar 2026" for a day or a week (its first day);
 * "Mar 2026" for a month - a daily bar has no time of day worth showing. */
export function crosshairTimeLabel(sec: number, zone: string, tf: TimeframeId = "5min"): string {
  const d = new Date(sec * 1000);
  if (tf === "month") return d.toLocaleDateString("en-IN", { timeZone: zone, month: "short", year: "numeric" });
  if (DAILY_OR_LONGER.has(tf)) return d.toLocaleDateString("en-IN", { timeZone: zone, weekday: "short", day: "2-digit", month: "short", year: "numeric" });
  return d.toLocaleString("en-IN", { timeZone: zone, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}

/** The canvas target a primitive draws on, as the library types it (no direct dependency on its canvas package). */
type DrawTarget = Parameters<IPrimitivePaneRenderer["draw"]>[0];

/** The name of a lower pane ("Volume", "RSI 14"), drawn at its top left in the axis text colour. */
export class PaneLabel implements ISeriesPrimitive<Time> {
  private requestUpdate: (() => void) | null = null;
  private readonly views: readonly IPrimitivePaneView[];

  constructor(public text: string, public color: string) {
    this.views = [{ zOrder: () => "top", renderer: () => ({
      draw: (target: DrawTarget) => target.useMediaCoordinateSpace(({ context }) => {
        context.font = "11px 'JetBrains Mono', ui-monospace, monospace";
        context.fillStyle = this.color;
        context.textBaseline = "top";
        context.fillText(this.text, 8, 6);
      }),
    }) }];
  }

  attached({ requestUpdate }: { requestUpdate: () => void }) { this.requestUpdate = requestUpdate; }
  detached() { this.requestUpdate = null; }
  paneViews() { return this.views; }

  set(text: string, color: string) {
    if (text === this.text && color === this.color) return;
    this.text = text;
    this.color = color;
    this.requestUpdate?.();
  }
}

/** Review PR 3: markers (a backtest's entries and exits) on the bars. A marker snaps to the last bar at or before its
 * time, so it never vanishes on a coarser timeframe; one before the first bar is left out. A colour may be a meaning
 * ("up" / "down" / "fg"), which follows the chart's palette. Sorted by time, as the library wants them. Pure. */
export function snapMarkers(markers: readonly ChartMarker[], times: readonly number[], palette: Pick<ChartPalette, "up" | "down" | "text">): SeriesMarker<Time>[] {
  const colour = (c: string) => (c === "up" ? palette.up : c === "down" ? palette.down : c === "fg" ? palette.text : c);
  const out: SeriesMarker<Time>[] = [];
  for (const m of markers) {
    const t = Math.floor(Date.parse(m.timestamp) / 1000);
    if (!Number.isFinite(t)) continue;
    let lo = 0, hi = times.length - 1, best = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (times[mid] <= t) { best = mid; lo = mid + 1; } else hi = mid - 1; }
    if (best < 0) continue;
    out.push({ time: times[best] as UTCTimestamp, position: m.position, color: colour(m.color), shape: m.shape, text: m.text });
  }
  return out.sort((a, b) => (a.time as number) - (b.time as number));
}

function lineData(times: UTCTimestamp[], s: Series | null) {
  if (!s) return [];
  return times.map((time, i) => (s[i] == null || !Number.isFinite(s[i]) ? { time } : { time, value: s[i] as number }));
}

export class ChartCore {
  readonly chart: IChartApi;
  readonly engine: LightweightEngine;
  private readonly candles: ISeriesApi<"Candlestick">;
  private readonly bars: ISeriesApi<"Bar">;
  private readonly line: ISeriesApi<"Line">;
  private readonly overlays: ISeriesApi<"Line">[];
  private volume: ISeriesApi<"Histogram"> | null = null;
  private rsi: ISeriesApi<"Line"> | null = null;
  private rsiBands: IPriceLine[] = [];
  private readonly volumeLabel = new PaneLabel("Volume", "");
  private readonly rsiLabel = new PaneLabel(`RSI ${DEFAULT_SETTINGS.rsiPeriod}`, "");
  private readonly studyLines = new Set<ISeriesApi<"Line">>();
  private levels: LevelLine[] = [];
  private levelLines: IPriceLine[] = [];
  /** Review PR 3: the markers, on the candle series (it always holds the bars, transparent for Bars and Line). */
  private markers: readonly ChartMarker[] = [];
  /** M75 W-1: areas drawn as filled bands (kept in view like the levels). */
  private areas: readonly LayerBand[] = [];
  private markersPlugin: ISeriesMarkersPluginApi<Time> | null = null;
  private data: ChartBar[] = [];
  private key = "";
  private readonly unsubs: (() => void)[] = [];
  /** R4: the core is the one owner of the gesture options; every change goes through `setInteraction`. */
  private interaction: InteractionState = { ...DEFAULT_INTERACTION };
  // PR 2 (R3): the gesture layer's state.
  private view: ViewState = { ...INITIAL_VIEW };
  private insets: PlotInsets = { right: 0, bottom: 0 };
  private readout: Readout | null = null;
  private priceDrag!: PriceDrag;
  private dragPointer: number | null = null;
  private endWindowDrag: (() => void) | null = null;
  private barIndex = new Map<number, number>();
  private syncQueued = false;
  private removed = false;

  constructor(private readonly element: HTMLElement, private opts: ChartCoreOptions) {
    const theme = chartThemeOptions(opts.palette, opts.grid);
    const daily = DAILY_OR_LONGER.has(opts.timeframe ?? "5min");
    const base = baseChartOptions(this.interaction);
    this.chart = createChart(element, {
      ...theme,
      ...base,
      autoSize: true,
      // The TradingView attribution logo stays (the library's licence asks for it once per chart).
      layout: { ...theme.layout, fontFamily: CHART_FONT_FAMILY, fontSize: chartFontSize(), attributionLogo: true },
      rightPriceScale: { ...theme.rightPriceScale, ...base.rightPriceScale },
      timeScale: { ...theme.timeScale, ...base.timeScale, timeVisible: !daily,
                   tickMarkFormatter: (t: Time, type: number) => formatTick(t as number, type, opts.zone) },
      localization: { timeFormatter: (t: Time) => crosshairTimeLabel(t as number, this.opts.zone, this.opts.timeframe) },
      trackingMode: trackingOptions(),
    });
    const p = opts.palette;
    this.candles = this.chart.addSeries(CandlestickSeries, {
      ...candleOptions(p, this.candlesHidden()),
      // The price scale also takes in the level lines, so a stop or target away from the bars still shows.
      autoscaleInfoProvider: (base: () => AutoscaleInfo | null) => {
        const info = base();
        const prices = [...this.levels.map((l) => l.price), ...this.areas.flatMap((a) => [a.low, a.high])];
        if (!info?.priceRange || prices.length === 0) return info;
        return { ...info, priceRange: { minValue: Math.min(info.priceRange.minValue, ...prices), maxValue: Math.max(info.priceRange.maxValue, ...prices) } };
      },
    });
    this.bars = this.chart.addSeries(BarSeries, { upColor: p.up, downColor: p.down, thinBars: false, priceLineVisible: false });
    this.line = this.chart.addSeries(LineSeries, { color: p.drawing, lineWidth: 2 });
    this.overlays = OVERLAYS_V2.map((_, i) => this.chart.addSeries(LineSeries, {
      color: p.ov[i], lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    }));
    this.engine = new LightweightEngine(this.chart, this.candles, (y) => this.candles.coordinateToPrice(y),
                                        { add: (pane, color, title) => this.addStudyLine(pane, color, title), remove: (s) => this.removeStudyLine(s) },
                                        (enabled) => this.setInteraction({ suspended: !enabled }));
    this.engine.setDefaultDrawingColor(p.drawing);
    this.engine.setZoneColors(zoneColors(p));
    this.setPanes(opts.panes);
    const nearStart = (range: LogicalRange | null) => { if (range && range.from < 10) this.opts.onLoadOlder?.(); };
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(nearStart);
    this.unsubs.push(() => this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(nearStart));
    // PR 2: "→ Latest" follows the visible range (once per frame, not per pan event); the readout follows the crosshair.
    this.priceDrag = new PriceDrag(this.chart.priceScale("right"), () => this.pricePaneHeight());
    const range = () => this.queueSync();
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(range);
    this.unsubs.push(() => this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(range));
    const cross = (p: MouseEventParams) => this.crosshairAt(typeof p.time === "number" && p.point ? p.time : null);
    this.chart.subscribeCrosshairMove(cross);
    this.unsubs.push(() => this.chart.unsubscribeCrosshairMove(cross));
    if (typeof element.addEventListener === "function") this.bindPointer(element);
  }

  // ---- PR 2: the gesture layer (R3), the floating controls' state and the readout ----

  /** The Auto / Latest state now (a copy). */
  viewState(): ViewState { return { ...this.view }; }

  private pricePaneHeight(): number { return this.chart.panes()[0]?.getHeight() ?? 0; }

  /** A press inside the price pane's plot (not on the price axis, a lower pane or the time axis). */
  private inPricePlot(x: number, y: number): boolean {
    return x >= 0 && y >= 0 && x < this.chart.paneSize(0).width && y < this.pricePaneHeight();
  }

  /** A press on the chart at (x, y) of the plot element. Starts a price drag when it is on the price plot, the chart
   * is not suspended (a drawing drag) and, for a finger, the chart captures vertical drags. True: the drag is tracked. */
  pressPlot(x: number, y: number, pointerType = "mouse", pointerId = 1): boolean {
    if (this.dragPointer !== null) { this.releasePointer(); return false; }      // a second finger: a pinch, not a pan
    if (this.interaction.suspended || (pointerType === "touch" && !this.interaction.vertTouchDrag)) return false;
    if (!this.inPricePlot(x, y) || !this.priceDrag.begin(x, y)) return false;
    this.dragPointer = pointerId;
    return true;
  }

  /** The pressed pointer moved. A drawing drag that began meanwhile ends the price drag. */
  movePointer(x: number, y: number, pointerId = 1): void {
    if (pointerId !== this.dragPointer) return;
    if (this.interaction.suspended) { this.releasePointer(); return; }
    if (this.priceDrag.move(x, y) && this.view.autoScale) this.syncView();      // the first move turned Auto off
  }

  /** The press ended. */
  releasePointer(): void {
    this.dragPointer = null;
    this.priceDrag.end();
    this.endWindowDrag?.();
    this.syncView();
  }

  /** "Auto" on or off: on fits the prices on screen, off keeps the trader's price range. */
  setAutoScale(on: boolean): void {
    this.chart.priceScale("right").setAutoScale(on);
    this.syncView();
  }

  /** "Reset": auto price, the default zoom, the latest bar. */
  resetView(): void {
    this.chart.priceScale("right").setAutoScale(true);
    this.chart.timeScale().resetTimeScale();
    this.scrollToLatest();
  }

  /** "→ Latest": back to the latest bar (the zoom and the price range stay). At once, not the library's animated
   * `scrollToRealTime`: the button goes the moment it is pressed, and a test clock cannot stall it. */
  scrollToLatest(): void {
    const ts = this.chart.timeScale();
    ts.scrollToPosition(ts.options().rightOffset, false);
    this.syncView();
  }

  /** Shift + wheel pans time where the browser does not turn it into a sideways wheel. True: handled here. */
  wheel(e: { shiftKey: boolean; deltaX: number; deltaY: number; deltaMode?: number }): boolean {
    if (this.interaction.suspended) return false;
    const ts = this.chart.timeScale();
    const position = shiftWheelPosition(e, ts.scrollPosition(), ts.options().barSpacing);
    if (position === null) return false;
    ts.scrollToPosition(position, false);
    return true;
  }

  /** Reads the Auto state back from the price scale (the library has no event for it: an axis drag turns it off, a
   * double-click / double-tap on the axis turns it on) and the latest bar's place, and tells the page what changed. */
  syncView(): void {
    if (this.removed) return;
    const next: ViewState = {
      autoScale: !!this.chart.priceScale("right").options().autoScale,
      latestVisible: latestVisible(this.chart.timeScale().getVisibleLogicalRange(), this.data.length),
    };
    if (!sameView(next, this.view)) { this.view = next; this.opts.onView?.(next); }
    const paneHeight = this.pricePaneHeight();
    const height = this.element.clientHeight ?? 0;
    const insets = { right: this.chart.priceScale("right").width(), bottom: Math.max(0, height - paneHeight) };
    if (paneHeight > 0 && (insets.right !== this.insets.right || insets.bottom !== this.insets.bottom)) {
      this.insets = insets;
      this.opts.onInsets?.(insets);
    }
  }

  /** One sync per frame (a pan fires the range event many times a frame); at once where there are no frames (tests). */
  private queueSync(): void {
    if (typeof requestAnimationFrame !== "function") { this.syncView(); return; }
    if (this.syncQueued) return;
    this.syncQueued = true;
    requestAnimationFrame(() => { this.syncQueued = false; this.syncView(); });
  }

  /** The crosshair is on the bar at `time` (null: off the chart). */
  crosshairAt(time: number | null): void {
    const i = time === null ? undefined : this.barIndex.get(time);
    const b = i === undefined ? undefined : this.data[i];
    const next: Readout | null = b && time !== null
      ? { time, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume > 0 ? b.volume : null } : null;
    const r = this.readout;
    if (r === next || (r && next && r.time === next.time && r.open === next.open && r.high === next.high && r.low === next.low
                       && r.close === next.close && r.volume === next.volume)) return;
    this.readout = next;
    this.opts.onReadout?.(next);
  }

  /** A price as the price axis writes it. */
  formatPrice(price: number): string { return this.candles.priceFormatter().format(price); }

  /** The DOM side of the gesture layer. Presses bubble up from the library's canvases (a drawing tool that takes a press
   * stops it before, in the capture phase); moves and the release are followed on the window, so a drag that leaves
   * the chart still ends. The Auto state is read back after the library has handled a release, a double-click, a
   * double-tap or a wheel. */
  private bindPointer(el: HTMLElement): void {
    const local = (e: { clientX: number; clientY: number }) => {
      const r = el.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };
    const move = (e: PointerEvent) => { const p = local(e); this.movePointer(p.x, p.y, e.pointerId); };
    const up = (e: PointerEvent) => { if (e.pointerId === this.dragPointer) this.releasePointer(); };
    this.endWindowDrag = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
    const down = (e: PointerEvent) => {
      if (e.defaultPrevented || e.button !== 0) return;
      const p = local(e);
      if (!this.pressPlot(p.x, p.y, e.pointerType, e.pointerId)) return;
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    };
    const later = () => { window.setTimeout(() => this.syncView(), 0); };
    const wheel = (e: WheelEvent) => {
      if (this.wheel(e)) { e.preventDefault(); e.stopPropagation(); }
      later();
    };
    el.addEventListener("pointerdown", down);
    el.addEventListener("pointerup", later);
    el.addEventListener("dblclick", later);
    el.addEventListener("touchend", later);
    el.addEventListener("wheel", wheel, { capture: true, passive: false });
    this.unsubs.push(() => {
      this.endWindowDrag?.();
      el.removeEventListener("pointerdown", down);
      el.removeEventListener("pointerup", later);
      el.removeEventListener("dblclick", later);
      el.removeEventListener("touchend", later);
      el.removeEventListener("wheel", wheel, { capture: true });
    });
  }

  /** The gesture state now (a copy). */
  interactionState(): InteractionState { return { ...this.interaction }; }

  /** R4: changes the gesture state and writes the full options for it (a drawing drag suspends and resumes here). */
  setInteraction(change: Partial<InteractionState>): void {
    const next = { ...this.interaction, ...change };
    if (next.vertTouchDrag === this.interaction.vertTouchDrag && next.suspended === this.interaction.suspended) return;
    this.interaction = next;
    this.chart.applyOptions(interactionOptions(next));
  }

  private candlesHidden(): boolean { return this.opts.type === "bars" || this.opts.type === "line"; }

  /** A series in a new pane at the end, moved to `index` (so it never joins the pane already there). */
  private addLowerPane<T extends SeriesType>(def: SeriesDefinition<T>, options: SeriesPartialOptionsMap[T], index: number): ISeriesApi<T> {
    const series = this.chart.addSeries(def, options, this.chart.panes().length);
    const pane = series.getPane();
    if (pane.paneIndex() !== index) pane.moveTo(index);
    pane.setStretchFactor(LOWER_PANE_STRETCH);
    return series;
  }

  /** Volume and RSI panes on or off. Both are rebuilt in their order (an empty pane is removed by the library), then
   * their data is sent again; the price pane, the drawings and the zoom are untouched. */
  setPanes(panes: PaneChoice): void {
    if (!!this.volume === panes.volume && !!this.rsi === panes.rsi) return;
    const p = this.opts.palette;
    if (this.volume) this.chart.removeSeries(this.volume);
    if (this.rsi) this.chart.removeSeries(this.rsi);
    this.volume = panes.volume
      ? this.addLowerPane(HistogramSeries, { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false }, 1) : null;
    this.rsi = panes.rsi
      ? this.addLowerPane(LineSeries, { color: p.rsi, lineWidth: 2, priceLineVisible: false, crosshairMarkerVisible: false },
                                        panes.volume ? 2 : 1) : null;
    this.volume?.attachPrimitive(this.volumeLabel);
    this.rsi?.attachPrimitive(this.rsiLabel);
    this.paintLabels();
    this.rsiBands = this.rsi ? RSI_LEVELS.map((price) => (this.rsi as ISeriesApi<"Line">).createPriceLine({
      price, color: p.rsiBand, lineWidth: 1, lineStyle: price === DEFAULT_SETTINGS.rsiMid ? LineStyle.Dotted : LineStyle.Dashed,
      axisLabelVisible: false, title: "",
    })) : [];
    this.opts = { ...this.opts, panes };
    this.render();
    this.queueSync();                                                         // the price pane's height changed
  }

  /** A new timeframe: day / week / month labels lose the time of day (the bars come with the next setData). */
  setTimeframe(timeframe: TimeframeId): void {
    if (timeframe === this.opts.timeframe) return;
    this.opts = { ...this.opts, timeframe };
    this.chart.applyOptions({ timeScale: { timeVisible: !DAILY_OR_LONGER.has(timeframe) } });
  }

  /** UI2-4e: the level lines (replaced as a whole; a price that is not a number is left out). While there are levels
   * the last-price line is neutral and dotted, so it never reads as the red dashed Stop. */
  setLevels(levels: readonly LevelLine[]): void {
    for (const line of this.levelLines) this.candles.removePriceLine(line);
    const had = this.levels.length > 0;
    this.levels = levels.filter((l) => Number.isFinite(l.price));
    if (had !== this.levels.length > 0) this.candles.applyOptions(this.lastPriceLine());
    this.levelLines = this.levels.map((l) => this.candles.createPriceLine({
      price: l.price, color: this.levelColour(l.tone), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: l.title,
    }));
  }

  /** M75 W-1 (W2): areas as filled, translucent bands with their label inside or beside them (geometry.ts
   * `placeBandLabels`) - never two dashed price lines with titles on the axis. Replaced as a whole; kept in view. */
  setAreas(areas: readonly LayerBand[]): void {
    this.areas = areas.filter((a) => Number.isFinite(a.low) && Number.isFinite(a.high));
    this.engine.setLayer(AREAS_LAYER_ID, this.areas.length > 0 ? { bands: [...this.areas] } : null);
  }

  /** M75 W-1 (W3): rectangles of the plot (the floating controls, the chip row) no band label may sit on - in plot
   * pixels; moved into pane pixels here (the pane starts after the left price scale). */
  setLabelAvoid(rects: readonly Rect[]): void {
    const dx = this.engine.paneOffsetX();
    this.engine.setLabelAvoid(rects.map((r) => ({ ...r, x: r.x - dx })));
  }

  /** Review PR 3: the markers (replaced as a whole). They sit on the candle series, which always holds the bars, so
   * they show for every chart type. */
  setMarkers(markers: readonly ChartMarker[]): void {
    if (markers.length === 0 && this.markers.length === 0) return;
    this.markers = markers;
    this.paintMarkers();
  }

  private paintMarkers(): void {
    if (this.markers.length === 0 && !this.markersPlugin) return;
    this.markersPlugin ??= createSeriesMarkers(this.candles, []);
    this.markersPlugin.setMarkers(snapMarkers(this.markers, this.data.map((b) => toTime(b.timestamp)), this.opts.palette));
  }

  /** The last-price line: the library's own (the bar's colour, dashed), or neutral and dotted beside level lines. */
  private lastPriceLine() {
    return this.levels.length > 0
      ? { priceLineColor: this.opts.palette.crosshair, priceLineStyle: LineStyle.Dotted }
      : { priceLineColor: "", priceLineStyle: LineStyle.Dashed };
  }

  private levelColour(tone: LevelLine["tone"]): string {
    const p = this.opts.palette;
    return tone === "up" ? p.up : tone === "down" ? p.down : p.text;
  }

  /** The volume pane's name: "Volume", or where an index's volume comes from ("Volume · NIFTY OCT FUT"). */
  setVolumeLabel(text: string): void {
    this.volumeLabel.set(text, this.opts.palette.text);
  }

  private paintLabels(): void {
    this.volumeLabel.set(this.volumeLabel.text, this.opts.palette.text);
    this.rsiLabel.set(this.rsiLabel.text, this.opts.palette.text);
  }

  setType(type: ChartTypeV2): void {
    if (type === this.opts.type) return;
    this.opts = { ...this.opts, type };
    this.candles.applyOptions(candleOptions(this.opts.palette, this.candlesHidden()));
    this.render();
  }

  setOverlays(overlays: readonly OverlayId[]): void {
    this.opts = { ...this.opts, overlays };
    this.render();
  }

  /** The chart theme, the grid or the text size changed: options in place, no rebuild (the visible range is never
   * touched). */
  applyTheme(palette: ChartPalette, grid: boolean): void {
    this.opts = { ...this.opts, palette, grid };
    this.chart.applyOptions({ layout: { fontSize: chartFontSize() } });
    applyChartTheme(this.chart, { candles: this.candles, bars: this.bars, line: this.line, rsi: this.rsi ?? undefined,
                                  rsiBands: this.rsiBands, overlays: this.overlays }, palette, grid, this.candlesHidden());
    this.engine.setDefaultDrawingColor(palette.drawing);
    this.engine.setZoneColors(zoneColors(palette));
    this.levelLines.forEach((line, i) => line.applyOptions({ color: this.levelColour(this.levels[i].tone) }));
    if (this.levels.length > 0) this.candles.applyOptions(this.lastPriceLine());
    this.paintLabels();
    this.paintVolume(this.data.map((b) => toTime(b.timestamp)));      // volume carries its colour per bar
    if (this.markers.length > 0) this.paintMarkers();                   // a meaning colour follows the palette
  }

  /** New bars. The same `key` (symbol, timeframe, type) keeps the trader's view, shifted by the bars added on the left
   * (older history loaded); a new key fits every bar. */
  setData(bars: ChartBar[], key: string): void {
    const prevFirst = this.data[0]?.timestamp;
    const keep = key === this.key && this.data.length > 0 ? this.chart.timeScale().getVisibleLogicalRange() : null;
    this.data = bars;
    this.key = key;
    this.render();
    if (bars.length > 0 && !keep) this.chart.timeScale().fitContent();
    const added = keep ? bars.findIndex((b) => b.timestamp === prevFirst) : -1;
    if (keep && added > 0) this.chart.timeScale().setVisibleLogicalRange({ from: keep.from + added, to: keep.to + added });
    this.queueSync();                                                         // a new bar may be past the screen
  }

  private paintVolume(times: UTCTimestamp[]): void {
    const p = this.opts.palette;
    this.volume?.setData(this.data.map((b, i) => ({ time: times[i], value: b.volume || 0, color: b.close >= b.open ? p.volUp : p.volDown })));
  }

  private render(): void {
    const { palette: p, type, overlays } = this.opts;
    const times = this.data.map((b) => toTime(b.timestamp));
    this.barIndex = new Map(times.map((t, i) => [t as number, i]));
    const ohlc = this.data.map((b, i) => ({ time: times[i] as number, open: b.open, high: b.high, low: b.low, close: b.close }));
    const view = chartView(type === "heikin_ashi" ? "heikin_ashi" : "candles", ohlc, ohlc, { periodSec: 60, nowSec: 0, up: p.up, down: p.down });
    const shown = view.bars.map((b) => ({ ...b, time: b.time as UTCTimestamp }));
    this.candles.setData(shown);
    this.bars.setData(type === "bars" ? shown : []);
    this.line.setData(type === "line" ? shown.map((b) => ({ time: b.time, value: b.close })) : []);
    const cl = closes(this.data);
    const values: Record<OverlayId, () => Series> = {
      ema_fast: () => ema(cl, DEFAULT_SETTINGS.emaFast), ema_slow: () => ema(cl, DEFAULT_SETTINGS.emaSlow),
      vwap: () => vwap(this.data), sma: () => sma(cl, DEFAULT_SETTINGS.smaPeriod),
    };
    OVERLAYS_V2.forEach((id, i) => this.overlays[i].setData(overlays.includes(id) ? lineData(times, values[id]()) : []));
    this.paintVolume(times);
    this.rsi?.setData(lineData(times, rsi(cl, DEFAULT_SETTINGS.rsiPeriod)));
    if (this.markers.length > 0) this.paintMarkers();                   // the bars' times may have changed
  }

  /** CH6 study lines: on the price pane, or all together in one pane under volume and RSI. */
  private addStudyLine(pane: number, color: string, title: string): LineSeriesLike {
    const [existing] = this.studyLines;
    const index = pane === 0 ? 0 : existing ? existing.getPane().paneIndex() : this.chart.panes().length;
    const series = this.chart.addSeries(LineSeries, { color, lineWidth: 2, priceLineVisible: false, title, crosshairMarkerVisible: false }, index);
    if (pane !== 0) this.studyLines.add(series);
    return series as unknown as LineSeriesLike;
  }

  private removeStudyLine(line: LineSeriesLike): void {
    const series = line as unknown as ISeriesApi<"Line">;
    this.studyLines.delete(series);
    this.chart.removeSeries(series);
  }

  remove(): void {
    this.removed = true;
    for (const u of this.unsubs) u();
    this.engine.dispose();
    this.markersPlugin?.detach();
    this.chart.remove();
  }
}
