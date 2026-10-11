/**
 * CH1/CH2 (ADR-0023): `ChartEngine` over the lightweight-charts v5 chart that ProChart already draws.
 *
 * ProChart keeps rendering its candles, indicators, price lines, zones and markers exactly as before (no visible
 * change); this adapter adds what sits above the interface:
 * - drawings: `hline` as a price line (it keeps its axis label); every other `drawing/1` kind as a v5 series primitive
 *   (CH2b, `primitives.ts` + `geometry.ts`), placed by time/price so it follows scroll, zoom and timeframe changes;
 * - layers: their lines as price lines (markers stay ProChart's job);
 * - events: crosshair, click, visible range.
 * Studies are recorded in the layout; ProChart draws its own until CH6 moves them onto ScreenQL.
 */
import { ANCHOR_RULES, type DrawingKind, type DrawingV1 } from "./drawings";
import type { ChartEngine, EngineBar, EngineEvent, EngineLayout, LayerData, StudySpec } from "./engine";
import { timeToLogical, type Converters } from "./geometry";
import { DrawingPrimitive } from "./primitives";

// The slice of the lightweight-charts API this adapter uses (structural, so tests can pass a fake).
type PriceLineLike = object;
interface PriceLineOptionsLike { price: number; color: string; lineWidth: 1 | 2 | 3 | 4; lineStyle: number; axisLabelVisible: boolean; title: string }
export interface CandleSeriesLike {
  setData(data: unknown[]): void;
  update(bar: unknown): void;
  createPriceLine(options: PriceLineOptionsLike): PriceLineLike;
  removePriceLine(line: PriceLineLike): void;
  priceToCoordinate(price: number): number | null;
  data(): readonly { time: unknown }[];
  subscribeDataChanged(handler: () => void): void;
  unsubscribeDataChanged(handler: () => void): void;
  attachPrimitive(primitive: DrawingPrimitive): void;
  detachPrimitive(primitive: DrawingPrimitive): void;
}
interface MouseParamsLike { time?: unknown; point?: { x: number; y: number } }
export interface ChartLike {
  subscribeCrosshairMove(handler: (p: MouseParamsLike) => void): void;
  unsubscribeCrosshairMove(handler: (p: MouseParamsLike) => void): void;
  subscribeClick(handler: (p: MouseParamsLike) => void): void;
  unsubscribeClick(handler: (p: MouseParamsLike) => void): void;
  timeScale(): {
    subscribeVisibleTimeRangeChange(handler: (r: { from: unknown; to: unknown } | null) => void): void;
    unsubscribeVisibleTimeRangeChange(handler: (r: { from: unknown; to: unknown } | null) => void): void;
    logicalToCoordinate(logical: number): number | null;
  };
}

const LINE_STYLE = { solid: 0, dotted: 1, dashed: 2 } as const;
const SUPPORTED: ReadonlySet<DrawingKind> = new Set(Object.keys(ANCHOR_RULES) as DrawingKind[]);
const timeSec = (t: unknown): number => (typeof t === "number" ? t : typeof t === "string" ? Math.floor(Date.parse(t) / 1000) : NaN);

type Rendered = { kind: "priceLine"; line: PriceLineLike } | { kind: "primitive"; primitive: DrawingPrimitive } | { kind: "none" };

export class LightweightEngine implements ChartEngine {
  readonly name = "lightweight-v5";
  private symbol: string | null = null;
  private timeframe: string | null = null;
  private studies = new Map<string, StudySpec>();
  private drawings = new Map<string, DrawingV1>();
  private rendered = new Map<string, Rendered>();
  private layers = new Map<string, PriceLineLike[]>();
  private listeners = new Set<(e: EngineEvent) => void>();
  private unsubs: (() => void)[] = [];
  private disposed = false;
  private barTimes: number[] | null = null;                    // the candles' times (epoch s), rebuilt when the data changes

  constructor(private readonly chart: ChartLike, private readonly candles: CandleSeriesLike, private readonly priceOf?: (y: number) => number | null) {
    const cross = (p: MouseParamsLike) => this.emit({ type: "crosshair", time: typeof p.time === "number" ? p.time : null, price: this.price(p) });
    const click = (p: MouseParamsLike) => this.emit({ type: "click", time: typeof p.time === "number" ? p.time : null, price: this.price(p) });
    const range = (r: { from: unknown; to: unknown } | null) => {
      if (r && typeof r.from === "number" && typeof r.to === "number") this.emit({ type: "visibleRangeChanged", from: r.from, to: r.to });
    };
    const dataChanged = () => { this.barTimes = null; };
    candles.subscribeDataChanged(dataChanged);
    this.unsubs.push(() => candles.unsubscribeDataChanged(dataChanged));
    chart.subscribeCrosshairMove(cross);
    chart.subscribeClick(click);
    chart.timeScale().subscribeVisibleTimeRangeChange(range);
    this.unsubs.push(() => chart.unsubscribeCrosshairMove(cross), () => chart.unsubscribeClick(click),
                     () => chart.timeScale().unsubscribeVisibleTimeRangeChange(range));
  }

  // Assumes the candles carry every time point of the main chart's time scale (true in ProChart: all main-chart series
  // share the candle times, gaps as whitespace), so an index into the candle data is the chart's logical index.
  private times(): number[] {
    if (!this.barTimes) this.barTimes = this.candles.data().map((b) => timeSec(b.time)).filter(Number.isFinite);
    return this.barTimes;
  }

  /** Converters for one paint: anchor time -> fractional bar index -> x; price -> y on the candle scale. */
  private converters(width: number, height: number): Converters | null {
    const times = this.times();
    if (times.length === 0) return null;
    const scale = this.chart.timeScale();
    return {
      x: (t) => {
        const logical = timeToLogical(times, t);
        return logical == null ? null : scale.logicalToCoordinate(logical);
      },
      y: (p) => this.candles.priceToCoordinate(p),
      width, height,
    };
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
    const current = this.rendered.get(id);
    if (current?.kind === "primitive" && drawing.kind !== "hline") {     // same primitive, new anchors: no re-attach
      this.drawings.set(id, drawing);
      current.primitive.setDrawing(drawing);
      return;
    }
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
    if (r?.kind === "primitive") this.candles.detachPrimitive(r.primitive);
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
    if (d.kind === "hline") return { kind: "none" };
    const primitive = new DrawingPrimitive(d, (w, h) => this.converters(w, h));
    this.candles.attachPrimitive(primitive);
    return { kind: "primitive", primitive };
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
