import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { EodChainReply } from "../api/eod";
import { menuGroups } from "../chart/indicatorsModel";
import pageSource from "../chart/ChartsPageV2.tsx?raw";
import viewSource from "./OiViewV2.tsx?raw";
import { optionsMoveLayer, optionsMoveText } from "./expectedMoveLayer";
import {
  clampFrame, daysToExpiryClose, eodColumn, expectedMove, liveColumn, maxPainOf, nextFrame, pickExpiries, pickerTime, rangeNote, replayRows,
  replayScale, rowsAround, storedColumn, storedIsCurrent, straddleOf, type ExpirySummary, type RangeReply, type ReplayReply,
} from "./history";
import { ExpiryTable } from "./MultiExpiryV2";
import OiThresholdAlertsV2, { ruleOf, withType } from "./OiThresholdAlertsV2";
import type { LiveChain } from "./types";
import type { OiAlertSettings } from "../oi/types";

const leg = (oi: number | null, ltp: number | null, oiChange: number | null = null) => ({
  oi, oi_change: oiChange, ltp, iv: null, volume: null, bid: null, ask: null, delta: null, gamma: null, theta: null, vega: null });

describe("M96 N1: OI between two times", () => {
  it("builds the API time from the pickers and keeps the rows around the ATM", () => {
    expect(pickerTime("2026-03-10", "10:05")).toBe("2026-03-10T10:05:00");
    expect(pickerTime("2026-03-10", "")).toBeNull();
    const rows = [100, 110, 120, 130, 140, 150, 160].map((strike) => ({ strike }));
    expect(rowsAround(rows, 131, 1).map((r) => r.strike)).toEqual([120, 130, 140]);
    expect(rowsAround(rows, 131, 0)).toHaveLength(7);
    expect(rowsAround(rows, null, 1)).toHaveLength(7);
  });

  it("says plainly when a time has no stored reading", () => {
    const base: RangeReply = { underlying: "NIFTY", expiry: "2026-03-12", start: "2026-03-10T09:00:00+05:30", end: "2026-03-10T09:10:00+05:30",
      missing: "both", from: null, to: null, atm: null, rows: [], totals: null };
    expect(rangeNote(base)).toBe("No stored reading at or before 09:00 on 10-03 or 09:10 on 10-03 for this expiry.");
    expect(rangeNote({ ...base, missing: "start" })).toMatch(/Pick a later 'From' time/);
    expect(rangeNote({ ...base, missing: "same" })).toMatch(/same stored reading/);
    expect(rangeNote({ ...base, missing: null })).toBeNull();
  });
});

const REPLAY: ReplayReply = { underlying: "NIFTY", expiry: "2026-03-12", day: "2026-03-10", around: 10, strikes: [100, 110],
  frames: [
    { slot: "a", captured_at: "2026-03-10T10:00:20+05:30", spot: 105, expiry: "2026-03-12", ce: [1000, 2000], pe: [3000, null] },
    { slot: "b", captured_at: "2026-03-10T10:01:20+05:30", spot: 106, expiry: "2026-03-12", ce: [1500, 1800], pe: [3100, 500] },
    { slot: "c", captured_at: "2026-03-10T10:02:20+05:30", spot: 107, expiry: "2026-03-12", ce: [9000, 1800], pe: [3100, 600] },
  ] };

describe("M96 N1: the replay", () => {
  it("steps frame by frame and stops at the last one", () => {
    expect(nextFrame(0, 3)).toBe(1);
    expect(nextFrame(1, 3)).toBe(2);
    expect(nextFrame(2, 3)).toBeNull();
    expect(clampFrame(7, 3)).toBe(2);
    expect(clampFrame(-1, 3)).toBe(0);
    expect(clampFrame(4, 0)).toBe(0);
  });

  it("shows each frame's OI and its change since the first frame - never a later frame's number", () => {
    const one = replayRows(REPLAY, 1);
    expect(one).toEqual([
      { strike: 100, ce: 1500, pe: 3100, ceChange: 500, peChange: 100 },
      { strike: 110, ce: 1800, pe: 500, ceChange: -200, peChange: null },
    ]);
    expect(JSON.stringify(replayRows(REPLAY, 1))).not.toContain("9000");        // frame 2's number is not in frame 1
    expect(replayRows(REPLAY, 0)[0].ceChange).toBe(0);
    expect(replayScale(REPLAY)).toBe(9000);
  });
});

const SUMMARY: ExpirySummary = { slot: "s", captured_at: "2026-03-10T10:05:20+05:30", spot: 111, expiry: "2026-03-12", total_call_oi: 7900,
  total_put_oi: 6800, call_oi_change: 0, put_oi_change: 1200, pcr_oi: 6800 / 7900, max_pain: 110, atm_strike: 110, atm_iv: 18.5,
  days_to_expiry: 2, since: "2026-03-10T10:00:20+05:30" };

const LIVE: LiveChain = {
  underlying: "NIFTY", expiry: "2026-03-26", fetched_at: "2026-03-10T05:00:00Z", market_open: true, data_as_of: "2026-03-10T10:30:00+05:30",
  oi_change: { status: "ready", source: "broker", since: null },
  summary: { spot: 24510, futures: null, atm_strike: 24500, pcr_oi: 1.2, max_pain: 24400, atm_iv: 14, days_to_expiry: 12,
    max_call_oi_strike: null, max_put_oi_strike: null, total_call_oi: 1000, total_put_oi: 1200 },
  rows: [{ strike: 24500, call: leg(600, 120, 50), put: leg(700, 110, -20) }, { strike: 24550, call: leg(400, 90, 30), put: leg(500, 140, null) }],
};

describe("M96 N1: expiries side by side", () => {
  it("shows the two nearest and the nearest monthly", () => {
    const list = [{ date: "2026-03-12", kind: "weekly" as const }, { date: "2026-03-19", kind: "weekly" as const },
      { date: "2026-03-26", kind: "monthly" as const }, { date: "2026-04-30", kind: "monthly" as const }];
    expect(pickExpiries(list).map((e) => e.date)).toEqual(["2026-03-12", "2026-03-19", "2026-03-26"]);
    expect(pickExpiries([list[2], list[3]]).map((e) => e.date)).toEqual(["2026-03-26", "2026-04-30"]);
  });

  it("aggregates a stored reading, a live chain and a last close chain", () => {
    const e = { date: "2026-03-12", kind: "weekly" as const };
    expect(storedColumn(e, SUMMARY)).toMatchObject({ source: "stored", totalCall: 7900, putChange: 1200, maxPain: 110, atmIv: 18.5 });
    const live = liveColumn({ date: "2026-03-26", kind: "monthly" }, LIVE);
    expect(live).toMatchObject({ source: "live", callChange: 80, putChange: -20, pcr: 1.2, totalPut: 1200 });
    expect(liveColumn(e, { ...LIVE, oi_change: { status: "building", source: null, since: null } }).callChange).toBeNull();
    const eod: EodChainReply = { underlying: "NIFTY", date: "2026-03-09", expiry: "2026-03-12", expiries: [], spot: 105, source: "eod",
      rows: [{ strike: 100, call: { close: 6, oi: 100 }, put: { close: 1, oi: 300 } }, { strike: 110, call: { close: 1, oi: 300 }, put: { close: 6, oi: 100 } }] };
    const col = eodColumn(e, eod);
    expect(col).toMatchObject({ source: "last_close", totalCall: 400, totalPut: 400, pcr: 1, atmIv: null, callChange: null });
    // pain(100) = calls 0 + puts 100 * 10 = 1000; pain(110) = calls 100 * 10 = 1000 + 0 -> the first lowest, 100
    expect(maxPainOf([{ strike: 100, callOi: 100, putOi: 300 }, { strike: 110, callOi: 300, putOi: 100 }])).toBe(100);
    expect(maxPainOf([{ strike: 100, callOi: 0, putOi: 0 }])).toBeNull();
  });

  it("uses a stored reading while it is current, else the live chain", () => {
    const at = Date.parse(SUMMARY.captured_at);
    expect(storedIsCurrent(SUMMARY, true, at + 60_000)).toBe(true);
    expect(storedIsCurrent(SUMMARY, true, at + 10 * 60_000)).toBe(false);
    expect(storedIsCurrent(SUMMARY, false, at + 10 * 60 * 60_000)).toBe(true);
  });

  it("renders one column per expiry with its source", () => {
    const html = renderToStaticMarkup(<ExpiryTable cols={[storedColumn({ date: "2026-03-12", kind: "weekly" }, SUMMARY),
      liveColumn({ date: "2026-03-26", kind: "monthly" }, LIVE)]} />);
    expect(html).toContain('data-multi-expiry-col="2026-03-12"');
    expect(html).toContain("Monthly");
    expect(html).toContain('data-source="stored"');
    expect(html).toContain("Your broker, live");
    expect(html).toContain("18.5%");
    expect(html).not.toMatch(/\b(buy|sell|target|bullish|bearish)\b/i);
  });
});

describe("M96 N1: expected move (from the option prices)", () => {
  it("is spot ± straddle and spot × IV × sqrt(days / 365) - hand-computed", () => {
    // spot 24,000, straddle 300 -> 23,700 .. 24,300; IV 15 %, 7.3 days -> 24,000 * 0.15 * sqrt(0.02) = 509.1169
    const m = expectedMove({ spot: 24000, straddle: 300, ivPct: 15, days: 7.3 });
    expect(m.straddle).toEqual({ low: 23700, high: 24300, move: 300 });
    expect(m.iv?.move).toBeCloseTo(509.1169, 3);
    expect(m.iv?.low).toBeCloseTo(23490.8831, 3);
    expect(expectedMove({ spot: 24000, straddle: null, ivPct: 15, days: 0 })).toEqual({ straddle: null, iv: null });
    expect(expectedMove({ spot: null, straddle: 300, ivPct: 15, days: 3 })).toEqual({ straddle: null, iv: null });
  });

  it("counts calendar days to the expiry close and prices the ATM straddle", () => {
    expect(daysToExpiryClose("2026-03-12", Date.parse("2026-03-10T15:30:00+05:30"))).toBeCloseTo(2, 6);
    expect(daysToExpiryClose("2026-03-12", Date.parse("2026-03-13T09:00:00+05:30"))).toBe(0);
    expect(daysToExpiryClose(null, 0)).toBeNull();
    expect(straddleOf(LIVE)).toBe(230);
  });

  it("draws two bands and one line under the chart, and is a Layers item off by default", () => {
    const move = { range: expectedMove({ spot: 24000, straddle: 300, ivPct: 15, days: 7.3 }), spot: 24000, expiry: "2026-03-12",
      asOf: "2026-03-10T10:30:00+05:30", source: "live" as const };
    expect(optionsMoveLayer(move)?.bands?.map((b) => b.label)).toEqual(["Expected move · IV", "Expected move · straddle"]);
    expect(optionsMoveText(move, null)).toBe("Expected move (from the option prices) to 12 Mar: straddle 23,700–24,300 · IV 23,491–24,509 · as of 10:30 IST");
    expect(optionsMoveLayer({ ...move, range: { straddle: null, iv: null } })).toBeNull();
    expect(menuGroups("expected", () => true).flatMap((g) => g.items.map((i) => i.id))).toEqual(["options_expected_move"]);
    expect(menuGroups("expected", () => false)).toEqual([]);
    expect(pageSource).toContain("view.layers.includes(OPTIONS_EM_LAYER_ID)");
  });
});

describe("M96 N1: my OI levels (alerts)", () => {
  const alerts = { enabled: false, types: ["DIRECTION_CHANGE"], cooldown_minutes: 15, max_pain_strikes: 2, dte_milestones: [1, 0],
    quiet_start: null, quiet_end: null, digest_time: null } as OiAlertSettings;

  it("is off and empty until the user sets it", () => {
    const html = renderToStaticMarkup(<OiThresholdAlertsV2 alerts={alerts} onSave={() => {}} />);
    expect(html).not.toMatch(/data-oi-threshold-type="STRIKE_OI_CHANGE"[^>]*checked/);
    expect(html).not.toMatch(/data-oi-threshold-type="PCR_CROSS"[^>]*checked/);
    expect(html).not.toContain("data-oi-threshold-rules");
    expect(html).toMatch(/data-oi-threshold-pcr[^>]*value=""|value=""[^>]*data-oi-threshold-pcr/);
  });

  it("builds a rule only from the user's own three fields, in lakhs", () => {
    expect(ruleOf("24,500", "CE", "2.5")).toEqual({ strike: 24500, side: "CE", threshold: 250000 });
    expect(ruleOf("24500", "", "2")).toBeNull();
    expect(ruleOf("24500", "PE", "0")).toBeNull();
    expect(ruleOf("", "PE", "1")).toBeNull();
    expect(withType(["A", "B"], "PCR_CROSS", true)).toEqual(["A", "B", "PCR_CROSS"]);
    expect(withType(["A", "PCR_CROSS"], "PCR_CROSS", false)).toEqual(["A"]);
  });

  it("the OI view mounts the two new sections", () => {
    expect(viewSource).toContain("<MultiExpiryV2 underlying={u} />");
    expect(viewSource).toContain("<OiHistoryV2 underlying={u} />");
  });
});
