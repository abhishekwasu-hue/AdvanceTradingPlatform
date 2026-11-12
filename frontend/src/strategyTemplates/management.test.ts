import { describe, expect, it } from "vitest";
import { EMPTY_MANAGEMENT, managementBody, managementOffered, managementProblem, referenceHint, type ManagementChoices } from "./management";

const CHOICES: ManagementChoices = {
  classes: ["UNDERLYING"],
  trails: [{ value: "SWING", label: "By the swings" }, { value: "ATR", label: "By ATR" }, { value: "MA", label: "At an average" }],
  averages: ["EMA", "SMA"],
  bounds: { trail_degree: [0, 3], trail_n: [1, 500], trail_k: [0.1, 20], bars: [1, 1000] },
  reference: { trail_k: 3, trail_n_atr: 22, trail_ma: "EMA", stall_bars: 2 },
  reference_label: "Reference - edit before use",
};

describe("M99 P2 trade-management options", () => {
  it("all off sends nothing and has no problem", () => {
    expect(managementBody(EMPTY_MANAGEMENT)).toBeNull();
    expect(managementProblem(EMPTY_MANAGEMENT, CHOICES)).toBeNull();
  });

  it("a switched-on option sends only its own numbers", () => {
    expect(managementBody({ ...EMPTY_MANAGEMENT, trail: "ATR", trailN: "22", trailK: "3", degree: "1" }))
      .toEqual({ trail: "ATR", trail_n: 22, trail_k: 3 });
    expect(managementBody({ ...EMPTY_MANAGEMENT, trail: "SWING", degree: "0", stall: true, stallBars: "2" }))
      .toEqual({ trail: "SWING", trail_degree: 0, stall_bars: 2 });
    expect(managementBody({ ...EMPTY_MANAGEMENT, maxBars: true, maxBarsN: "30", stallBars: "4" })).toEqual({ max_bars: 30 });
  });

  it("an empty or out-of-range number is asked for in plain words; the reference is only a hint", () => {
    expect(managementProblem({ ...EMPTY_MANAGEMENT, trail: "SWING", degree: "" }, CHOICES)).toMatch(/swing size/);
    expect(managementProblem({ ...EMPTY_MANAGEMENT, trail: "ATR", trailN: "22", trailK: "0" }, CHOICES)).toMatch(/average true ranges/);
    expect(managementProblem({ ...EMPTY_MANAGEMENT, trail: "MA", trailN: "20", average: "" }, CHOICES)).toMatch(/kind of average/);
    expect(managementProblem({ ...EMPTY_MANAGEMENT, stall: true, stallBars: "1.5" }, CHOICES)).toMatch(/without a new high/);
    expect(referenceHint(CHOICES, "trail_k")).toBe("Reference - edit before use: 3");
    expect(referenceHint(CHOICES, "max_bars")).toBe("");
  });

  it("is offered only on the instruments the server names", () => {
    expect(managementOffered(CHOICES, "UNDERLYING")).toBe(true);
    expect(managementOffered(CHOICES, "OPTION_SINGLE")).toBe(false);
    expect(managementOffered(null, "UNDERLYING")).toBe(false);
  });
});
