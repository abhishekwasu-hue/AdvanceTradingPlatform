import { describe, expect, it } from "vitest";
import golden from "./__fixtures__/chart_types_golden.json";
import {
  autoBoxSize, barPath, chartView, closedBars, formatTick, heikinAshi, hollowCandles, is24x7, niceStep, periodSeconds, rangeBars, renko,
  sessionBreaks, venueZone, type OhlcBar,
} from "./chartTypes";

const T0 = 1_772_430_300;                                    // a bar start (epoch s); every test counts from here

const bar = (i: number, open: number, high: number, low: number, close: number, step = 60): OhlcBar => ({ time: T0 + i * step, open, high, low, close });
const closesOnly = (closes: number[]) => closes.map((c, i) => bar(i, c, c, c, c));

/** A deterministic random walk (LCG) of 1-minute bars - the goldens' input and the invariance tests' data. */
function walk(n: number, seed = 7, start = 22_000): OhlcBar[] {
  let s = seed;
  const rnd = () => { s = (s * 1_103_515_245 + 12_345) % 2_147_483_648; return s / 2_147_483_648; };
  const out: OhlcBar[] = [];
  let last = start;
  for (let i = 0; i < n; i++) {
    const open = last;
    const close = Math.round((open + (rnd() - 0.5) * 60) * 20) / 20;
    const high = Math.round((Math.max(open, close) + rnd() * 25) * 20) / 20;
    const low = Math.round((Math.min(open, close) - rnd() * 25) * 20) / 20;
    out.push(bar(i, open, high, low, close));
    last = close;
  }
  return out;
}

describe("Heikin Ashi", () => {
  it("matches the hand-worked bars and keeps the source times", () => {
    expect(heikinAshi([bar(0, 10, 12, 9, 11), bar(1, 11, 13, 10, 12)])).toEqual([
      { time: T0, open: 10.5, high: 12, low: 9, close: 10.5 },
      { time: T0 + 60, open: 10.5, high: 13, low: 10, close: 11.5 },
    ]);
  });
});

describe("Renko", () => {
  it("adds a brick per full box in the trend, reverses only a box beyond the last brick's other side", () => {
    const bricks = renko(closesOnly([100, 100.5, 101.2, 103.1, 102.4, 100.9, 99.8]), 1);
    expect(bricks.map((b) => [b.time - T0, b.open, b.close])).toEqual([
      [120, 100, 101], [180, 101, 102], [181, 102, 103],   // two bricks in one bar: the second a second later
      [300, 102, 101], [360, 101, 100],                    // 102.4 is not a reversal (needs 101); 100.9 is
    ]);
    expect(bricks.every((b) => b.high === Math.max(b.open, b.close) && b.low === Math.min(b.open, b.close))).toBe(true);
    expect(renko(closesOnly([100, 100.9, 99.2]), 1)).toEqual([]);                // no full box yet: nothing
    expect(renko(closesOnly([100, 98.9]), 1).map((b) => [b.open, b.close])).toEqual([[100, 99]]);
    expect(renko([], 1)).toEqual([]);
    expect(renko(closesOnly([1, 5]), 0)).toEqual([]);
  });
});

describe("range bars", () => {
  it("spans exactly the range, opens where the last closed, and walks each bar open-low-high-close (up) or open-high-low-close (down)", () => {
    expect(barPath(bar(0, 10, 11, 9.5, 10.5))).toEqual([10, 9.5, 11, 10.5]);
    expect(barPath(bar(0, 12.8, 12.9, 9, 9.2))).toEqual([12.8, 12.9, 9, 9.2]);
    const bars = rangeBars([bar(0, 10, 11, 9.5, 10.5), bar(1, 10.5, 13, 10.4, 12.8), bar(2, 12.8, 12.9, 9, 9.2)], 2);
    expect(bars.map((b) => [b.time - T0, b.open, b.high, b.low, b.close])).toEqual([
      [60, 10, 11.5, 9.5, 11.5], [120, 11.5, 13, 11, 11], [121, 11, 11, 9, 9],
    ]);
    for (const b of bars) expect(b.high - b.low).toBeCloseTo(2, 9);
    expect(rangeBars([bar(0, 10, 11, 9.5, 10.5)], 2)).toEqual([]);              // still filling: not returned
  });
});

describe("no forming bar, no repainting", () => {
  const data = walk(400);

  it("closedBars drops only the bars whose period has not ended", () => {
    expect(closedBars(data, 60, T0 + 400 * 60)).toHaveLength(400);
    expect(closedBars(data, 60, T0 + 399 * 60 + 59)).toHaveLength(399);          // the last bar is still forming
    expect(closedBars(data, 60, T0 + 120)).toHaveLength(2);
    expect(periodSeconds("15min")).toBe(900);
    expect(periodSeconds("day")).toBe(86_400);
  });

  it("Renko and range bricks of any prefix are a prefix of the bricks of all the bars (truncation invariance)", () => {
    const auto = autoBoxSize(data) as number;
    const box = 20;                                                              // small enough for many bricks
    const fullR = renko(data, box);
    const fullG = rangeBars(data, box);
    expect(fullR.length).toBeGreaterThan(50);
    expect(fullG.length).toBeGreaterThan(50);
    for (let n = 1; n <= data.length; n++) {
      const prefix = data.slice(0, n);
      expect(autoBoxSize(prefix)).toBe(auto);                                     // the auto box is fixed by the first bar
      const r = renko(prefix, box), g = rangeBars(prefix, box);
      expect(r).toEqual(fullR.slice(0, r.length));
      expect(g).toEqual(fullG.slice(0, g.length));
      expect(heikinAshi(prefix)).toEqual(heikinAshi(data).slice(0, n));
    }
  });

  it("a forming bar never reaches Renko or range bricks, whatever its values", () => {
    const opts = { periodSec: 60, nowSec: T0 + 399 * 60 + 30, up: "#0f0", down: "#f00" };
    for (const type of ["renko", "range"] as const) {
      const base = chartView(type, data, data, opts);
      const spiked = data.slice(0, 399).concat({ ...data[399], high: 99_999, low: 1, close: 99_999 });
      expect(chartView(type, spiked, spiked, opts)).toEqual(base);
      expect(base).toEqual(chartView(type, data.slice(0, 399), data.slice(0, 399), { ...opts, nowSec: T0 + 10 ** 6 }));
      expect(base.synthetic).toBe(true);
    }
    const live = chartView("heikin_ashi", data, data, opts);                     // candle types show the forming bar
    expect(live.bars).toHaveLength(400);
    expect(chartView("line", data, data, opts).line).toBe(true);
  });
});

describe("hollow candles", () => {
  it("is hollow when it closed at or above its open, coloured by the close against the previous close", () => {
    const out = hollowCandles([bar(0, 10, 11, 9, 10.5), bar(1, 10.5, 11, 9, 10), bar(2, 9, 10, 8.5, 9.5)], "UP", "DN");
    expect(out.map((b) => [b.color, b.borderColor])).toEqual([["rgba(0,0,0,0)", "UP"], ["DN", "DN"], ["rgba(0,0,0,0)", "DN"]]);
  });
});

describe("box size", () => {
  it("rounds down to 1 / 2 / 2.5 / 5 x 10^k", () => {
    expect([niceStep(110), niceStep(14), niceStep(0.37), niceStep(2.5), niceStep(0)]).toEqual([100, 10, 0.25, 2.5, 1]);
    expect(autoBoxSize([bar(0, 22_000, 22_010, 21_990, 22_005)])).toBe(100);
    expect(autoBoxSize([])).toBeNull();
  });
});

describe("goldens (CH5-b)", () => {
  it("Heikin Ashi, Renko and range bars of the 400-bar walk match the stored goldens", async () => {
    const data = walk(400);
    const box = 20;
    const now = { input_seed: 7, bars: data.length, box, heikin_ashi: heikinAshi(data), renko: renko(data, box), range: rangeBars(data, box) };
    // Regenerate after a deliberate change: UPDATE_GOLDEN=1 npx vitest run src/charting/chartTypes.test.ts
    const env = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env ?? {};
    if (env.UPDATE_GOLDEN === "1") {
      const fs = (await import(/* @vite-ignore */ `node:${"fs"}`)) as { writeFileSync(p: URL, d: string): void };
      fs.writeFileSync(new URL("./__fixtures__/chart_types_golden.json", import.meta.url), `${JSON.stringify(now)}\n`);
      return;
    }
    expect(now).toEqual(golden);
  });
});

describe("sessions and the 24x7 axis", () => {
  const ist = (d: string) => Math.floor(Date.parse(`${d}+05:30`) / 1000);
  const utc = (d: string) => Math.floor(Date.parse(`${d}Z`) / 1000);

  it("breaks where the venue's calendar date changes: IST for NSE and MCX, UTC for crypto; none on daily bars", () => {
    const nse = [ist("2026-03-02T15:25:00"), ist("2026-03-03T09:15:00"), ist("2026-03-03T09:20:00"), ist("2026-03-04T09:15:00")];
    expect(sessionBreaks(nse, "NSE", 300)).toEqual([nse[1], nse[3]]);
    const mcx = [ist("2026-03-02T23:25:00"), ist("2026-03-03T09:00:00")];
    expect(sessionBreaks(mcx, "MCX", 300)).toEqual([mcx[1]]);
    const crypto = [utc("2026-03-02T23:55:00"), utc("2026-03-03T00:00:00"), utc("2026-03-03T00:05:00")];
    expect(sessionBreaks(crypto, "CRYPTO", 300)).toEqual([crypto[1]]);            // midnight UTC, weekends included
    expect(sessionBreaks(crypto, "NSE", 300)).toEqual([]);                         // the same instants are one IST day
    expect(sessionBreaks(nse, "NSE", 86_400)).toEqual([]);
    expect([venueZone("crypto"), venueZone("NSE"), venueZone(undefined)]).toEqual(["UTC", "Asia/Kolkata", "Asia/Kolkata"]);
    expect([is24x7("CRYPTO"), is24x7("MCX")]).toEqual([true, false]);
  });

  it("labels ticks in the venue's zone: dates on day marks, times otherwise", () => {
    const t = utc("2026-03-02T22:30:00");
    expect(formatTick(t, 3, "UTC")).toBe("22:30");
    expect(formatTick(t, 3, "Asia/Kolkata")).toBe("04:00");
    expect(formatTick(t, 2, "UTC")).toMatch(/02 Mar/);
    expect(formatTick(t, 2, "Asia/Kolkata")).toMatch(/03 Mar/);
  });
});

describe("session break lines", () => {
  it("draws a full-height dotted line at each break the time scale can place, nothing for the others", async () => {
    const { breakShapes } = await import("./sessionBreaksPrimitive");
    const shapes = breakShapes([100, 200, 300], (t) => (t === 200 ? null : t / 10), 240, "#999");
    expect(shapes).toEqual([
      { type: "segment", x1: 10, y1: 0, x2: 10, y2: 240, color: "#999", width: 1, dash: "dotted" },
      { type: "segment", x1: 30, y1: 0, x2: 30, y2: 240, color: "#999", width: 1, dash: "dotted" },
    ]);
  });
});
