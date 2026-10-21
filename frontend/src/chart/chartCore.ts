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
  BarSeries, CandlestickSeries, HistogramSeries, LineSeries, LineStyle, createChart, type IChartApi, type IPriceLine, type ISeriesApi,
  type AutoscaleInfo, type IPrimitivePaneRenderer, type IPrimitivePaneView, type ISeriesPrimitive, type LogicalRange, type SeriesDefinition, type SeriesPartialOptionsMap, type SeriesType, type Time, type UTCTimestamp,
} from "lightweight-charts";
import { chartView, formatTick } from "../charting/chartTypes";
import { LightweightEngine, type LineSeriesLike } from "../charting/lightweight";
import { DEFAULT_SETTINGS, closes, ema, rsi, sma, vwap, type Series } from "../utils/indicators";
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
}

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
  private data: ChartBar[] = [];
  private key = "";
  private readonly unsubs: (() => void)[] = [];

  constructor(element: HTMLElement, private opts: ChartCoreOptions) {
    const theme = chartThemeOptions(opts.palette, opts.grid);
    const daily = DAILY_OR_LONGER.has(opts.timeframe ?? "5min");
    this.chart = createChart(element, {
      ...theme,
      autoSize: true,
      // The TradingView attribution logo stays (the library's licence asks for it once per chart).
      layout: { ...theme.layout, fontFamily: "'JetBrains Mono', ui-monospace, monospace", fontSize: 11, attributionLogo: true },
      rightPriceScale: { ...theme.rightPriceScale, minimumWidth: 72 },
      timeScale: { ...theme.timeScale, timeVisible: !daily, secondsVisible: false, rightOffset: 4,
                   tickMarkFormatter: (t: Time, type: number) => formatTick(t as number, type, opts.zone) },
      localization: { timeFormatter: (t: Time) => crosshairTimeLabel(t as number, this.opts.zone, this.opts.timeframe) },
      // Touch as on ProChart (PW2): pinch to zoom, a one-finger drag pans while a vertical swipe still scrolls the page.
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
      kineticScroll: { touch: true, mouse: false },
    });
    const p = opts.palette;
    this.candles = this.chart.addSeries(CandlestickSeries, {
      ...candleOptions(p, this.candlesHidden()),
      // The price scale also takes in the level lines, so a stop or target away from the bars still shows.
      autoscaleInfoProvider: (base: () => AutoscaleInfo | null) => {
        const info = base();
        if (!info?.priceRange || this.levels.length === 0) return info;
        const prices = this.levels.map((l) => l.price);
        return { ...info, priceRange: { minValue: Math.min(info.priceRange.minValue, ...prices), maxValue: Math.max(info.priceRange.maxValue, ...prices) } };
      },
    });
    this.bars = this.chart.addSeries(BarSeries, { upColor: p.up, downColor: p.down, thinBars: false, priceLineVisible: false });
    this.line = this.chart.addSeries(LineSeries, { color: p.drawing, lineWidth: 2 });
    this.overlays = OVERLAYS_V2.map((_, i) => this.chart.addSeries(LineSeries, {
      color: p.ov[i], lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    }));
    this.engine = new LightweightEngine(this.chart, this.candles, (y) => this.candles.coordinateToPrice(y),
                                        { add: (pane, color, title) => this.addStudyLine(pane, color, title), remove: (s) => this.removeStudyLine(s) });
    this.engine.setDefaultDrawingColor(p.drawing);
    this.setPanes(opts.panes);
    const nearStart = (range: LogicalRange | null) => { if (range && range.from < 10) this.opts.onLoadOlder?.(); };
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(nearStart);
    this.unsubs.push(() => this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(nearStart));
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

  /** The chart theme or the grid changed: options in place, no rebuild (the visible range is never touched). */
  applyTheme(palette: ChartPalette, grid: boolean): void {
    this.opts = { ...this.opts, palette, grid };
    applyChartTheme(this.chart, { candles: this.candles, bars: this.bars, line: this.line, rsi: this.rsi ?? undefined,
                                  rsiBands: this.rsiBands, overlays: this.overlays }, palette, grid, this.candlesHidden());
    this.engine.setDefaultDrawingColor(palette.drawing);
    this.levelLines.forEach((line, i) => line.applyOptions({ color: this.levelColour(this.levels[i].tone) }));
    if (this.levels.length > 0) this.candles.applyOptions(this.lastPriceLine());
    this.paintLabels();
    this.paintVolume(this.data.map((b) => toTime(b.timestamp)));      // volume carries its colour per bar
  }

  /** New bars. The same `key` (symbol, timeframe, type) keeps the trader's view, shifted by the bars added on the left
   * (older history loaded); a new key fits every bar. */
  setData(bars: ChartBar[], key: string): void {
    const prevFirst = this.data[0]?.timestamp;
    const keep = key === this.key && this.data.length > 0 ? this.chart.timeScale().getVisibleLogicalRange() : null;
    this.data = bars;
    this.key = key;
    this.render();
    if (bars.length === 0) return;
    if (!keep) { this.chart.timeScale().fitContent(); return; }
    const added = bars.findIndex((b) => b.timestamp === prevFirst);
    if (added > 0) this.chart.timeScale().setVisibleLogicalRange({ from: keep.from + added, to: keep.to + added });
  }

  private paintVolume(times: UTCTimestamp[]): void {
    const p = this.opts.palette;
    this.volume?.setData(this.data.map((b, i) => ({ time: times[i], value: b.volume || 0, color: b.close >= b.open ? p.volUp : p.volDown })));
  }

  private render(): void {
    const { palette: p, type, overlays } = this.opts;
    const times = this.data.map((b) => toTime(b.timestamp));
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
    for (const u of this.unsubs) u();
    this.engine.dispose();
    this.chart.remove();
  }
}
