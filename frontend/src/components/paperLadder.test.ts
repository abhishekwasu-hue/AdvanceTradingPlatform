import { describe, expect, it } from "vitest";
import type { LadderCriterion } from "../types";
import { criterionProgress, criterionText, signedMoney } from "./paperLadder";

const c = (over: Partial<LadderCriterion>): LadderCriterion => ({ key: "k", label: "L", value: 0, target: 10, comparison: "at_least", met: false, ...over });

describe("progression ladder helpers (Z3-b)", () => {
  it("fills an at-least bar by value over target, capped at full", () => {
    expect(criterionProgress(c({ value: 3, target: 20 }))).toBeCloseTo(0.15);
    expect(criterionProgress(c({ value: 40, target: 20 }))).toBe(1);
    expect(criterionText(c({ value: 3, target: 20 }))).toBe("3 of 20");
  });

  it("keeps an at-most bar full within the limit and shrinks it past the limit", () => {
    expect(criterionProgress(c({ comparison: "at_most", value: 4.2, target: 10 }))).toBe(1);
    expect(criterionProgress(c({ comparison: "at_most", value: 20, target: 10 }))).toBeCloseTo(0.5);
    expect(criterionText(c({ comparison: "at_most", value: 4.2, target: 10 }))).toBe("4.20 (limit 10)");
  });

  it("shows the server's note for a criterion not measured yet", () => {
    const unmeasured = c({ value: null, target: null, comparison: null, met: null, note: "Not measured yet (P1-e)" });
    expect(criterionProgress(unmeasured)).toBeNull();
    expect(criterionText(unmeasured)).toBe("Not measured yet (P1-e)");
  });

  it("formats signed money", () => {
    expect(signedMoney(1234.5)).toBe("+1,234.50");
    expect(signedMoney(-20)).toBe("−20.00");
    expect(signedMoney(0)).toBe("0.00");
  });
});
