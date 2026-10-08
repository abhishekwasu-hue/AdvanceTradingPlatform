import { describe, expect, it } from "vitest";
import type { OHLCVBar } from "../types";
import { SESSION_BARS, generateSampleCandles, resampleByFactor } from "./sampleData";

/** Groups minute bars by IST date into daily OHLC. */
function days(bars: OHLCVBar[]) {
  const out = new Map<string, { open: number; high: number; low: number; close: number; n: number }>();
  for (const b of bars) {
    const d = new Date(new Date(b.timestamp).getTime() + 330 * 60_000).toISOString().slice(0, 10);
    const cur = out.get(d);
    if (!cur) out.set(d, { open: b.open, high: b.high, low: b.low, close: b.close, n: 1 });
    else { cur.high = Math.max(cur.high, b.high); cur.low = Math.min(cur.low, b.low); cur.close = b.close; cur.n += 1; }
  }
  return [...out.values()];
}

describe("sample candles (P0.10)", () => {
  const bars = generateSampleCandles(SESSION_BARS * 12, 24_000, 11, { end: new Date("2026-10-08T12:00:00Z") });

  it("only has bars inside the 09:15-15:29 IST session on weekdays, 375 a day", () => {
    for (const b of bars) {
      const ist = new Date(new Date(b.timestamp).getTime() + 330 * 60_000);
      const minute = ist.getUTCHours() * 60 + ist.getUTCMinutes();
      expect(minute).toBeGreaterThanOrEqual(9 * 60 + 15);
      expect(minute).toBeLessThanOrEqual(15 * 60 + 29);
      expect([0, 6]).not.toContain(ist.getUTCDay());
    }
    expect(days(bars).every((d) => d.n === SESSION_BARS)).toBe(true);
  });

  it("moves like an index day: open-to-close within ~3%, ranges and pivot levels a few percent away at most", () => {
    const ds = days(bars);
    const moves = ds.map((d) => Math.abs(d.close / d.open - 1) * 100);
    const ranges = ds.map((d) => (d.high / d.low - 1) * 100);
    expect(Math.max(...moves)).toBeLessThan(3);
    expect(moves.reduce((a, b) => a + b, 0) / moves.length).toBeGreaterThan(0.2);
    expect(Math.max(...ranges)).toBeLessThan(4);
    for (let i = 1; i < ds.length; i++) {
      const p = ds[i - 1];
      const pivot = (p.high + p.low + p.close) / 3;
      const r2 = pivot + (p.high - p.low);
      expect(Math.abs(r2 / ds[i].close - 1) * 100).toBeLessThan(5);            // P0.9 showed R2 +10.98%
      expect(Math.abs(p.high / ds[i].close - 1) * 100).toBeLessThan(4);        // and PDH +7.08%
    }
    const last = bars[bars.length - 1].close;
    expect(Math.abs(last / 24_000 - 1)).toBeLessThan(0.12);                     // two weeks stay near the start level
  });

  it("daily bars move about 0.5-1.5% a day", () => {
    const daily = generateSampleCandles(250, 24_000, 11, { daily: true, end: new Date("2026-10-08T12:00:00Z") });
    expect(daily).toHaveLength(250);
    const moves = daily.slice(1).map((b, i) => Math.abs(b.close / daily[i].close - 1) * 100);
    const mean = moves.reduce((a, b) => a + b, 0) / moves.length;
    expect(mean).toBeGreaterThan(0.4);
    expect(mean).toBeLessThan(1.6);
  });

  it("resamples inside the session: a 60-minute bar never spans two days", () => {
    const hourly = resampleByFactor(bars, 60);
    expect(hourly.length).toBe(12 * 7);          // 6 full hours + the 15:15-15:29 stub each day
    const fives = resampleByFactor(bars, 5);
    expect(fives.length).toBe(12 * 75);
    // a still-forming last bar is dropped: 12 days minus the last 3 minutes -> the last 15-minute slot is incomplete
    expect(resampleByFactor(bars.slice(0, -3), 15).length).toBe(12 * 25 - 1);
  });

  it("never ends in the future by default", () => {
    const last = generateSampleCandles(SESSION_BARS, 24_000, 3).at(-1)!;
    expect(new Date(last.timestamp).getTime()).toBeLessThanOrEqual(Date.now());
  });
});
