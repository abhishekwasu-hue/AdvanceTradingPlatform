import { describe, expect, it } from "vitest";
import { MAX_COMBINATIONS, checkGrid, newRow, optimisableParams, rangeValues } from "./optimiserGrid";

const PARAMS = optimisableParams({ ema_fast: 9, rsi_period: 14, atr_mult_sl: 1.2, target_rr: [1.5, 2], last_entry: "14:30", on: true });

describe("SB-UX SU7: the optimiser's fields", () => {
  it("lists only the number settings, by their plain names", () => {
    expect(PARAMS.map((p) => [p.key, p.label, p.whole])).toEqual([["ema_fast", "Fast EMA", true], ["rsi_period", "RSI length", true], ["atr_mult_sl", "Stop (x ATR)", false]]);
  });

  it("adds a row with the next free setting and no numbers (DEF-0)", () => {
    expect(newRow(PARAMS, [])).toEqual({ key: "ema_fast", from: "", to: "", step: "" });
    expect(newRow(PARAMS, [{ key: "ema_fast", from: "", to: "", step: "" }])?.key).toBe("rsi_period");
    expect(newRow(PARAMS, PARAMS.map((p) => ({ key: p.key, from: "", to: "", step: "" })))).toBeNull();
  });

  it("expands From / To / Step without float drift", () => {
    expect(rangeValues(9, 12, 3)).toEqual([9, 12]);
    expect(rangeValues(9, 13, 3)).toEqual([9, 12]);
    expect(rangeValues(0.8, 1.2, 0.1)).toEqual([0.8, 0.9, 1, 1.1, 1.2]);
  });

  it("builds the grid the server receives and counts the combinations", () => {
    const c = checkGrid([{ key: "ema_fast", from: "9", to: "12", step: "3" }, { key: "rsi_period", from: "7", to: "14", step: "7" }], PARAMS);
    expect(c).toEqual({ grid: { ema_fast: [9, 12], rsi_period: [7, 14] }, problems: [], combinations: 4 });
  });

  it("says every problem in words, naming the setting", () => {
    expect(checkGrid([], PARAMS).problems).toEqual(["Add at least one setting to vary."]);
    const p = (from: string, to: string, step: string, key = "ema_fast") => checkGrid([{ key, from, to, step }], PARAMS).problems[0];
    expect(p("", "12", "1")).toBe("Fast EMA: fill From, To and Step.");
    expect(p("9", "12", "0")).toBe("Fast EMA: Step must be above 0.");
    expect(p("12", "9", "1")).toBe("Fast EMA: From must not be above To.");
    expect(p("9", "12", "1.5")).toBe("Fast EMA: use whole numbers.");
    expect(p("0", "12", "1")).toBe("Fast EMA: From must be 1 or more.");
    expect(p("1", "500", "1")).toMatch(/^Fast EMA: more than 60 values/);
    expect(p("1", "2", "0.5", "atr_mult_sl")).toBeUndefined();
    expect(checkGrid([{ key: "ema_fast", from: "9", to: "9", step: "1" }, { key: "ema_fast", from: "9", to: "9", step: "1" }], PARAMS).problems)
      .toEqual(["Fast EMA is in the list twice."]);
    expect(checkGrid([{ key: "nope", from: "1", to: "2", step: "1" }], PARAMS).problems[0]).toBe("Nope: this strategy has no such setting.");
  });

  it("refuses more than the server's 60 combinations before the call", () => {
    const c = checkGrid([{ key: "ema_fast", from: "1", to: "10", step: "1" }, { key: "rsi_period", from: "1", to: "7", step: "1" }], PARAMS);
    expect(c.grid).toBeNull();
    expect(c.combinations).toBe(70);
    expect(c.problems[0]).toBe(`70 combinations: the limit is ${MAX_COMBINATIONS}. Use a bigger step or fewer settings.`);
  });
});
