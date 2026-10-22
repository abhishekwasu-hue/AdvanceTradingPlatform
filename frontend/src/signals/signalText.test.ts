import { describe, expect, it } from "vitest";
import { hasCallWords } from "../chartAnalyze/signalLanguage";
import signalTextSource from "./signalText.ts?raw";
import { hasPrice as languageHasPrice, isClean } from "./language";
import { SETUP_FALLBACK, hasPrice, isStructureText, reasonWords, setupWords } from "./signalText";

/**
 * #365 / #366 overlap: signalText.ts (UX3) now takes its call-word and price checks from the one signal-language module
 * (language.ts). The answers must be the same as before: a frozen copy of the old checks runs on a corpus.
 */

// Frozen copy of the old signalText.ts checks (before the delegation).
const OLD_PRICE = /(?:\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\b\d{3,}(?:\.\d+)?\b|\b\d+\.\d+\b)/;
const oldHasPrice = (text: string) => OLD_PRICE.test(text);
const oldIsStructure = (text: string) => { const t = text.trim(); return t !== "" && !hasCallWords(t) && !oldHasPrice(t); };
const oldSetup = (setup: string | null | undefined) => { const t = (setup ?? "").trim(); return oldIsStructure(t) ? t : SETUP_FALLBACK; };
const oldReasons = (reasons: readonly string[]) => {
  const out: string[] = [];
  for (const r of reasons) { const t = r.trim(); if (oldIsStructure(t) && !out.includes(t)) out.push(t); }
  return out;
};

const CORPUS = [
  "", "  ", "Higher low held", "Range broke on volume", "Buy the dip", "buyer area held", "Seller area reached", "SELL",
  "Sold into strength", "stops below the swing", "Stop-loss moved", "stoploss hit", "SL trailed", "sl is a word here",
  "Target 24,930 in sight", "targeting the high", "Entry 24,790.00", "above 24790", "near 1,35,000.50", "at 99.5",
  "Price above EMA 20", "15m opening range", "RSI divergence on 2 swings", "The pullback reached a seller area; a handover is forming",
  "Opening-range breakout, retest held", "Bought at the open", "1,000 lots", "999", "12.0", "v2 rules", "BUYING pressure",
  "Exit if wrong", "Exit at profit", "A setup from your strategy", "Bullish RSI divergence on the 1-hour chart",
  "NIFTY · Seller area: 23,000–23,100. The market is in a downtrend.", "Stops", "targets", "sell-side liquidity swept",
];

describe("signalText delegates to language.ts with identical outputs", () => {
  it("the checks come from language.ts, not a second copy", () => {
    expect(signalTextSource).toContain('from "./language"');
    expect(signalTextSource).not.toMatch(/const PRICE\s*=|chartAnalyze\/signalLanguage/);
    expect(hasPrice).toBe(languageHasPrice);
  });

  it("every corpus line gives the same answers as the frozen old checks", () => {
    for (const text of CORPUS) {
      expect(hasPrice(text), `hasPrice ${text}`).toBe(oldHasPrice(text));
      expect(!isClean(text.trim()), `call words ${text}`).toBe(hasCallWords(text.trim()));
      expect(isStructureText(text), `isStructureText ${text}`).toBe(oldIsStructure(text));
      expect(setupWords(text), `setupWords ${text}`).toBe(oldSetup(text));
    }
    expect(setupWords(null)).toBe(oldSetup(null));
    expect(setupWords(undefined)).toBe(oldSetup(undefined));
    expect(reasonWords(CORPUS)).toEqual(oldReasons(CORPUS));
  });
});
