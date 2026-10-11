import { describe, expect, it } from "vitest";
import type { DataTierBadgeInfo } from "../types";
import { formatEodDate, formatIstTime, tierText, tierTitle, tierTone } from "./dataTier";

const live: DataTierBadgeInfo = {
  tier: "A", label: "Live · your Upstox", source: "broker:upstox", broker: "upstox", display_name: "Upstox",
  as_of: "2026-10-12T05:12:05+00:00", eod_date: null, stale: false, stale_reason: null, alerts_suppressed: false,
};
const eod: DataTierBadgeInfo = {
  tier: "C", label: "EOD · 2026-10-09", source: "eod", broker: null, display_name: null, as_of: null, eod_date: "2026-10-09",
  stale: false, stale_reason: null, alerts_suppressed: false, reason: "Zerodha is execution-only in ATP.",
};

describe("data-tier badge helpers (BT1)", () => {
  it("formats EOD dates and IST times", () => {
    expect(formatEodDate("2026-10-09")).toBe("9 Oct 2026");
    expect(formatEodDate("bad")).toBeNull();
    expect(formatEodDate(null)).toBeNull();
    expect(formatIstTime("2026-10-12T05:12:05Z")).toBe("10:42:05 IST");
    expect(formatIstTime("nope")).toBeNull();
  });

  it("labels live data with the user's broker and time, EOD with its date", () => {
    expect(tierText(live)).toBe("Live · your Upstox · 10:42:05 IST");
    expect(tierText({ ...live, as_of: null })).toBe("Live · your Upstox");
    expect(tierText(eod)).toBe("EOD · 9 Oct 2026");
    expect(tierText({ ...eod, eod_date: null })).toBe("EOD");
  });

  it("explains the tier, the reason and suppressed alerts", () => {
    expect(tierTitle(eod)).toContain("execution-only");
    expect(tierTone(eod)).toBe("eod");
    expect(tierTone(live)).toBe("live");
    const stale = { ...live, stale: true, stale_reason: "quote stale: last trade 900s ago", alerts_suppressed: true };
    expect(tierTone(stale)).toBe("stale");
    expect(tierTitle(stale)).toContain("Alerts on this data are held back");
    expect(tierTitle({ ...live, degraded: true })).toContain("symbol budget");
  });
});
