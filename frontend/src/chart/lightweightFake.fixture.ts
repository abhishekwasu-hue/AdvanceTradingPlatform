/**
 * Test only: a stand-in for the slice of lightweight-charts v5 the v2 chart uses (`vi.mock("lightweight-charts", ...)`),
 * with native panes (a series added past the last pane opens a new one; a pane left empty is removed, as the library
 * does) and a record of every option change, so a test can tell an in-place change from a rebuild.
 */
type Options = Record<string, unknown>;

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
  removed = false;
  private rangeHandlers: ((r: unknown) => void)[] = [];
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
  };
  constructor(public options: Options) {}
  applyOptions(o: Options) { this.applied.push(o); this.calls.push("applyOptions"); }
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
  subscribeCrosshairMove() {}
  unsubscribeCrosshairMove() {}
  subscribeClick() {}
  unsubscribeClick() {}
  paneSize() { return { width: 400, height: 300 }; }
  priceScale() { return { width: () => 0 }; }
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
