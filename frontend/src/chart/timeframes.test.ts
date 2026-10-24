import { describe, expect, it } from "vitest";
import {
  DAILY_OR_LONGER, TIMEFRAMES, foldLive, historyDaysFor, isTimeframe, olderPageDaysFor, stepTimeframe, timeframeLabel, timeframeSeconds,
  type ChartBar,
} from "./timeframes";

const bar = (timestamp: string, close: number, extra: Partial<ChartBar> = {}): ChartBar =>
  ({ timestamp, open: close, high: close + 1, low: close - 1, close, volume: 10, ...extra });

describe("UI2-4b1 the one timeframe list", () => {
  it("is 1 min ... Monthly in words (day-one fix 8), in that order, with the candle route's ids", () => {
    expect(TIMEFRAMES.map(timeframeLabel)).toEqual(["1 min", "3 min", "5 min", "15 min", "30 min", "1 hour", "Daily", "Weekly", "Monthly"]);
    expect([...TIMEFRAMES]).toEqual(["1min", "3min", "5min", "15min", "30min", "60min", "day", "week", "month"]);
    const seconds = TIMEFRAMES.map(timeframeSeconds);
    expect([...seconds].sort((a, b) => a - b)).toEqual(seconds);                  // shortest to longest
    expect([...DAILY_OR_LONGER]).toEqual(["day", "week", "month"]);
  });

  it("steps through the whole list and stops at both ends (to month, past day)", () => {
    expect(stepTimeframe("day", 1)).toBe("week");
    expect(stepTimeframe("week", 1)).toBe("month");
    expect(stepTimeframe("month", 1)).toBe("month");
    expect(stepTimeframe("1min", -1)).toBe("1min");
    expect(stepTimeframe("60min", 1)).toBe("day");
    expect(stepTimeframe("5min", -2)).toBe("1min");
  });

  it("knows its own ids only", () => {
    expect(isTimeframe("week")).toBe(true);
    expect(isTimeframe("1h")).toBe(false);
    expect(isTimeframe(5)).toBe(false);
  });

  it("loads enough history per timeframe: intraday within the 30-day cap, D two years, W five, M twenty", () => {
    for (const tf of TIMEFRAMES) {
      if (!DAILY_OR_LONGER.has(tf)) expect(historyDaysFor(tf)).toBeLessThanOrEqual(30);
      expect(olderPageDaysFor(tf)).toBeGreaterThan(0);
    }
    expect(historyDaysFor("day")).toBe(730);
    expect(historyDaysFor("week")).toBe(1826);
    expect(historyDaysFor("month")).toBe(7300);
  });
});

describe("UI2-4b1 live price on the chart", () => {
  const at = "2026-03-04T05:00:00Z";

  it("day / week / month: only a bar the server marked forming moves, and no new bar is ever started", () => {
    for (const tf of ["day", "week", "month"] as const) {
      const forming = [bar("2026-03-02T18:30:00Z", 100), bar("2026-03-03T18:30:00Z", 105, { forming: true })];
      const out = foldLive(forming, 110, at, tf);
      expect(out).toHaveLength(2);
      expect(out[1]).toMatchObject({ close: 110, high: 110, low: 104, forming: true });
      expect(out[0]).toBe(forming[0]);

      const completed = [bar("2026-03-02T18:30:00Z", 100), bar("2026-03-03T18:30:00Z", 105, { forming: false })];
      expect(foldLive(completed, 110, at, tf)).toBe(completed);              // yesterday's bar never takes today's price
      expect(foldLive([bar("2026-03-03T18:30:00Z", 105)], 110, at, tf)).toHaveLength(1);
    }
  });

  it("intraday works as every chart does today: the last bar moves, a later bucket starts a new bar", () => {
    const bars = [bar("2026-03-04T04:50:00Z", 100), bar("2026-03-04T04:55:00Z", 101)];
    expect(foldLive(bars, 102, "2026-03-04T04:57:00Z", "5min")).toHaveLength(2);
    const next = foldLive(bars, 103, at, "5min");
    expect(next).toHaveLength(3);
    expect(next[2]).toMatchObject({ timestamp: at.replace("Z", ".000Z"), close: 103 });
    expect(foldLive(bars, null, at, "5min")).toBe(bars);
  });
});
