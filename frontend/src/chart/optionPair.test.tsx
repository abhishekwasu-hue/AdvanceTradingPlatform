import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { CrosshairLink } from "../charting/crosshairLink";
import type { ChartEngine, EngineEvent } from "../charting/engine";
import { RangeLink } from "../charting/rangeLink";
import { headerFigures, optionRightOf, pairSymbol, shownSides, sidesOf } from "./optionPair";

vi.mock("./EmbeddedChartV2", () => ({
  default: (p: { symbol: string; toolbar?: boolean; timeframe?: string; emptyNote?: string }) =>
    <section data-stub-chart={p.symbol} data-toolbar={p.toolbar === false ? "off" : "on"} data-tf={p.timeframe} data-empty={p.emptyNote} />,
}));
vi.mock("./useMarketStatus", () => ({ useMarketStatus: () => ({ status: null, at: 0 }) }));
vi.mock("../components/chartHelpers", () => ({ useLiveLtp: () => ({ ltp: null, error: null }) }));
const { default: OptionPairChartV2 } = await import("./OptionPairChartV2");

/** CEPE-1 (owner 13 Nov): CE on top and the same-strike PE below in the option chart sheet. */
describe("CEPE-1: the same strike and expiry on the other side", () => {
  it("swaps CE / PE in the broker's spaced form and the exchange's compact form", () => {
    expect(pairSymbol("NIFTY 25050 CE 17 NOV 26", "NFO")).toBe("NIFTY 25050 PE 17 NOV 26");
    expect(pairSymbol("NIFTY 25050 PE 17 NOV 26", "NFO")).toBe("NIFTY 25050 CE 17 NOV 26");
    expect(pairSymbol("NIFTY26N0324500CE", "NFO")).toBe("NIFTY26N0324500PE");
    expect(pairSymbol("SENSEX 81500 PE 20 NOV 26", "BFO")).toBe("SENSEX 81500 CE 20 NOV 26");
  });
  it("keeps strike, expiry and underlying (only the right word changes)", () => {
    const ce = "BANKNIFTY 55000 CE 25 NOV 26";
    const pe = pairSymbol(ce, "NFO")!;
    expect(pe.replace(" PE ", " CE ")).toBe(ce);
    expect(pe.split(" ").filter((w) => w !== "PE")).toEqual(ce.split(" ").filter((w) => w !== "CE"));
  });
  it("is no option for a stock, an index, a future or a cash exchange", () => {
    expect(optionRightOf("RELIANCE", "NSE")).toBeNull();
    expect(optionRightOf("NIFTY 50", "NSE")).toBeNull();
    expect(optionRightOf("NIFTY 25NOV FUT", "NFO")).toBeNull();
    expect(optionRightOf("NIFTY 25050 CE 17 NOV 26", "NSE")).toBeNull();
    expect(optionRightOf("CESC", "NFO")).toBeNull();
    expect(pairSymbol("RELIANCE", "NSE")).toBeNull();
  });
  it("puts the CE first whichever side the sheet was opened for", () => {
    expect(sidesOf("NIFTY 25050 PE 17 NOV 26", "NFO")).toEqual({ CE: "NIFTY 25050 CE 17 NOV 26", PE: "NIFTY 25050 PE 17 NOV 26" });
    expect(sidesOf("nifty 25050 ce 17 nov 26", "nfo")).toEqual({ CE: "NIFTY 25050 CE 17 NOV 26", PE: "NIFTY 25050 PE 17 NOV 26" });
  });
});

describe("CEPE-1: the CE · PE · Both switch", () => {
  it("shows both charts by default, CE above PE, one control bar for both", () => {
    const html = renderToStaticMarkup(<OptionPairChartV2 symbol="NIFTY 25050 PE 17 NOV 26" exchange="NFO" timeframe="15min" />);
    expect(html).toContain('data-option-pair="both"');
    const ce = html.indexOf('data-stub-chart="NIFTY 25050 CE 17 NOV 26"');
    const pe = html.indexOf('data-stub-chart="NIFTY 25050 PE 17 NOV 26"');
    expect(ce).toBeGreaterThan(-1);
    expect(pe).toBeGreaterThan(ce);
    expect(html.match(/data-toolbar="on"/g)).toHaveLength(1);
    expect(html.match(/data-tf="15min"/g)).toHaveLength(2);   // one timeframe for both
    expect(html).toMatch(/role="radio" aria-checked="true" data-option="both"/);
    for (const word of ["Buy", "Sell", "Target", "Stop"]) expect(html).not.toContain(word);
  });
  it("one chosen side fills the sheet alone, with its own control bar", () => {
    expect(shownSides("both")).toEqual(["CE", "PE"]);
    expect(shownSides("CE")).toEqual(["CE"]);
    expect(shownSides("PE")).toEqual(["PE"]);
  });
  it("a side without candles says so in a plain sentence on its own half", () => {
    const html = renderToStaticMarkup(<OptionPairChartV2 symbol="NIFTY 25050 CE 17 NOV 26" exchange="NFO" />);
    expect(html).toContain("Your broker has no prices for NIFTY 25050 PE 17 NOV 26 on this timeframe yet.");
  });
  it("the chart panel uses it for an option contract only, and fills the sheet", () => {
    const src = readFileSync(new URL("../inplace/panels/ChartPanel.tsx", import.meta.url), "utf8");
    expect(src).toContain("optionRightOf(symbol, exchange)");
    expect(src).not.toContain('className="h-96"');
    expect(readFileSync(new URL("../inplace/panels.ts", import.meta.url), "utf8")).toMatch(/kind: "chart",[\s\S]{0,400}fill: true/);
  });
});

describe("CEPE-1: header figures", () => {
  const bar = (ts: string, close: number) => ({ timestamp: ts, open: close, high: close, low: close, close, volume: 0 });
  it("the change is from the last close of the trading day before, coloured by meaning", () => {
    const candles = [bar("2026-11-12T09:55:00Z", 200), bar("2026-11-13T03:45:00Z", 190), bar("2026-11-13T09:00:00Z", 181.6)];
    const f = headerFigures(candles, null, false);
    expect(f.last).toBe(181.6);
    expect(f.change).toBeCloseTo(-18.4);
    expect(f.pct).toBeCloseTo(-9.2);
    expect(f.meaning).toBe("down");
    expect(headerFigures(candles, 210, false).meaning).toBe("up");
  });
  it("a daily chart uses the previous bar; without an earlier close there is no change", () => {
    expect(headerFigures([bar("2026-11-12T00:00:00Z", 100), bar("2026-11-13T00:00:00Z", 110)], null, true).change).toBe(10);
    expect(headerFigures([bar("2026-11-13T04:00:00Z", 100), bar("2026-11-13T05:00:00Z", 110)], null, false).change).toBeNull();
    expect(headerFigures([], null, false)).toEqual({ last: null, change: null, pct: null, meaning: "flat" });
  });
});

class FakeEngine {
  listeners = new Set<(e: EngineEvent) => void>();
  crosshair: (number | null)[] = [];
  ranges: [number, number][] = [];
  on(l: (e: EngineEvent) => void) { this.listeners.add(l); return () => { this.listeners.delete(l); }; }
  emit(e: EngineEvent) { for (const l of this.listeners) l(e); }
  setCrosshair(t: number | null) { this.crosshair.push(t); }
  setVisibleTimeRange(from: number, to: number) { this.ranges.push([from, to]); this.emit({ type: "visibleRangeChanged", from, to }); }
}
const asEngine = (f: FakeEngine) => f as unknown as ChartEngine;

describe("CEPE-1: the two charts are linked", () => {
  it("scroll / zoom on one moves the other to the same time range, with no echo back", () => {
    const link = new RangeLink();
    const ce = new FakeEngine(), pe = new FakeEngine();
    link.register(0, asEngine(ce));
    link.register(1, asEngine(pe));
    ce.emit({ type: "visibleRangeChanged", from: 1000, to: 5000 });
    expect(pe.ranges).toEqual([[1000, 5000]]);
    expect(ce.ranges).toEqual([]);                       // the PE's report of the range it was given is not sent back
    pe.emit({ type: "visibleRangeChanged", from: 2000, to: 6000 });
    expect(ce.ranges).toEqual([[2000, 6000]]);
    link.register(1, null);                              // the PE hidden by the switch: nothing follows
    ce.emit({ type: "visibleRangeChanged", from: 3000, to: 7000 });
    expect(pe.ranges).toHaveLength(1);
  });
  it("a snapped echo (the library rounds the range to its bars) is not sent back either", () => {
    let t = 0;
    const link = new RangeLink(() => t);
    const ce = new FakeEngine(), pe = new FakeEngine();
    pe.setVisibleTimeRange = function (from: number, to: number) { this.ranges.push([from, to]); this.emit({ type: "visibleRangeChanged", from: from + 300, to }); };
    link.register(0, asEngine(ce));
    link.register(1, asEngine(pe));
    ce.emit({ type: "visibleRangeChanged", from: 1000, to: 5000 });
    expect(ce.ranges).toEqual([]);
    t = 1000;                                              // later: the PE's own scroll is followed again
    pe.emit({ type: "visibleRangeChanged", from: 1500, to: 5500 });
    expect(ce.ranges).toEqual([[1500, 5500]]);
  });
  it("the crosshair time follows between the two charts", () => {
    const link = new CrosshairLink((i) => [1 - i]);
    const ce = new FakeEngine(), pe = new FakeEngine();
    link.register(0, asEngine(ce));
    link.register(1, asEngine(pe));
    ce.emit({ type: "crosshair", time: 1234, price: 180, user: true });
    expect(pe.crosshair).toEqual([1234]);
    pe.emit({ type: "crosshair", time: null, price: null, user: true });
    expect(ce.crosshair).toEqual([null]);
  });
});
