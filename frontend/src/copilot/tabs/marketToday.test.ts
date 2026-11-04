import { describe, expect, it } from "vitest";
import { holidayWord, shownHeadline } from "./MarketPulseTab";
import type { BriefRead } from "../../types";

/** Owner 3 Nov (CA-Q2): the Market today tab - no repeated session label, holidays as words. */
describe("Market today tab", () => {
  const read = { state: "last_session", label: "Last session, Tue 3 Nov" } as BriefRead;
  it("the headline does not repeat the badge's label", () => {
    expect(shownHeadline("Last session, Tue 3 Nov: Sideways: price is moving inside a range, with no clear trend.", read))
      .toBe("Sideways: price is moving inside a range, with no clear trend.");
    expect(shownHeadline("Trending up: higher highs and higher lows.", read)).toBe("Trending up: higher highs and higher lows.");
    expect(shownHeadline("Last session, Tue 3 Nov: x", { ...read, state: "none" } as BriefRead)).toBe("Last session, Tue 3 Nov: x");
  });
  it("a holiday reads as a weekday and a date", () => {
    expect(holidayWord("2026-11-08")).toBe("Sun 8 Nov");
    expect(holidayWord("2026-11-10")).toBe("Tue 10 Nov");
    expect(holidayWord("odd")).toBe("odd");
  });
});
