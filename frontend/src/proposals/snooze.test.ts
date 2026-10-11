import { describe, expect, it } from "vitest";
import { evidenceSummary, isSnoozed, next0900Ist, snoozeChoices, timeLeft } from "./snooze";

// 2026-10-11 14:00 IST = 08:30 UTC
const AFTERNOON = new Date("2026-10-11T08:30:00Z");
// 2026-10-11 07:00 IST = 01:30 UTC
const EARLY = new Date("2026-10-11T01:30:00Z");

describe("next0900Ist", () => {
  it("is tomorrow 09:00 IST in the afternoon and today 09:00 IST before nine", () => {
    expect(next0900Ist(AFTERNOON)).toEqual({ at: new Date("2026-10-12T03:30:00Z"), tomorrow: true });
    expect(next0900Ist(EARLY)).toEqual({ at: new Date("2026-10-11T03:30:00Z"), tomorrow: false });
    // Late evening UTC is already the next day in India.
    expect(next0900Ist(new Date("2026-10-11T20:00:00Z")).at).toEqual(new Date("2026-10-12T03:30:00Z"));
  });
});

describe("snoozeChoices", () => {
  it("offers 15 min, 1 hour and 09:00 IST, cut at the expiry", () => {
    const expires = new Date(AFTERNOON.getTime() + 24 * 3600 * 1000).toISOString();
    const choices = snoozeChoices(AFTERNOON, expires);
    expect(choices.map((c) => c.key)).toEqual(["15m", "1h", "0900"]);
    expect(choices[0].body).toEqual({ minutes: 15 });
    expect(choices[1].body).toEqual({ minutes: 60 });
    expect(choices[2].body).toEqual({ until: "2026-10-12T03:30:00.000Z" });
    expect(choices.every((c) => !c.cutAtExpiry)).toBe(true);

    const soon = new Date(AFTERNOON.getTime() + 30 * 60 * 1000).toISOString();
    const cut = snoozeChoices(AFTERNOON, soon);
    expect(cut.map((c) => c.cutAtExpiry)).toEqual([false, true, true]);
    expect(cut[1].wakesAt.toISOString()).toBe(soon);
  });
});

describe("isSnoozed / timeLeft / evidenceSummary", () => {
  it("is snoozed only while PROPOSED and before the wake time", () => {
    const later = new Date(AFTERNOON.getTime() + 60_000).toISOString();
    expect(isSnoozed({ status: "PROPOSED", snoozed_until: later }, AFTERNOON)).toBe(true);
    expect(isSnoozed({ status: "PROPOSED", snoozed_until: AFTERNOON.toISOString() }, AFTERNOON)).toBe(false);
    expect(isSnoozed({ status: "REJECTED", snoozed_until: later }, AFTERNOON)).toBe(false);
    expect(isSnoozed({ status: "PROPOSED", snoozed_until: null }, AFTERNOON)).toBe(false);
  });

  it("counts the time left down to minutes", () => {
    expect(timeLeft(new Date(AFTERNOON.getTime() + (3 * 60 + 20) * 60_000 + 59_000).toISOString(), AFTERNOON)).toEqual({ hours: 3, minutes: 20 });
    expect(timeLeft(AFTERNOON.toISOString(), AFTERNOON)).toBeNull();
    expect(timeLeft(null, AFTERNOON)).toBeNull();
  });

  it("summarises scalar evidence in a few pairs", () => {
    expect(evidenceSummary({ day_pnl: -2345.678, capital: 100000, threshold_pct: 2, extra: 1 })).toEqual(["day pnl: -2345.68", "capital: 100000", "threshold pct: 2"]);
    expect(evidenceSummary({ losses: [-1, -2, -3], regime: "VOLATILE", nested: { a: 1 } })).toEqual(["losses: -1, -2, -3", "regime: VOLATILE"]);
    expect(evidenceSummary(undefined)).toEqual([]);
  });
});
