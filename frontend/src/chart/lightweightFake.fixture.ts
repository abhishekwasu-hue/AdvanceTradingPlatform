/**
 * Test only: a stand-in for the slice of lightweight-charts v5 the v2 chart uses (`vi.mock("lightweight-charts", ...)`),
 * with native panes (a series added past the last pane opens a new one; a pane left empty is removed, as the library
 * does) and a record of every option change, so a test can tell an in-place change from a rebuild.
 * Chart review (first PR): the price scale (`options / applyOptions / setAutoScale / getVisibleRange / setVisibleRange`),
 * the time scale's scroll position (`scrollPosition / scrollToPosition / scrollToRealTime / resetTimeScale`), the
 * crosshair handlers and `merged()` (the chart's options as the library would hold them) for the gesture tests.
 */
type Options = Record<string, unknown>;

const isObject = (v: unknown): v is Options => typeof v === "object" && v !== null && !Array.isArray(v);
/** A deep merge like the library's `applyOptions` (objects merge, everything else replaces). */
export function deepMerge(base: Options, change: Options): Options {
  const out: Options = { ...base };
  for (const [k, v] of Object.entries(change)) out[k] = isObject(v) && isObject(out[k]) ? deepMerge(out[k] as Options, v) : v;
  return out;
}

export class FakePriceScale {
  opts: Options = { autoScale: true, mode: 0, invertScale: false };
  /** The visible price range; the library recomputes it while auto-scale is on, the fake keeps it. */
  range: { from: number; to: number } | null = { from: 90, to: 110 };
  constructor(private readonly chart: FakeChart, readonly id: string) {}
  width() { return 0; }
  options() { return this.opts as Options & { autoScale: boolean; mode: number }; }
  applyOptions(o: Options) { this.opts = deepMerge(this.opts, o); this.chart.calls.push(`priceScale.applyOptions:${this.id}`); }
  setAutoScale(on: boolean) { this.applyOptions({ autoScale: on }); }
  getVisibleRange() { return this.range; }
  /** As the library: a range set by hand turns auto-scale off. */
  setVisibleRange(r: { from: number; to: number }) { this.setAutoScale(false); this.range = { ...r }; this.chart.calls.push(`priceScale.setVisibleRange:${this.id}`); }
}

export class FakePriceLine {
  constructor(public options: Options) {}
  applyOptions(o: Options) { this.options = { ...this.options, ...o }; }
}

export class FakePane {
  stretch = 1;
  constructor(private readonly chart: FakeChart, public series: FakeSeries[] = []) {}
  getHeight() { return 300; }
  paneIndex() { return this.chart.paneList.indexOf(this); }
  moveTo(index: number) {
    const list = this.chart.paneList;
    list.splice(list.indexOf(this), 1);
    list.splice(index, 0, this);
  }
  setStretchFactor(f: number) { this.stretch = f; }
  getSeries() { return this.series; }
}

export class FakeSeries {
  rows: unknown[] = [];
  priceLines: FakePriceLine[] = [];
  constructor(private readonly chart: FakeChart, readonly type: string, public options: Options) {}
  setData(rows: unknown[]) { this.rows = rows; }
  update(row: unknown) { this.rows = [...this.rows, row]; }
  data() { return this.rows as { time: unknown }[]; }
  applyOptions(o: Options) { this.options = { ...this.options, ...o }; this.chart.calls.push(`series.applyOptions:${this.type}`); }
  createPriceLine(o: Options) { const l = new FakePriceLine(o); this.priceLines.push(l); return l; }
  removePriceLine(l: FakePriceLine) { this.priceLines = this.priceLines.filter((x) => x !== l); }
  priceToCoordinate(p: number) { return 1000 - p; }
  coordinateToPrice(y: number) { return 1000 - y; }
  priceFormatter() { return { format: (p: number) => p.toFixed(2) }; }
  subscribeDataChanged() {}
  unsubscribeDataChanged() {}
  attachPrimitive() {}
  detachPrimitive() {}
  getPane() { return this.chart.paneList.find((p) => p.series.includes(this)) as FakePane; }
}

export class FakeChart {
  calls: string[] = [];
  applied: Options[] = [];
  paneList: FakePane[] = [new FakePane(this)];
  range: { from: number; to: number } | null = { from: 5, to: 50 };
  /** Bars from the right edge to the latest bar (the library's `scrollPosition`): the default right offset is 4. */
  position = 4;
  removed = false;
  timeOptions: Options = { rightOffset: 4, barSpacing: 6 };
  private rangeHandlers: ((r: unknown) => void)[] = [];
  crosshairHandlers: ((p: unknown) => void)[] = [];
  clickHandlers: ((p: unknown) => void)[] = [];
  private readonly scales = new Map<string, FakePriceScale>();
  private readonly scale = {
    subscribeVisibleLogicalRangeChange: (h: (r: unknown) => void) => { this.rangeHandlers.push(h); },
    unsubscribeVisibleLogicalRangeChange: (h: (r: unknown) => void) => { this.rangeHandlers = this.rangeHandlers.filter((x) => x !== h); },
    subscribeVisibleTimeRangeChange: () => {},
    unsubscribeVisibleTimeRangeChange: () => {},
    getVisibleLogicalRange: () => this.range,
    setVisibleLogicalRange: (r: { from: number; to: number }) => { this.range = r; this.calls.push("setVisibleLogicalRange"); },
    fitContent: () => { this.calls.push("fitContent"); },
    logicalToCoordinate: (l: number) => l * 10,
    coordinateToLogical: (x: number) => x / 10,
    getVisibleRange: () => this.range,
    scrollPosition: () => this.position,
    scrollToPosition: (p: number) => { this.position = p; this.calls.push("scrollToPosition"); },
    scrollToRealTime: () => { this.position = this.timeOptions.rightOffset as number; this.calls.push("scrollToRealTime"); },
    resetTimeScale: () => { this.position = this.timeOptions.rightOffset as number; this.calls.push("resetTimeScale"); },
    options: () => this.timeOptions,
    applyOptions: (o: Options) => { this.timeOptions = deepMerge(this.timeOptions, o); this.calls.push("timeScale.applyOptions"); },
  };
  constructor(public options: Options) {}
  applyOptions(o: Options) { this.applied.push(o); this.calls.push("applyOptions"); }
  /** The chart's options now: the creation options with every `applyOptions` merged in, as the library holds them. */
  merged(): Options { return this.applied.reduce<Options>((acc, o) => deepMerge(acc, o), this.options); }
  addSeries(def: { type: string }, options: Options = {}, pane = 0) {
    const s = new FakeSeries(this, def.type, options);
    if (pane >= this.paneList.length) this.paneList.push(new FakePane(this));
    this.paneList[Math.min(pane, this.paneList.length - 1)].series.push(s);
    this.calls.push(`addSeries:${def.type}`);
    return s;
  }
  removeSeries(s: FakeSeries) {
    const pane = s.getPane();
    pane.series = pane.series.filter((x) => x !== s);
    if (pane.series.length === 0 && pane.paneIndex() > 0) this.paneList.splice(pane.paneIndex(), 1);
    this.calls.push(`removeSeries:${s.type}`);
  }
  panes() { return [...this.paneList]; }
  /** The series types of each pane, top to bottom. */
  layout() { return this.paneList.map((p) => p.series.map((s) => s.type)); }
  series(type: string) { return this.paneList.flatMap((p) => p.series).filter((s) => s.type === type); }
  scrollTo(r: { from: number; to: number }) { this.range = r; for (const h of this.rangeHandlers) h(r); }
  timeScale() { return this.scale; }
  subscribeCrosshairMove(h: (p: unknown) => void) { this.crosshairHandlers.push(h); }
  unsubscribeCrosshairMove(h: (p: unknown) => void) { this.crosshairHandlers = this.crosshairHandlers.filter((x) => x !== h); }
  /** A crosshair move as the library reports it (`time`, `point`, `seriesData`, ...). */
  moveCrosshair(p: unknown) { for (const h of this.crosshairHandlers) h(p); }
  subscribeClick(h: (p: unknown) => void) { this.clickHandlers.push(h); }
  unsubscribeClick(h: (p: unknown) => void) { this.clickHandlers = this.clickHandlers.filter((x) => x !== h); }
  paneSize() { return { width: 400, height: 300 }; }
  priceScale(id = "right") {
    let scale = this.scales.get(id);
    if (!scale) { scale = new FakePriceScale(this, id); this.scales.set(id, scale); }
    return scale;
  }
  /** Scrolls the time scale to a position (bars from the right edge) and tells the range subscribers. */
  scrollToBars(position: number) { this.position = position; for (const h of this.rangeHandlers) h(this.range); }
  remove() { this.removed = true; this.calls.push("remove"); }
}

export const made: FakeChart[] = [];
export function createChart(_el: unknown, options: Options) {
  const c = new FakeChart(options);
  made.push(c);
  return c;
}
export const CandlestickSeries = { type: "Candlestick" };
export const BarSeries = { type: "Bar" };
export const LineSeries = { type: "Line" };
export const HistogramSeries = { type: "Histogram" };
export const AreaSeries = { type: "Area" };
export const ColorType = { Solid: "solid" };
export const CrosshairMode = { Normal: 0 };
export const LineStyle = { Solid: 0, Dotted: 1, Dashed: 2 };
export const PriceScaleMode = { Normal: 0, Logarithmic: 1, Percentage: 2, IndexedTo100: 3 };
export const TrackingModeExitMode = { OnTouchEnd: 0, OnNextTap: 1 };
