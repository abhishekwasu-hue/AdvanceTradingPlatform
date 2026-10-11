import { describe, expect, it } from "vitest";
import type { OHLCVBar } from "../types";
import fixture from "./__fixtures__/indicator_parity.json";
import { adx, atr, bollinger, closes, ema, rsi, sma, supertrend, vwap, type Series } from "./indicators";

/**
 * Z10-a (U3 G1): the Pro Chart must draw what the engine computes. The fixture holds generated bars and the backend's
 * values for them (app/indicators, written by backend/tests/test_z10a_chart_parity.py); every chart indicator must
 * match it bar for bar, warm-up included.
 */
const bars = fixture.bars as OHLCVBar[];
const want = fixture.series as Record<string, (number | null)[]>;

function expectSeries(got: Series, expected: (number | null)[], name: string) {
  expect(got.length, name).toBe(expected.length);
  got.forEach((g, i) => {
    const w = expected[i];
    if (w == null) {
      expect(g, `${name} at bar ${i}: the backend has no value yet`).toBeNull();
    } else {
      expect(g, `${name} at bar ${i}: the backend has ${w}`).not.toBeNull();
      expect(Math.abs((g as number) - w), `${name} at bar ${i}: ${g} vs ${w}`).toBeLessThanOrEqual(1e-9 * Math.max(1, Math.abs(w)));
    }
  });
}

describe("chart indicators match the backend (Z10-a parity fixture)", () => {
  const close = closes(bars);
  it("EMA, SMA, RSI, ATR", () => {
    expectSeries(ema(close, 9), want.ema_9, "EMA 9");
    expectSeries(ema(close, 21), want.ema_21, "EMA 21");
    expectSeries(sma(close, 20), want.sma_20, "SMA 20");
    expectSeries(rsi(close, 14), want.rsi_14, "RSI 14");
    expectSeries(atr(bars, 14), want.atr_14, "ATR 14");
  });
  it("ADX and the directional indicators", () => {
    const a = adx(bars, 14);
    expectSeries(a.plusDi, want.plus_di_14, "+DI 14");
    expectSeries(a.minusDi, want.minus_di_14, "-DI 14");
    expectSeries(a.adx, want.adx_14, "ADX 14");
  });
  it("Supertrend line and direction", () => {
    const s = supertrend(bars, 10, 3);
    expectSeries(s.line, want.supertrend_10_3, "Supertrend");
    expect(s.direction).toEqual(want.supertrend_10_3_direction);
  });
  it("Bollinger bands", () => {
    const b = bollinger(close, 20, 2);
    expectSeries(b.mid, want.bb_mid_20_2, "BB mid");
    expectSeries(b.upper, want.bb_upper_20_2, "BB upper");
    expectSeries(b.lower, want.bb_lower_20_2, "BB lower");
  });
  it("session VWAP (IST day)", () => {
    expectSeries(vwap(bars), want.vwap, "VWAP");
  });
});
