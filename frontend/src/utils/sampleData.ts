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
 * P0.9: a seeded random walk with slowly changing drift and volatility clustering, pulled gently back to
 * the start price. The old series (a 40-bar sine wave of 1.5% plus a 25% drift) reversed every breakout
 * on schedule - every breakout strategy lost exactly 1R on it - and carried NIFTY from 24,500 to 30,700
 * in two weeks. Sample numbers are still not performance; they only have to look like a market.
 */
export function generateSampleCandles(count = 400, startPrice = 100, seed = 7): OHLCVBar[] {
  const rand = mulberry32(seed);
  const gauss = () => {
    const u = Math.max(rand(), 1e-12);
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rand());
  };
  const bars: OHLCVBar[] = [];
  let close = startPrice;
  let drift = 0;          // per-bar drift, re-drawn every ~150 bars (trend phases)
  let vol = 0.0006;       // per-bar volatility (~0.06% a minute), clustered
  const start = new Date("2024-01-02T09:15:00Z").getTime();

  for (let i = 0; i < count; i++) {
    if (i % 150 === 0) drift = (rand() - 0.5) * 0.00012;
    vol = Math.min(0.0018, Math.max(0.0003, vol * 0.97 + 0.03 * 0.0006 + Math.abs(gauss()) * 0.00004));
    const pull = ((startPrice - close) / startPrice) * 0.002;      // keeps the series near its start price
    const open = close;
    close = Math.max(startPrice * 0.2, open * Math.exp(drift + pull + vol * gauss()));
    const wick = open * vol * (0.3 + rand() * 0.7);
    const high = Math.max(open, close) + wick;
    const low = Math.min(open, close) - wick;
    const volume = 800 + Math.round(rand() * 400);

    bars.push({
      timestamp: new Date(start + i * 60_000).toISOString(),
      open: round2(open), high: round2(high), low: round2(low), close: round2(close), volume,
    });
  }
  return bars;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/** Groups every `factor` consecutive 1-minute bars into one bar - an exact resample since the
 * base series is evenly spaced, used to build the higher timeframe a multi-timeframe strategy needs.
 */
export function resampleByFactor(bars: OHLCVBar[], factor: number): OHLCVBar[] {
  if (factor <= 1) return bars;
  const out: OHLCVBar[] = [];
  for (let i = 0; i + factor <= bars.length; i += factor) {
    const chunk = bars.slice(i, i + factor);
    out.push({
      timestamp: chunk[0].timestamp,
      open: chunk[0].open,
      high: Math.max(...chunk.map((b) => b.high)),
      low: Math.min(...chunk.map((b) => b.low)),
      close: chunk[chunk.length - 1].close,
      volume: chunk.reduce((sum, b) => sum + b.volume, 0),
    });
  }
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
