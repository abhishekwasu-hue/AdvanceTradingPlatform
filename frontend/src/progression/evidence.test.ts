import { describe, expect, it } from "vitest";
import type { LadderCriterion, LadderEvidence } from "../types";
import { disciplineWeeksNote, kellyLine, pct, probability, ratio, rupees, ruinLine, shortWeek } from "./evidence";
import { tProgression as t } from "./i18n";
import { LOSING, OK } from "./samples";

describe("ladder evidence helpers (N2-1)", () => {
  it("formats shares, rupees, ratios and small probabilities", () => {
    expect(pct(0.6)).toBe("60.0%");
    expect(pct(null)).toBe("-");
    expect(rupees(1234.5)).toBe("₹1,234.50");
    expect(rupees(80, true)).toBe("+₹80.00");
    expect(rupees(-20, true)).toBe("-₹20.00");
    expect(rupees(0, true)).toBe("₹0.00");
    expect(ratio(2)).toBe("2.00");
    expect(ratio(undefined)).toBe("-");
    expect(probability(0.0004011)).toBe("0.04%");
    expect(probability(0.00002)).toBe("< 0.01%");
    expect(probability(0)).toBe("< 0.01%");          // an upper bound that underflowed: never "0.00%"
    expect(probability(1)).toBe("100.00%");
    expect(shortWeek("2026-W41")).toBe("W41");
    expect(shortWeek("odd")).toBe("odd");
  });

  it("writes the Kelly and ruin lines from the server's status", () => {
    expect(kellyLine(OK, t)).toBe("40.0% of capital lost on a losing trade maximises the long-run growth of these past outcomes (f = (p·b − q) / (b·(p + q))).");
    expect(ruinLine(OK, t)).toBe("At most 0.04% chance of ever falling 10% (₹1,000.00) below your starting capital.");
    expect(kellyLine(LOSING, t)).toBe("Below zero (-125.0%): these past outcomes lost money on average.");
    expect(ruinLine(LOSING, t)).toContain("Certain in this model");
    const noLoss: LadderEvidence = { ...OK, kelly: { ...OK.kelly!, fraction: null, status: "no_losses" }, ruin: { ...OK.ruin!, probability: null, status: "no_losses" } };
    expect(kellyLine(noLoss, t)).toBe("Not defined: no losing trade yet.");
    expect(ruinLine(noLoss, t)).toBe("Not bounded: no losing trade yet.");
  });

  it("says how many discipline weeks are missing, or why the criterion is not measured", () => {
    const base: LadderCriterion = { key: "discipline_score", label: "Discipline score", value: 72.5, target: 70, comparison: "at_least", met: false };
    expect(disciplineWeeksNote({ ...base, status: "too_few_weeks", weeks_needed: 4, weeks: [{ week: "2026-W40", score: 70 }, { week: "2026-W41", score: 75 }] }, t))
      .toBe("2 of 4 weeks scored");
    expect(disciplineWeeksNote({ ...base, status: "ok", weeks_needed: 2, weeks: [{ week: "2026-W40", score: 70 }, { week: "2026-W41", score: 75 }] }, t)).toBe("");
    expect(disciplineWeeksNote({ ...base, status: "engine_off", value: null, met: null }, t)).toBe("The journal's discipline engine is off for your organisation.");
  });
});
