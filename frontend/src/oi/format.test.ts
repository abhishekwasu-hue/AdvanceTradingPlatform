import { describe, expect, it } from "vitest";
import { bannerLines, formatOi, formatSigned, slotTime, strikeBars, tone } from "./format";
import type { OiBannerResponse, OiBannerRow } from "./types";

const row = (over: Partial<OiBannerRow> = {}): OiBannerRow => ({
  slot: "2026-03-10T09:25:00+05:30", data_as_of: "2026-03-10T03:55:12+00:00", direction: "BULLISH",
  message: "Put writing rising → avoid shorting calls; TESTIDX bias bullish", put_class: "Writing", call_class: "Flat/unclear",
  signal: "BULLISH (Strong)", diff: 13000, delta_diff: 2600, total_call_oi: 13000, total_put_oi: 28600, pcr: 2.2,
  pcr_band: "OVERBOUGHT", pcr_label: "Overbought — possible reversal (bull-trap risk)", underlying_price: 24510,
  atm_strike: 24500, strike_step: 50, max_pain: 24500, expiry: "2026-03-26", dte: 16, first_of_day: false, ...over,
});
const resp = (over: Partial<OiBannerResponse> = {}): OiBannerResponse => ({
  underlying: "TESTIDX", date: "2026-03-10", banner: row(), market_open: true, stale: false, age_minutes: 2, stale_after_minutes: 15, ...over,
});

describe("OI banner lines", () => {
  it("lays out headline, classes, PCR and chips as the spec says", () => {
    const l = bannerLines(resp());
    expect(l.headline).toContain("TESTIDX bias bullish");
    expect(l.classes).toBe("Put: Writing · Call: Flat/unclear");
    expect(l.pcr).toBe("PCR 2.20 — Overbought — possible reversal (bull-trap risk)");
    expect(l.chips).toEqual(["DTE 16", "Max pain 24,500", "Data as of 09:25"]);
    expect(l.tone.border).toBe("border-l-up");
  });
  it("shows market closed, stale and expiry chips, and the first-of-day text", () => {
    expect(bannerLines(resp({ market_open: false })).chips).toContain("Market closed");
    expect(bannerLines(resp({ stale: true, age_minutes: 22.4 })).chips).toContain("Stale · 22 min old");
    expect(bannerLines(resp({ banner: row({ dte: 0 }) })).chips[0]).toBe("Expiry today");
    expect(bannerLines(resp({ banner: row({ dte: 1 }) })).chips[0]).toBe("Expiry tomorrow");
    const first = bannerLines(resp({ banner: row({ first_of_day: true, message: "Insufficient data — history builds through the session" }) }));
    expect(first.classes).toBeNull();
    const none = bannerLines(resp({ banner: null, message: "Insufficient data — history builds through the session", stale: true, age_minutes: null }));
    expect(none.headline).toMatch(/^Insufficient data/);
    expect(none.chips).toEqual(["Stale · no data yet"]);
    expect(bannerLines(resp({ banner: row({ pcr: null }) })).pcr).toBe("PCR — (no call OI)");
  });
  it("maps every direction to a theme token and never to a raw colour", () => {
    expect(tone("BEARISH").border).toBe("border-l-down");
    expect(tone("MIXED").text).toBe("text-warn");
    expect(tone("SOMETHING").label).toBe("Neutral");
    expect(tone(null).label).toBe("Neutral");
  });
  it("formats numbers from data only", () => {
    expect(formatOi(1234567)).toBe("12,34,567");
    expect(formatOi(null)).toBe("—");
    expect(formatSigned(-2500)).toBe("-2,500");
    expect(formatSigned(2500)).toBe("+2,500");
    expect(slotTime("bad")).toBe("—");
  });
  it("derives per-strike bars and the change since the day's first reading", () => {
    const bars = strikeBars([{ strike: 24500, baseline_call_oi: 900, baseline_put_oi: null,
      points: [{ slot: "a", call_oi: 950, put_oi: 2000 }, { slot: "b", call_oi: 1000, put_oi: 2100 }] }]);
    expect(bars).toEqual([{ strike: 24500, call: 1000, put: 2100, callChange: 100, putChange: null }]);
  });
  it("never words a banner as an order instruction", () => {
    for (const d of ["BULLISH", "BEARISH", "MIXED", "NEUTRAL"] as const) {
      const text = JSON.stringify(bannerLines(resp({ banner: row({ direction: d }) })));
      expect(text).not.toMatch(/\b(buy|sell|target|recommended)\b/i);
    }
  });
});
