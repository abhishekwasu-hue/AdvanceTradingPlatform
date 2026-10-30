import { describe, expect, it } from "vitest";
import { isSellerPack, paperBotRequest, paperBotRequestWithDefaults } from "../paperbot/model";
import type { StrategyInfo } from "../types";
import {
  EMPTY_DEFAULTS, SWING_CONSTRUCTION_NOTE, constructionChoices, constructionProblem, needsConstruction, needsWidth, swingNotTaken, widthOf,
  withConstruction, type TradingDefaults,
} from "./tradingDefaults";

/** REV-H3 (docs/design/REV1_PLAN.md M1; CLAUDE.md section 3, DEF-0): an option seller pack on an index starts with no side
 * and no structure; the user chooses them before Start. No "buy at the money" fallback; a bot held for days reads the
 * structure default the same way and says plainly why the API's swing rules leave it out. */

const intraday = { timeframes: ["15m"] } as Pick<StrategyInfo, "timeframes">;
const daily = { timeframes: ["day"] } as Pick<StrategyInfo, "timeframes">;
const d = (over: Partial<TradingDefaults>): TradingDefaults => ({ ...EMPTY_DEFAULTS, ...over });
const F1 = "template:F1:2:15m:both:,,,,,,,,,,,";
const F2 = "template:F2:2:15m:both:,,,,,,,,";
const F1_DAILY = "template:F1:2:1d:both:,,,,,,,,,,,";

describe("a seller pack on an index: nothing pre-chosen", () => {
  it("knows the packs by their template id, nothing else", () => {
    expect(isSellerPack(F1)).toBe(true);
    expect(isSellerPack(F2)).toBe(true);
    expect(isSellerPack("template:D1:1:15m:both:,,")).toBe(false);
    expect(isSellerPack("orb_15m_5m")).toBe(false);
    expect(isSellerPack("")).toBe(false);
  });

  it("with no defaults the pack's request names no side and no structure, so Start waits for the user's choice", () => {
    const { request, applied } = paperBotRequestWithDefaults(F1, "NIFTY 50", intraday, null, false);
    expect(request.instrument_kind).toBe("OPTION");
    expect(request).not.toHaveProperty("option_position");
    expect(request).not.toHaveProperty("option_strategy");
    expect(applied).toEqual([]);
    expect(needsConstruction(request)).toBe(true);
    expect(constructionProblem(request)).toBe("Choose how this bot trades the option to start.");
    // the same with empty defaults
    expect(paperBotRequestWithDefaults(F2, "NIFTY BANK", intraday, EMPTY_DEFAULTS, false).request).not.toHaveProperty("option_position");
  });

  it("any other strategy on an index is exactly as before (one bought option)", () => {
    expect(paperBotRequest("orb_15m_5m", "NIFTY 50", intraday)).toMatchObject({ instrument_kind: "OPTION", option_position: "BUY" });
    expect(constructionProblem(paperBotRequest("orb_15m_5m", "NIFTY 50", intraday))).toBeNull();
  });

  it("a pack on a stock trades the shares: nothing to choose", () => {
    const req = paperBotRequest(F1, "RELIANCE", intraday);
    expect(req).toMatchObject({ instrument_kind: "UNDERLYING" });
    expect(constructionProblem(req)).toBeNull();
  });

  it("the user's own choice: a single option's side, or a structure and its width", () => {
    const base = paperBotRequest(F1, "NIFTY 50", intraday);
    expect(withConstruction(base, "", null)).toBe(base);
    const write = withConstruction(base, "WRITE", null);
    expect(write).toMatchObject({ option_position: "WRITE" });
    expect(constructionProblem(write)).toBeNull();
    const spread = withConstruction(base, "BULL_PUT_SPREAD", null);
    expect(spread).toMatchObject({ option_strategy: "BULL_PUT_SPREAD" });
    expect(spread).not.toHaveProperty("option_position");
    expect(needsWidth(spread)).toBe(true);
    expect(constructionProblem(spread)).toBe("Choose the width of the structure to start.");
    const sized = withConstruction(base, "BULL_PUT_SPREAD", 3);
    expect(sized).toMatchObject({ option_strategy: "BULL_PUT_SPREAD", spread_width: 3 });
    expect(constructionProblem(sized)).toBeNull();
    // a structure is exited on its credit / debit: a premium stop from the defaults is not sent with it (the API refuses it)
    const withStop = paperBotRequestWithDefaults(F1, "NIFTY 50", intraday, d({ premium_stop_pct: 40 }), false).request;
    expect(withStop.premium_stop_pct).toBe(40);
    expect(withConstruction(withStop, "IRON_CONDOR", 2)).not.toHaveProperty("premium_stop_pct");
    expect(withConstruction(withStop, "WRITE", null).premium_stop_pct).toBe(40);
    // a straddle has no width to choose
    expect(constructionProblem(withConstruction(base, "SHORT_STRADDLE", null))).toBeNull();
    // a request that already says how it trades is never changed by the form's choice
    const bought = paperBotRequest("orb_15m_5m", "NIFTY 50", intraday);
    expect(withConstruction(bought, "WRITE", null)).toBe(bought);
  });

  it("the width is whole strike steps from 1 to 20, else not chosen", () => {
    expect(widthOf("3")).toBe(3);
    expect(widthOf(" 20 ")).toBe(20);
    for (const t of ["", "0", "21", "2.5", "-1", "abc"]) expect(widthOf(t)).toBeNull();
  });

  it("the user's own defaults fill it: a structure, a side (buy too), and no width is invented", () => {
    const structure = paperBotRequestWithDefaults(F1, "NIFTY 50", intraday, d({ option_strategy: "IRON_CONDOR", spread_width: 4 }), false);
    expect(structure.request).toMatchObject({ option_strategy: "IRON_CONDOR", spread_width: 4 });
    expect(constructionProblem(structure.request)).toBeNull();
    const noWidth = paperBotRequestWithDefaults(F1, "NIFTY 50", intraday, d({ option_strategy: "BEAR_CALL_SPREAD" }), false).request;
    expect(noWidth).not.toHaveProperty("spread_width");
    expect(constructionProblem(noWidth)).toBe("Choose the width of the structure to start.");
    const buy = paperBotRequestWithDefaults(F1, "NIFTY 50", intraday, d({ option_position: "BUY" }), false);
    expect(buy.request).toMatchObject({ option_position: "BUY" });
    expect(buy.applied).toEqual(["option buying"]);
    const write = paperBotRequestWithDefaults(F1, "NIFTY 50", intraday, d({ option_position: "WRITE" }), false);
    expect(write.request).toMatchObject({ option_position: "WRITE" });
    // not using the defaults: back to nothing chosen
    expect(needsConstruction(paperBotRequestWithDefaults(F1, "NIFTY 50", intraday, d({ option_position: "WRITE" }), false, false).request)).toBe(true);
  });

  it("a buy default on any other strategy changes nothing (the request already buys)", () => {
    const base = paperBotRequest("orb_15m_5m", "NIFTY 50", intraday);
    expect(paperBotRequestWithDefaults("orb_15m_5m", "NIFTY 50", intraday, d({ option_position: "BUY" }), false)).toEqual({ request: base, applied: [] });
  });
});

describe("a bot held for days: the same path, the API's swing rules said in words", () => {
  it("offers only a bought single option, still chosen by the user", () => {
    const req = paperBotRequestWithDefaults(F1_DAILY, "NIFTY 50", daily, null, false).request;
    expect(req.holding).toBe("SWING");
    expect(needsConstruction(req)).toBe(true);
    expect(constructionChoices(true)).toEqual([{ value: "BUY", label: "Buy a single option" }]);
    expect(constructionChoices(false).map((c) => c.value)).toEqual(expect.arrayContaining(["BUY", "WRITE", "BULL_PUT_SPREAD", "IRON_CONDOR"]));
    expect(constructionChoices(false).map((c) => c.value)).not.toContain("SINGLE");
    expect(SWING_CONSTRUCTION_NOTE).toBe("A bot held for days trades a single bought option; writing and option structures stay intraday.");
  });

  it("a structure or writing default is read and named, never dropped silently", () => {
    const ctx = { index: true, swing: true, approvalAllowed: false };
    expect(swingNotTaken(d({ option_strategy: "BULL_PUT_SPREAD" }), ctx, "OPTION"))
      .toBe("Your default bull put spread is not used here: a bot held for days trades a single bought option.");
    expect(swingNotTaken(d({ option_position: "WRITE" }), ctx, "OPTION"))
      .toBe("Your default option writing is not used here: a bot held for days trades a single bought option.");
    expect(swingNotTaken(d({ option_strategy: "BULL_PUT_SPREAD" }), { ...ctx, swing: false }, "OPTION")).toBeNull();
    expect(swingNotTaken(d({ option_strategy: "BULL_PUT_SPREAD" }), ctx, "UNDERLYING")).toBeNull();
    expect(swingNotTaken(d({ option_position: "BUY" }), ctx, "OPTION")).toBeNull();
    expect(swingNotTaken(null, ctx, "OPTION")).toBeNull();
    // the request itself keeps what the API takes for a swing bot: no structure, no writing
    const req = paperBotRequestWithDefaults(F1_DAILY, "NIFTY 50", daily, d({ option_strategy: "IRON_CONDOR", option_position: "WRITE" }), false).request;
    expect(req).not.toHaveProperty("option_strategy");
    expect(req.option_position).toBeUndefined();
  });
});
