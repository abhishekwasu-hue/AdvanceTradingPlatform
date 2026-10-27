import { afterEach, describe, expect, it } from "vitest";
import { BUILT_IN_STRATEGY_NAMES, botDisplayName, forgetStrategyNames, looksLikeSlug, rememberStrategyNames, strategyDisplayName } from "./strategyName";

afterEach(() => forgetStrategyNames());

describe("item 6: strategy and bot names are human-readable, never a slug", () => {
  it("the owner's example: orb_nifty reads as 'Opening Range Breakout · NIFTY 50'", () => {
    expect(strategyDisplayName("orb_nifty")).toBe("Opening Range Breakout · NIFTY 50");
    expect(botDisplayName("orb_nifty", "NIFTY")).toBe("Opening Range Breakout · NIFTY 50");    // the symbol is not repeated
    expect(strategyDisplayName("orb_nifty", null, { index: false })).toBe("Opening Range Breakout");
  });

  it("built-in strategies use the backend registry's names", () => {
    expect(strategyDisplayName("orb_15m_5m")).toBe("Opening Range Breakout");
    expect(strategyDisplayName("vwap_supertrend_5m")).toBe("VWAP + Supertrend");
    expect(botDisplayName("swing_ema_pullback_d", "RELIANCE")).toBe("Swing EMA Pullback (daily) · RELIANCE");
  });

  it("a name the page read, or the shell remembered, wins over everything else", () => {
    expect(strategyDisplayName("custom:12")).toBe("My strategy");
    rememberStrategyNames([["custom:12", "My gap fill"], ["custom:13", "  "]]);
    expect(strategyDisplayName("custom:12")).toBe("My gap fill");
    expect(strategyDisplayName("custom:13")).toBe("My strategy");                              // a blank name is ignored
    expect(strategyDisplayName("orb_15m_5m", new Map([["orb_15m_5m", "Opening range (15 min)"]]))).toBe("Opening range (15 min)");
  });

  it("templates and saved strategies without a known name get plain words", () => {
    expect(strategyDisplayName("template:D2:1:abc")).toBe("Strategy template");
    expect(strategyDisplayName("template:D2:1:abc", new Map([["template:D2", "Pullback in a trend"]]))).toBe("Pullback in a trend");
    expect(strategyDisplayName("custom_my_breakout")).toBe("My breakout");
    expect(strategyDisplayName("ema_rsi_pullback_15m")).toBe("EMA RSI pullback");
    expect(strategyDisplayName("banknifty_macd_cross_1h")).toBe("MACD cross · NIFTY BANK");
    expect(strategyDisplayName("")).toBe("");
    expect(strategyDisplayName(null)).toBe("");
  });

  it("guard: no id - built-in, odd or made up - is ever shown as a slug or in lower case", () => {
    const ids = [...Object.keys(BUILT_IN_STRATEGY_NAMES), "orb_nifty", "custom:12", "custom-7", "template:W1:2:x", "my_cool_strategy_5m",
      "rsi_div", "x_y", "ORB_NIFTY", "orb", "sr_flip_d", "nifty"];
    for (const id of ids) {
      for (const shown of [strategyDisplayName(id), botDisplayName(id, "RELIANCE")]) {
        expect(looksLikeSlug(shown), `${id} -> ${shown}`).toBe(false);
        expect(shown, id).not.toMatch(/[_:]/);
        expect(shown.charAt(0), id).toMatch(/[A-Z]/);
      }
    }
  });

  it("looksLikeSlug spots ids and leaves names alone", () => {
    for (const slug of ["orb_nifty", "custom:12", "template:D2", "vwap-supertrend-5m", "orb_15m_5m"]) expect(looksLikeSlug(slug), slug).toBe(true);
    for (const name of ["Opening Range Breakout · NIFTY 50", "VWAP + Supertrend", "My strategy", "Swing Breakout (daily)", "RELIANCE"]) {
      expect(looksLikeSlug(name), name).toBe(false);
    }
  });
});
