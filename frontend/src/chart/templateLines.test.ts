import { describe, expect, it } from "vitest";
import type { OHLCVBar, Trade } from "../types";
import { exitMarkerText, templateLines, templateParams } from "./templateLines";
import { tradeLayer } from "./BacktestChartV2";

/** BT-DRAW (owner 18 Nov): the test chart draws the template's own lines only. Made-up bars. */
const bars: OHLCVBar[] = Array.from({ length: 80 }, (_, i) => {
  const c = 100 + Math.sin(i / 5) * 5 + i * 0.1;
  return { timestamp: new Date(Date.UTC(2026, 9, 1, 4, 0) + i * 900_000).toISOString(), open: c - 0.5, high: c + 1, low: c - 1, close: c, volume: 1000 };
});
const titles = (id: string, p: Record<string, unknown> = {}) => templateLines(id, p, bars).studies.map((s) => s.line.title);

describe("each template draws what it reads, never a default EMA pair", () => {
  it("a price-structure template draws no lines", () => {
    for (const id of ["A1", "A5", "B1", "C1"]) expect(templateLines(id, {}, bars)).toEqual({ studies: [], named: [] });
  });
  it("moving-average templates draw their own averages at their own periods", () => {
    expect(titles("D2", { ma_type: "EMA", fast: 9, slow: 21 })).toEqual(["EMA 9", "EMA 21"]);
    expect(titles("D2", { ma_type: "SMA", fast: 10, slow: 30, use_trend: true, trend_period: 50 })).toEqual(["SMA 10", "SMA 30", "SMA 50"]);
    expect(titles("D4", { ma_type: "sma", ma_n: 50 })).toEqual(["SMA 50"]);
    expect(titles("H10", { ema_n: 9 })).toEqual(["EMA 9"]);
  });
  it("oscillators go to their own pane; bands and channels on the price", () => {
    const e1 = templateLines("E1", { rsi_period: 14, trend_filter: "average", ma_type: "EMA", trend_period: 50, context_timeframe: "1d" }, bars);
    expect(e1.studies.map((s) => [s.line.title, s.line.pane])).toEqual([["RSI 14", "own"]]);
    expect(e1.named).toEqual(["EMA 50 on the Daily chart"]);
    expect(titles("E3", { bb_n: 20, bb_k: 2, regime: "adx", adx_period: 14 })).toEqual(["Bollinger 20 / 2", "Middle", "Lower", "ADX 14"]);
    expect(titles("D6", { fast: 12, slow: 26, signal: 9 })).toEqual(["MACD 12 / 26", "Signal 9"]);
    expect(titles("E4", { k_period: 14, smooth: 3, d_period: 3 })).toEqual(["Stochastic %K 14", "%D 3"]);
    expect(titles("B5", { n: 20 })).toEqual(["Donchian 20 high", "Donchian 20 low"]);
    expect(titles("D1", { period: 10, multiplier: 3 })).toEqual(["Supertrend 10 / 3"]);
    expect(templateLines("D7", {}, bars).named).toEqual(["Parabolic SAR 0.02 / 0.02 / 0.2"]);
  });
  it("lines carry only finite values on the bars' own times (no look-ahead: a value needs its window)", () => {
    const [ema] = templateLines("D2", { fast: 9, slow: 21 }, bars).studies;
    expect(ema.line.data.every((d) => Number.isFinite(d.value))).toBe(true);
    expect(ema.line.data[0].time).toBeGreaterThanOrEqual(Math.floor(Date.parse(bars[0].timestamp) / 1000));
    const [hi] = templateLines("B5", { n: 20 }, bars).studies;
    expect(hi.line.data[0].time).toBe(Math.floor(Date.parse(bars[20].timestamp) / 1000));
    expect(hi.line.data[0].value).toBe(Math.max(...bars.slice(0, 20).map((b) => b.high)));
  });
  it("settings: the defaults, then the trader's own", () => {
    expect(templateParams([{ key: "fast", default: 9 }, { key: "slow", default: 21 }], { slow: 50 })).toEqual({ fast: 9, slow: 50 });
  });
});

describe("trades on the chart say why they closed and where each idea was wrong", () => {
  it("exit marker text", () => {
    expect(exitMarkerText("Template exit", "-9")).toBe("Template exit -9");
    expect(exitMarkerText(null, "120")).toBe("Exit 120");
    expect(exitMarkerText("Square-off (15:15 IST) after a long session", "5")).toBe("Square-off (15:15 IST… 5");
  });
  it("one dashed stop line per closed trade, from its entry to its exit", () => {
    const t = (over: Partial<Trade>): Trade => ({ symbol: "DEMO", strategy_id: "x", direction: "LONG", entry_time: bars[10].timestamp, entry_price: 101,
      quantity: 1, stop_loss: 99, target1: 0, target2: null, exit_time: bars[20].timestamp, exit_price: 102, exit_reason: "x", pnl: 1, charges: 0, ...over });
    const layer = tradeLayer([t({}), t({ exit_time: null }), t({ stop_loss: 0 })]);
    expect(layer.segments).toHaveLength(1);
    expect(layer.segments?.[0]).toMatchObject({ p1: 99, p2: 99, style: "dashed" });
  });
  it("a level-gate trade's area is boxed from its setup to its exit, in the side's colour (BT-DRAW 2c)", () => {
    const t: Trade = { symbol: "DEMO", strategy_id: "x", direction: "SHORT", entry_time: bars[10].timestamp, entry_price: 101, quantity: 1,
      stop_loss: 103, target1: 0, target2: null, exit_time: bars[20].timestamp, exit_price: 100, exit_reason: "x", pnl: 1, charges: 0,
      setup_time: bars[7].timestamp, setup_area_low: 101.5, setup_area_high: 102.5 };
    const box = (tradeLayer([t]).segments ?? []).filter((x) => x.role);
    expect(box).toHaveLength(4);
    expect(box.every((x) => x.role === "supply")).toBe(true);
    expect(box[0]).toMatchObject({ p1: 102.5, p2: 102.5, t1: Math.floor(Date.parse(bars[7].timestamp) / 1000) });
    expect((tradeLayer([{ ...t, setup_area_low: null }]).segments ?? []).filter((x) => x.role)).toHaveLength(0);
  });
});
