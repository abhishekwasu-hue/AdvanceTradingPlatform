import type { OHLCVBar, OptionChain } from "../types";

// Deterministic PRNG so the demo dataset is reproducible across reloads.
function mulberry32(seed: number) {
  let a = seed;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const TIMEFRAME_MINUTES: Record<string, number> = {
  "1min": 1, "5min": 5, "15min": 15, "30min": 30, "60min": 60,
};

/**
 * Client-side stand-in for real broker market data, clearly labelled "sample" wherever it is shown.
 *
 * P0.10: shaped like an NSE session, so the numbers read like a market. Bars are one minute apart only inside the
 * 09:15-15:29 IST session (375 a day), on weekdays, ending on the latest weekday; each day has its own volatility
 * (a typical index day moves 0.5-1.5% open to close, clustered), a small opening gap, a U-shaped intraday
 * volatility and volume profile, and a gentle pull back to the start price. The P0.9 series ran minute bars round
 * the clock, so one "day" held 1,440 bars and the pivot / previous-day levels came out 7-11% away from the price.
 * `daily: true` gives one bar per weekday (for swing reads) with the same day-level statistics.
 * Sample numbers are still not performance; they only have to look like a market.
 */
export const SESSION_BARS = 375;
const SESSION_OPEN_UTC_MINUTES = 3 * 60 + 45;     // 09:15 IST

export interface SampleOptions {
  /** One bar per trading day instead of one per session minute. */
  daily?: boolean;
  /** The last trading day is the last weekday on or before this date (default: today). */
  end?: Date;
}

/** The `n` weekdays ending on the last weekday on or before `end`, oldest first, as UTC midnights. Without `end` the
 * series ends on the last COMPLETED session (today only after 15:30 IST), so no sample bar lies in the future. */
export function tradingDays(n: number, end?: Date): number[] {
  const out: number[] = [];
  let last = end;
  if (!last) {
    const now = new Date();
    const istMinutes = (now.getUTCHours() * 60 + now.getUTCMinutes() + 330) % 1440;
    const istDay = new Date(now.getTime() + 330 * 60_000);
    last = new Date(Date.UTC(istDay.getUTCFullYear(), istDay.getUTCMonth(), istDay.getUTCDate()));
    if (istMinutes < 15 * 60 + 30) last.setUTCDate(last.getUTCDate() - 1);
  }
  const d = new Date(Date.UTC(last.getUTCFullYear(), last.getUTCMonth(), last.getUTCDate()));
  while (out.length < n) {
    const wd = d.getUTCDay();
    if (wd !== 0 && wd !== 6) out.unshift(d.getTime());
    d.setUTCDate(d.getUTCDate() - 1);
  }
  return out;
}

export function generateSampleCandles(count = 400, startPrice = 100, seed = 7, opts: SampleOptions = {}): OHLCVBar[] {
  const rand = mulberry32(seed);
  const gauss = () => {
    const u = Math.max(rand(), 1e-12);
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rand());
  };
  const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
  const nDays = opts.daily ? count : Math.ceil(count / SESSION_BARS);
  const days = tradingDays(nDays, opts.end);
  const bars: OHLCVBar[] = [];
  let close = startPrice;
  let dayVol = 0.0075;                                  // open-to-close sigma of a day, clustered between 0.4% and 1.1%

  for (const day of days) {
    dayVol = clamp(0.6 * dayVol + 0.4 * (0.004 + rand() * 0.007), 0.004, 0.011);
    const pull = ((startPrice - close) / startPrice) * 0.15;        // per day: drifts back toward the start price
    const dayDrift = pull * 0.01 + gauss() * dayVol * 0.25;          // the day's own lean, as a return over the session
    const gap = gauss() * dayVol * 0.25;
    if (opts.daily) {
      const open = close * Math.exp(gap);
      close = open * Math.exp(dayDrift + gauss() * dayVol * 0.8);
      const high = Math.max(open, close) * (1 + rand() * dayVol * 0.6);
      const low = Math.min(open, close) * (1 - rand() * dayVol * 0.6);
      bars.push({ timestamp: new Date(day + SESSION_OPEN_UTC_MINUTES * 60_000).toISOString(),
                  open: round2(open), high: round2(high), low: round2(low), close: round2(close), volume: 150_000 + Math.round(rand() * 100_000) });
      continue;
    }
    close = close * Math.exp(gap);
    const perBar = dayVol / Math.sqrt(SESSION_BARS);
    for (let m = 0; m < SESSION_BARS; m++) {
      // U-shape: busier first and last half hour, quieter around midday.
      const edge = m < 30 || m >= SESSION_BARS - 30 ? 1.45 : m > 150 && m < 270 ? 0.75 : 1.0;
      const open = close;
      close = open * Math.exp(dayDrift / SESSION_BARS + perBar * edge * gauss());
      const wick = open * perBar * edge * (0.2 + rand() * 0.6);
      bars.push({
        timestamp: new Date(day + (SESSION_OPEN_UTC_MINUTES + m) * 60_000).toISOString(),
        open: round2(open), high: round2(Math.max(open, close) + wick), low: round2(Math.min(open, close) - wick), close: round2(close),
        volume: Math.round((700 + rand() * 500) * edge * edge),
      });
    }
  }
  return bars.slice(-count);
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/** Groups 1-minute bars into `factor`-minute bars aligned to the 09:15 IST session open, one group never spanning two
 * days (P0.10: a 30- or 60-minute bar no longer straddles the close and the next open). A last group that is still
 * forming (fewer minutes than its slot, and not the session's short closing stub) is dropped - only closed bars. */
export function resampleByFactor(bars: OHLCVBar[], factor: number): OHLCVBar[] {
  if (factor <= 1) return bars;
  const out: OHLCVBar[] = [];
  let key = "";
  let count = 0;
  let slotStart = 0;
  for (const b of bars) {
    const t = new Date(b.timestamp);
    const minuteOfDay = t.getUTCHours() * 60 + t.getUTCMinutes() - SESSION_OPEN_UTC_MINUTES;
    const slot = Math.floor(minuteOfDay / factor);
    const k = `${t.toISOString().slice(0, 10)}#${slot}`;
    const last = out[out.length - 1];
    if (k !== key || !last) {
      out.push({ ...b });
      key = k;
      count = 1;
      slotStart = slot * factor;
    } else {
      last.high = Math.max(last.high, b.high);
      last.low = Math.min(last.low, b.low);
      last.close = b.close;
      last.volume += b.volume;
      count += 1;
    }
  }
  const expected = Math.max(1, Math.min(factor, SESSION_BARS - slotStart));
  if (out.length && count < expected) out.pop();
  return out;
}

/** A synthetic option chain around `underlyingLtp`, shaped so the bias comes out bullish,
 * bearish, or mixed/conflicting depending on `tilt` - useful for exercising every branch of
 * the Option Chain Intelligence Engine's bias classification from the UI.
 */
export function generateSampleOptionChain(
  underlying: string,
  underlyingLtp: number,
  tilt: "bullish" | "bearish" | "mixed" = "bullish",
  strikeStep = 50,
  strikesEachSide = 6,
): OptionChain {
  const atm = Math.round(underlyingLtp / strikeStep) * strikeStep;
  const rows = [];
  for (let i = -strikesEachSide; i <= strikesEachSide; i++) {
    const strike = atm + i * strikeStep;
    const distance = Math.abs(i);
    const baseOi = 1000 * (strikesEachSide + 1 - distance);

    let callOi = baseOi;
    let putOi = baseOi;
    let callChange = 0;
    let putChange = 0;

    if (tilt === "bullish") {
      putOi = baseOi * 1.6;
      putChange = baseOi * 0.15;
      callChange = -baseOi * 0.05;
    } else if (tilt === "bearish") {
      callOi = baseOi * 1.6;
      callChange = baseOi * 0.15;
      putChange = -baseOi * 0.05;
    } else {
      putOi = baseOi * 1.6; // PCR looks bullish...
      callChange = baseOi * 0.15; // ...but OI is actively building on the call side (bearish) - CONFLICTING
      putChange = -baseOi * 0.02;
    }

    rows.push({
      strike,
      call_oi: Math.round(callOi),
      call_change_oi: Math.round(callChange),
      call_ltp: Math.max(1, Math.round((underlyingLtp - strike) * 0.4 + 40)),
      put_oi: Math.round(putOi),
      put_change_oi: Math.round(putChange),
      put_ltp: Math.max(1, Math.round((strike - underlyingLtp) * 0.4 + 40)),
    });
  }

  return {
    underlying,
    expiry: "2024-01-25",
    underlying_ltp: underlyingLtp,
    rows,
  };
}

/** Builds the {timeframe: candles} map a strategy's `analyze()` expects, from one 1-minute base series. */
export function buildTimeframeData(baseBars: OHLCVBar[], timeframes: string[]): Record<string, OHLCVBar[]> {
  const data: Record<string, OHLCVBar[]> = {};
  for (const tf of timeframes) {
    const minutes = TIMEFRAME_MINUTES[tf] ?? 1;
    data[tf] = minutes === 1 ? baseBars : resampleByFactor(baseBars, minutes);
  }
  return data;
}
