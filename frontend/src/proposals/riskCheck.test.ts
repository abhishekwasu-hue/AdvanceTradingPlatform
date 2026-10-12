import { describe, expect, it } from "vitest";
import { breachRows } from "./riskCheck";

describe("short strike breach lines on the proposal card (Z5)", () => {
  it("prints the server's probabilities per short strike", () => {
    expect(breachRows(null)).toEqual([]);
    expect(breachRows({ available: false, reason: "x", legs: [], note: "" })).toEqual([]);
    const rows = breachRows({ available: true, reason: null, note: "Model estimate", legs: [
      { leg: 0, option_type: "PE", strike: 19700, side: "put", expiry: "2026-03-05", sessions: 4, dte: 3, status: "ok", prob_close: 0.1234, prob_touch: 0.301 },
      { leg: 1, option_type: "CE", strike: 19900, side: "call", expiry: "2026-03-05", sessions: 4, dte: 3, status: "in_the_money", prob_close: null, prob_touch: null },
      { leg: 2, option_type: "CE", strike: 21000, side: "call", expiry: "2026-04-05", sessions: 24, dte: 23, status: "ok", prob_close: 0.05, prob_touch: 0.1, extrapolated: true },
    ] });
    expect(rows.map((r) => r.text)).toEqual([
      "Short PE 19700 (3 DTE): breach at expiry 12.3%, touch before 30.1%",
      "Short CE 19900 (3 DTE): already beyond the strike",
      "Short CE 21000 (23 DTE): breach at expiry 5.0%, touch before 10.0% (outside the fitted range)",
    ]);
  });
});
