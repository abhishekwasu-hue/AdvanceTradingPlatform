import { describe, expect, it } from "vitest";
import { OPTION_FILTER_LABELS } from "../types";
import {
  CHAIN_READ_WORDS, DIRECTION_WORDS, PCR_BAND_WORDS, alertStateWords, bannerLines, chainReadWords, classWords, directionWords, formatOi,
  formatSigned, isLateChip, leadWords, oiRead, pcrBandWords, slotTime, strikeBars,
} from "./format";
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
    // M61 OI-5: the read in numbers and neutral words - not the server's verdict message or PCR band label.
    expect(l.headline).toBe("Put OI above call OI (PCR 2.20)");
    expect(l.classes).toBe("Put: OI up, premium down · Call: no clear change");
    expect(l.totals).toBe("Call OI 13,000 · Put OI 28,600");
    expect(l.chips).toEqual(["DTE 16", "Max pain 24,500", "Data as of 09:25"]);
  });
  it("shows market closed, late-reading and expiry chips, and the first-of-day text in plain words (OC1)", () => {
    expect(bannerLines(resp({ market_open: false })).chips).toContain("Market closed");
    expect(bannerLines(resp({ stale: true, age_minutes: 22.4 })).chips).toContain("Last update 22 min ago");
    expect(bannerLines(resp({ banner: row({ dte: 0 }) })).chips[0]).toBe("Expiry today");
    expect(bannerLines(resp({ banner: row({ dte: 1 }) })).chips[0]).toBe("Expiry tomorrow");
    const first = bannerLines(resp({ banner: row({ first_of_day: true, message: "Insufficient data — history builds through the session" }) }));
    expect(first.classes).toBe("OI change will show after the first few minutes");
    expect(first.headline).toBe("Put OI above call OI (PCR 2.20)");
    const none = bannerLines(resp({ banner: null, message: "Insufficient data — history builds through the session", stale: true, age_minutes: null }));
    expect(none.headline).toBe("OI change will show after the first few minutes");
    expect(none.chips).toEqual(["No reading yet today"]);
    expect(isLateChip("Last update 22 min ago") && isLateChip("No reading yet today") && !isLateChip("Market closed")).toBe(true);
    // No "Insufficient data" and no "Stale" label anywhere in the banner's words.
    for (const l of [first, none, bannerLines(resp({ stale: true, age_minutes: 5 }))]) expect(JSON.stringify(l)).not.toMatch(/insufficient|stale/i);
    expect(bannerLines(resp({ banner: row({ pcr: null }) })).headline).toBe("Put OI above call OI (PCR –)");
  });
  it("maps the engine's internal labels to neutral words (M61 OI-5)", () => {
    expect(directionWords("BULLISH")).toBe("Put premium down / call premium up");
    expect(directionWords("BEARISH")).toBe("Put premium up / call premium down");
    expect(directionWords("MIXED")).toBe("Put and call readings differ");
    expect(directionWords("SOMETHING")).toBe("No clear OI change");
    expect(directionWords(null)).toBe("No clear OI change");
    expect(leadWords("BULLISH (Strong)")).toBe("Put OI lead, growing");
    expect(leadWords("BEARISH Weakening")).toBe("Call OI lead, not growing");
    expect(leadWords("NEUTRAL")).toBe("No OI lead");
    expect(pcrBandWords("OVERSOLD")).toBe("Very low PCR zone");
    expect(pcrBandWords("MILD_BULLISH")).toBe("High PCR zone");
    expect(classWords("Short covering")).toBe("OI down, premium up");
    expect(classWords("LONG_UNWINDING")).toBe("OI down, premium down");
    expect(alertStateWords("PCR_BAND", "OVERBOUGHT")).toBe("Very high PCR zone");
    expect(alertStateWords("STRENGTH_CHANGE", "BULLISH Strong")).toBe("Put OI lead, growing");
    expect(alertStateWords("OI_WALL", "formed CE@25000")).toBe("formed CE@25000");
    expect(alertStateWords("DIRECTION_CHANGE", null)).toBe("—");
    expect(oiRead({ total_call_oi: 120, total_put_oi: 100, pcr: 0.83 })).toBe("Call OI above put OI (PCR 0.83)");
    expect(chainReadWords("BULLISH")).toBe("PCR above 1.2, put OI building");
    expect(chainReadWords("CONFLICTING")).toBe("PCR and OI change differ");
    expect(chainReadWords(undefined)).toBe("No one-sided read");
  });
  it("guard: no user-facing OI / PCR text carries a verdict word (M61 OI-5)", () => {
    const VERDICT = /\b(bullish|bearish|overbought|oversold|mildly)\b/i;
    const labels = ["BULLISH", "BEARISH", "MIXED", "NEUTRAL", "SIDEWAYS", "BULLISH (Strong)", "BEARISH (Weakening)", ...Object.keys(PCR_BAND_WORDS)];
    const texts: string[] = [...Object.values(DIRECTION_WORDS), ...Object.values(PCR_BAND_WORDS), ...Object.values(CHAIN_READ_WORDS),
      ...Object.values(OPTION_FILTER_LABELS)];
    for (const label of [...labels, "CONFLICTING"]) {
      texts.push(directionWords(label), leadWords(label), pcrBandWords(label), chainReadWords(label));
      for (const t of ["DIRECTION_CHANGE", "STABLE_FLIP", "STRENGTH_CHANGE", "PCR_BAND"]) texts.push(alertStateWords(t, label));
    }
    for (const d of ["BULLISH", "BEARISH", "MIXED", "NEUTRAL"] as const) {
      // The server's own verdict message and band label are in the row; none of it may reach the banner's words.
      texts.push(JSON.stringify(bannerLines(resp({ banner: row({ direction: d, signal: `${d} (Strong)` }) }))));
    }
    for (const t of texts) expect(t).not.toMatch(VERDICT);
  });
  it("formats numbers from data only", () => {
    // M61 OI-1: the app's one count formatter ("12.35 L"), not full digits.
    expect(formatOi(1234567)).toBe("12.35 L");
    expect(formatOi(null)).toBe("–");
    expect(formatSigned(-2500)).toBe("−2,500");
    expect(formatSigned(2160470)).toBe("+21.6 L");
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
