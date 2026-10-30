import { describe, expect, it } from "vitest";
import type { RiskConfig } from "../types";
import { drawdownSentence, fieldProblem, limitDraft, limitValue } from "./model";
import { configFromDrafts, draftProblems, draftsOf } from "./RiskV2";
import riskSource from "./RiskV2.tsx?raw";

/** REV-H5 (docs/design/REV1_PLAN.md M1; DEF-0): a "Not set" limit (null) shows as a blank field, a blank saves as "Not
 * set" (null), and only typed text is checked - so the limits save while any of them is not set. */

const NOT_SET: RiskConfig = {
  capital: null, risk_per_trade_pct: null, max_daily_loss_pct: null, max_trades_per_day: null, max_open_positions: null,
  max_consecutive_losses: null, min_risk_reward: 0, lot_size: 1, max_portfolio_risk_pct: null, stop_cooldown_minutes: null,
  dd_level_1_pct: null, dd_level_2_pct: null, event_size_cut_pct: null, loss_streak_pause: "next_session",
};

describe("the draft of a limit and what it saves", () => {
  it("a null limit is a blank field, never the text 'null'", () => {
    expect(limitDraft(null)).toBe("");
    expect(limitDraft(undefined)).toBe("");
    expect(limitDraft(0)).toBe("0");
    expect(limitDraft(2.5)).toBe("2.5");
    const drafts = draftsOf(NOT_SET);
    expect(Object.values(drafts)).not.toContain("null");
    expect(drafts.max_trades_per_day).toBe("");
    expect(drafts.min_risk_reward).toBe("0");
    expect(drafts.lot_size).toBe("1");
  });

  it("blank saves as not set (null) for a limit that may be not set; typed text saves as its number", () => {
    expect(limitValue("", true)).toBeNull();
    expect(limitValue("  ", true)).toBeNull();
    expect(limitValue("3", true)).toBe(3);
    expect(limitValue("0", true)).toBe(0);
    expect(Number.isNaN(limitValue("", false))).toBe(true);
  });

  it("only typed text is checked: a blank optional limit is no problem, a blank required one still is", () => {
    expect(fieldProblem("", undefined, undefined, true)).toBeNull();
    expect(fieldProblem("", 5, undefined, true)).toBeNull();
    expect(fieldProblem("abc", undefined, undefined, true)).toBe("Enter a number.");
    expect(fieldProblem("-1", undefined, undefined, true)).toBe("Enter 0 or more.");
    expect(fieldProblem("9", 5, undefined, true)).toBe("The most allowed is 5.");
    // the platform's minimum cool-down (the server refuses a blank below it too)
    expect(fieldProblem("", undefined, 15, true)).toBe("The least allowed is 15.");
    expect(fieldProblem("")).toBe("Enter a number.");
  });

  it("the page saves an all-not-set form as is: no problem, every blank goes as null", () => {
    const drafts = draftsOf(NOT_SET);
    expect(draftProblems(drafts, null)).toEqual({});
    expect(configFromDrafts(NOT_SET, drafts, "next_session")).toEqual(NOT_SET);
  });

  it("one limit typed next to the not-set ones: only that one is sent as a number", () => {
    const drafts = { ...draftsOf(NOT_SET), max_daily_loss_pct: "2", max_trades_per_day: "" };
    const saved = configFromDrafts(NOT_SET, drafts, "until_resume");
    expect(saved).toEqual({ ...NOT_SET, max_daily_loss_pct: 2, loss_streak_pause: "until_resume" });
    // a cleared limit goes back to not set
    const cleared = configFromDrafts({ ...NOT_SET, max_open_positions: 3 }, { ...draftsOf({ ...NOT_SET, max_open_positions: 3 }), max_open_positions: "" }, "next_session");
    expect(cleared.max_open_positions).toBeNull();
    // a typed mistake still stops the save, and only that field is marked
    expect(Object.keys(draftProblems({ ...drafts, max_open_positions: "x" }, null))).toEqual(["max_open_positions"]);
    // the required fields still need a number
    expect(Object.keys(draftProblems({ ...drafts, lot_size: "" }, null))).toEqual(["lot_size"]);
  });
});

describe("the two Risk Mediums", () => {
  it("the help says plainly that 0 blocks every entry where the engine reads 0 as a limit, and blank means no limit", () => {
    expect(riskSource).toContain('" 0 blocks every new entry."');
    expect(riskSource).toContain('" Leave it blank for no limit."');
    for (const key of ["max_trades_per_day", "max_open_positions", "max_consecutive_losses", "max_daily_loss_pct"]) {
      expect(riskSource).toMatch(new RegExp(`key: "${key}"[^}]*zeroBlocks: true`));
    }
    expect(riskSource).not.toMatch(/key: "min_risk_reward"[^}]*zeroBlocks/);
  });

  it("the guardian sentence states only the levels that are set", () => {
    expect(drawdownSentence(5, 10)).toBe("The size is halved at 5.0% below your equity peak and new entries pause at 10.0%. A winning streak never raises the size above normal.");
    expect(drawdownSentence(5, null)).toBe("The size is halved at 5.0% below your equity peak. A winning streak never raises the size above normal.");
    expect(drawdownSentence(null, 10)).toBe("New entries pause at 10.0% below your equity peak. A winning streak never raises the size above normal.");
    expect(drawdownSentence(null, null)).toBeNull();
    expect(drawdownSentence(0, 0)).toBeNull();                       // 0 is off, as the guardian reads it
    expect(riskSource).not.toContain("formatNumber(guardian.settings.dd_level_1_pct");
  });
});
