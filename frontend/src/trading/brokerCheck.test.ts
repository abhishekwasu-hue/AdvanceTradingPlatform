import { describe, expect, it } from "vitest";
import { isBrokerPause } from "../fno/OrderTicketV2";
import { plainPauseText, reportText } from "./BrokerCheckV2";

/** Owner 20 Nov: the LIVE pause after a failed LIVE order, in plain words, with its one way out. */
describe("the LIVE pause card", () => {
  it("recognises the server's pause reason and nothing else", () => {
    expect(isBrokerPause("Broker state uncertain since 2026-11-19T05:18:59+00:00 (order 4 FAILED) - LIVE entries blocked")).toBe(true);
    expect(isBrokerPause("Margin 9,539/lot, 86,692 available")).toBe(false);
    expect(isBrokerPause(null)).toBe(false);
  });

  it("says what is paused and why, and what a check found", () => {
    expect(plainPauseText({ broker_uncertain_since: null })).toBe(
      "New LIVE orders are paused: an earlier LIVE order failed, so ATP checks your broker's positions first.");
    expect(plainPauseText({ broker_uncertain_since: "2026-11-19T05:18:59+00:00" })).toMatch(/^New LIVE orders are paused since .+: an earlier/);
    const base = { broker_name: "upstox", items: [], checked_at: "", accounts: [] } as unknown as Parameters<typeof reportText>[0];
    expect(reportText({ ...base, mismatched_count: 0 })).toBe("Your broker's positions match ATP's. LIVE orders are open again.");
    expect(reportText({ ...base, mismatched_count: 2 })).toBe("2 position(s) differ from your broker: close or record them, then check again.");
  });
});
