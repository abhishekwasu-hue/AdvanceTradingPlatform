/**
 * CH1 (ADR-0023), "B-lite": `ChartEngine` over the lightweight-charts v4 chart that ProChart already draws.
 *
 * ProChart keeps rendering its candles, indicators, price lines, zones and markers exactly as before (no visible
 * change); this adapter adds what sits above the interface:
 * - drawings: `hline` as a price line; `trendline`, `ray` and `measure` as a two-point line. The other kinds are kept
 *   in the layout (nothing is lost) and drawn once the v5 primitives core lands (CH2) - `supports()` says which;
 * - layers: their lines as price lines (markers stay ProChart's job until CH2 merges them);
 * - events: crosshair, click, visible range.
 * Studies are recorded in the layout; ProChart draws its own until CH6 moves them onto ScreenQL.
 */
import { LineSeries, type UTCTimestamp } from "lightweight-charts";
import type { DrawingKind, DrawingV1 } from "./drawings";
import type { ChartEngine, EngineBar, EngineEvent, EngineLayout, LayerData, StudySpec } from "./engine";

// The slice of the lightweight-charts API this adapter uses (structural, so tests can pass a fake).
type PriceLineLike = object;
interface PriceLineOptionsLike { price: number; color: string; lineWidth: 1 | 2 | 3 | 4; lineStyle: number; axisLabelVisible: boolean; title: string }
export interface CandleSeriesLike {
  setData(data: unknown[]): void;
  update(bar: unknown): void;
  createPriceLine(options: PriceLineOptionsLike): PriceLineLike;
  removePriceLine(line: PriceLineLike): void;
}
export interface LineSeriesLike { setData(data: { time: UTCTimestamp; value: number }[]): void }
interface MouseParamsLike { time?: unknown; point?: { x: number; y: number } }
export interface ChartLike {
  addSeries(definition: typeof LineSeries, options: Record<string, unknown>): LineSeriesLike;
  removeSeries(series: LineSeriesLike): void;
  subscribeCrosshairMove(handler: (p: MouseParamsLike) => void): void;
  unsubscribeCrosshairMove(handler: (p: MouseParamsLike) => void): void;
  subscribeClick(handler: (p: MouseParamsLike) => void): void;
  unsubscribeClick(handler: (p: MouseParamsLike) => void): void;
  timeScale(): {
    subscribeVisibleTimeRangeChange(handler: (r: { from: unknown; to: unknown } | null) => void): void;
    unsubscribeVisibleTimeRangeChange(handler: (r: { from: unknown; to: unknown } | null) => void): void;
  };
}

const LINE_STYLE = { solid: 0, dotted: 1, dashed: 2 } as const;
const SUPPORTED: ReadonlySet<DrawingKind> = new Set<DrawingKind>(["hline", "trendline", "ray", "measure"]);
const toSec = (iso: string) => Math.floor(Date.parse(iso) / 1000);

type Rendered = { kind: "priceLine"; line: PriceLineLike } | { kind: "series"; series: LineSeriesLike } | { kind: "none" };

export class LightweightEngine implements ChartEngine {
  readonly name = "lightweight-v4";
  private symbol: string | null = null;
  private timeframe: string | null = null;
  private studies = new Map<string, StudySpec>();
  private drawings = new Map<string, DrawingV1>();
  private rendered = new Map<string, Rendered>();
  private layers = new Map<string, PriceLineLike[]>();
  private listeners = new Set<(e: EngineEvent) => void>();
  private unsubs: (() => void)[] = [];
  private disposed = false;

  constructor(private readonly chart: ChartLike, private readonly candles: CandleSeriesLike, private readonly priceOf?: (y: number) => number | null) {
    const cross = (p: MouseParamsLike) => this.emit({ type: "crosshair", time: typeof p.time === "number" ? p.time : null, price: this.price(p) });
    const click = (p: MouseParamsLike) => this.emit({ type: "click", time: typeof p.time === "number" ? p.time : null, price: this.price(p) });
    const range = (r: { from: unknown; to: unknown } | null) => {
      if (r && typeof r.from === "number" && typeof r.to === "number") this.emit({ type: "visibleRangeChanged", from: r.from, to: r.to });
    };
    chart.subscribeCrosshairMove(cross);
    chart.subscribeClick(click);
    chart.timeScale().subscribeVisibleTimeRangeChange(range);
    this.unsubs.push(() => chart.unsubscribeCrosshairMove(cross), () => chart.unsubscribeClick(click),
                     () => chart.timeScale().unsubscribeVisibleTimeRangeChange(range));
  }

  private price(p: MouseParamsLike): number | null {
    return p.point && this.priceOf ? this.priceOf(p.point.y) : null;
  }

  private emit(event: EngineEvent) {
    for (const l of this.listeners) l(event);
  }

  setSymbol(symbol: string) { this.symbol = symbol; }
  setTimeframe(timeframe: string) { this.timeframe = timeframe; }
  setBars(bars: EngineBar[]) { this.candles.setData(bars.map((b) => ({ ...b }))); }
  appendBar(bar: EngineBar) { this.candles.update({ ...bar }); }
  addStudy(spec: StudySpec) { this.studies.set(spec.id, spec); }
  removeStudy(id: string) { this.studies.delete(id); }
  supports(kind: DrawingKind) { return SUPPORTED.has(kind); }

  addDrawing(id: string, drawing: DrawingV1) {
    this.removeDrawing(id);
    this.drawings.set(id, drawing);
    this.rendered.set(id, this.draw(drawing));
  }

  updateDrawing(id: string, drawing: DrawingV1) {
    this.addDrawing(id, drawing);
    this.emit({ type: "drawingChanged", id, drawing });
  }

  removeDrawing(id: string) {
    const r = this.rendered.get(id);
    if (r?.kind === "priceLine") this.candles.removePriceLine(r.line);
    if (r?.kind === "series") this.chart.removeSeries(r.series);
    this.rendered.delete(id);
    this.drawings.delete(id);
  }

  private draw(d: DrawingV1): Rendered {
    const style = d.style ?? {};
    const color = style.color ?? "#2962ff";
    const width = Math.min(4, Math.max(1, style.width ?? 1)) as 1 | 2 | 3 | 4;
    const lineStyle = LINE_STYLE[style.line ?? "solid"];
    if (d.kind === "hline" && d.anchors[0]?.p != null) {
      return { kind: "priceLine", line: this.candles.createPriceLine({ price: d.anchors[0].p, color, lineWidth: width, lineStyle, axisLabelVisible: true, title: d.text ?? "" }) };
    }
    if ((d.kind === "trendline" || d.kind === "ray" || d.kind === "measure") && d.anchors.length === 2) {
      const pts = d.anchors.map((a) => ({ time: toSec(a.t ?? "") as UTCTimestamp, value: a.p ?? NaN }))
        .filter((pt) => Number.isFinite(pt.time) && Number.isFinite(pt.value)).sort((x, y) => x.time - y.time);
      if (pts.length !== 2 || pts[0].time === pts[1].time) return { kind: "none" };
      const series = this.chart.addSeries(LineSeries, { color, lineWidth: width, lineStyle, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      series.setData(pts);
      return { kind: "series", series };
    }
    return { kind: "none" };                                   // kept in the layout; drawn by the CH2 primitives core
  }

  setLayer(id: string, data: LayerData | null) {
    for (const line of this.layers.get(id) ?? []) this.candles.removePriceLine(line);
    this.layers.delete(id);
    if (!data) return;
    const made = (data.lines ?? []).filter((l) => Number.isFinite(l.price)).map((l) => this.candles.createPriceLine({
      price: l.price, color: l.color ?? "#64748b", lineWidth: 1, lineStyle: LINE_STYLE[l.style ?? "dashed"], axisLabelVisible: false, title: l.title ?? "",
    }));
    this.layers.set(id, made);
  }

  serialize(): EngineLayout {
    return { engine: this.name, symbol: this.symbol, timeframe: this.timeframe, studies: [...this.studies.values()],
             drawings: Object.fromEntries(this.drawings) };
  }

  deserialize(layout: EngineLayout) {
    for (const id of [...this.drawings.keys()]) this.removeDrawing(id);
    this.symbol = layout.symbol;
    this.timeframe = layout.timeframe;
    this.studies = new Map(layout.studies.map((s) => [s.id, s]));
    for (const [id, d] of Object.entries(layout.drawings)) this.addDrawing(id, d);
  }

  on(listener: (event: EngineEvent) => void) {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    for (const u of this.unsubs) u();
    for (const id of [...this.drawings.keys()]) this.removeDrawing(id);
    for (const id of [...this.layers.keys()]) this.setLayer(id, null);
    this.listeners.clear();
  }
}
