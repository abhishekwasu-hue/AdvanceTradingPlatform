import { describe, expect, it } from "vitest";
import { istDayNumber, vwap } from "./indicators";

describe("istDayNumber (N4 CH6-2: no locale call per bar)", () => {
  it("splits days exactly at IST midnight, as toLocaleDateString in Asia/Kolkata does", () => {
    const stamps = ["2026-10-08T18:29:59Z", "2026-10-08T18:30:00Z", "2026-10-09T03:45:00Z", "2026-10-09T18:29:00.000Z", "2026-10-09T23:59:00+05:30",
                    "2026-03-01T00:00:00Z", "2025-12-31T18:30:00Z"];
    for (const a of stamps) for (const b of stamps) {
      const same = new Date(a).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" }) === new Date(b).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
      expect(istDayNumber(a) === istDayNumber(b), `${a} / ${b}`).toBe(same);
    }
  });
});

describe("vwap restarts on each IST day", () => {
  it("resets at IST midnight (18:30 UTC), not UTC midnight", () => {
    const bar = (timestamp: string, p: number, volume = 10) => ({ timestamp, open: p, high: p, low: p, close: p, volume });
    const v = vwap([bar("2026-10-08T18:00:00Z", 100), bar("2026-10-08T18:29:00Z", 200), bar("2026-10-08T18:30:00Z", 300), bar("2026-10-08T23:00:00Z", 500)]);
    expect(v).toEqual([100, 150, 300, 400]);
  });
});
