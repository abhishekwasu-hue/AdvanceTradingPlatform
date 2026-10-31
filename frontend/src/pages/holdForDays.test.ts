import { describe, expect, it } from "vitest";
import pageSource from "./DeploymentsPage.tsx?raw";
import { HOLD_FOR_DAYS_LINE, canHoldForDays } from "../types";

// M55 T1: "Hold for days" is offered only for a defined-risk credit structure (the API's rule).
describe("Hold for days", () => {
  it("is offered for the credit spreads only", () => {
    for (const s of ["BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "IRON_CONDOR", "IRON_BUTTERFLY"] as const) {
      expect(canHoldForDays("OPTION", s)).toBe(true);
    }
    for (const s of ["SINGLE", "SHORT_STRADDLE", "SHORT_STRANGLE", "LONG_STRADDLE", "CALL_RATIO_SPREAD", "CALENDAR_SPREAD", "CUSTOM"] as const) {
      expect(canHoldForDays("OPTION", s)).toBe(false);
    }
    expect(canHoldForDays("FUTURE", "BULL_PUT_SPREAD")).toBe(false);
    expect(canHoldForDays("UNDERLYING", "IRON_CONDOR")).toBe(false);
  });

  it("says in one plain line what it does", () => {
    expect(HOLD_FOR_DAYS_LINE).toBe("Kept overnight; exits by your own exit rules; settles at expiry");
  });

  it("the form sends SWING only for a daily strategy or a credit structure the user marked", () => {
    expect(pageSource).toContain("const held = swing || (canHold && holdForDays);");
    expect(pageSource).toContain('holding: held ? "SWING" : "INTRADAY"');
    expect(pageSource).toContain("{canHold && !swing && (");
    expect(pageSource).toContain("useState(false);         // M55 T1: off unless the trader turns it on");
  });
});
