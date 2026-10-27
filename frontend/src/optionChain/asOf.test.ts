/** B2: a closed market's chain says "as of the last session close" (one note, never the request time), and a spot taken
 * from our own last index close is labelled. */
import { describe, expect, it } from "vitest";
import { provenance } from "./analyses";
import { DEMO_ANALYSES } from "./analyses.fixture";
import { chartsProvenance, type ChartsResponse } from "./charts";
import { chainNotes, lastCloseText } from "./model";
import { summaryCards } from "./SummaryStripV2";
import type { ChainSummary, LiveChain } from "./types";

const FRI_CLOSE = "2026-10-23T15:30:00+05:30";
const SUMMARY: ChainSummary = { spot: 24180.25, futures: null, atm_strike: 24200, pcr_oi: 1.1, max_pain: 24200, atm_iv: 12,
  days_to_expiry: 3, max_call_oi_strike: 24500, max_put_oi_strike: 24000, total_call_oi: 1, total_put_oi: 1 } as ChainSummary;

function live(extra: Partial<LiveChain>): LiveChain {
  return { underlying: "NIFTY", expiry: "2026-10-27", summary: SUMMARY, oi_change: { status: "ready", source: "broker" } as LiveChain["oi_change"],
           rows: [], fetched_at: "2026-10-24T08:09:00Z", market_open: false, ...extra };
}

describe("B2a: as of the last session close", () => {
  it("the closed-market note names the last close, once", () => {
    const notes = chainNotes(live({ data_as_of: FRI_CLOSE }), false, () => "");
    expect(notes).toEqual([{ key: "closed", text: "Market closed — as of the last session close, 23 Oct 2026, 15:30 IST" }]);
    expect(chainNotes(live({}), false, () => "")[0].text).toBe("Market closed — showing the last session's chain");
  });

  it("the analyses and charts read the same line, not the request time", () => {
    expect(lastCloseText(FRI_CLOSE)).toBe("As of the last session close, 23 Oct 2026, 15:30 IST");
    const closed = { ...DEMO_ANALYSES, data_as_of: FRI_CLOSE, market_closed: true, session: { snapshots: 0, since: null, note: "x" } };
    expect(provenance(closed)).toBe("As of the last session close, 23 Oct 2026, 15:30 IST");
    const charts = { state: "ok", message: null, charts: [], facts: null, data_as_of: FRI_CLOSE, market_closed: true } as unknown as ChartsResponse;
    expect(chartsProvenance(charts)).toBe("As of the last session close, 23 Oct 2026, 15:30 IST");
    expect(provenance({ ...closed, market_closed: false, data_as_of: "2026-10-21T04:30:00Z" })).toBe("Data as of 10:00 IST");
  });
});

describe("B2b: the spot's label", () => {
  it("says 'Spot: last close' when the server used our own last index close", () => {
    expect(summaryCards({ ...SUMMARY, spot_source: "last_close" }, null)[0].label).toBe("Spot: last close");
    expect(summaryCards({ ...SUMMARY, spot_source: "broker" }, null)[0].label).toBe("Spot");
    expect(summaryCards(SUMMARY, null)[0].label).toBe("Spot");
  });
});

describe("T1-3 on the Option chain page (owner 25 Oct): the spot card is the chart's spot", () => {
  it("shows the live tick / LTP value and its time, never the chain's own snapshot spot", () => {
    const tick = { ltp: 24533.4, at: "2026-10-27T04:05:00Z", source: "tick" as const };
    const card = summaryCards({ ...SUMMARY, spot_source: "last_close" }, null, tick)[0];
    expect(card.label).toBe("Spot");
    expect(card.value).toBe(summaryCards({ ...SUMMARY, spot: 24533.4 }, null)[0].value);
    expect(card.caption).toBe("as of 09:35 IST");
  });

  it("falls back to the chain's own spot when there is no live price", () => {
    const fromChain = { ltp: 24180.25, at: FRI_CLOSE, source: "chain" as const };
    expect(summaryCards({ ...SUMMARY, spot_source: "last_close" }, null, fromChain)[0].label).toBe("Spot: last close");
    expect(summaryCards(SUMMARY, null, null)[0].caption).toBeUndefined();
  });
});
