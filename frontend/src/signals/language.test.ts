import { describe, expect, it } from "vitest";
import cases from "./__fixtures__/signal_language.json";
import {
  areaLabel, callWords, check, formatSignal, groupIn, isClean, setupNotice, shownLines, withheld, WITHHELD, type SignalStructure, type Surface,
} from "./language";
import { guardText, hasCallWords, WITHHELD as CHART_WITHHELD } from "../chartAnalyze/signalLanguage";

/** Handover 11l: the browser runs the same signal-language cases as the server. The fixture is a byte-identical copy of
 * backend/tests/fixtures/signal_language.json (backend/tests/test_signal_language.py checks the copy and runs the same
 * cases against app/signals/language.py). */

describe("the shared signal-language cases", () => {
  it("the formatter writes the same text as the server", () => {
    expect(cases.format.length).toBeGreaterThan(8);
    for (const c of cases.format) {
      expect(formatSignal(c.structure as SignalStructure, c.surface as Surface)).toBe(c.text);
    }
  });

  it("the formatter refuses what it has no words for", () => {
    for (const c of cases.refuse) expect(() => formatSignal(c.structure as SignalStructure, c.surface as Surface)).toThrow();
  });

  it("the checker finds the same problems as the server", () => {
    for (const c of cases.check) {
      expect({ text: c.text, problems: check(c.text, c.surface as Surface, { names: c.names, ownSymbols: c.own_symbols }) })
        .toEqual({ text: c.text, problems: c.problems });
    }
  });

  it("call words, the digit grouping and the setup notice match", () => {
    for (const c of cases.call_words) expect(callWords(c.text)).toEqual(c.words);
    for (const [value, text] of cases.group_in) expect(groupIn(value as number)).toBe(text);
    for (const c of cases.setup_notice) expect(setupNotice(c.label, c.symbol, c.direction, c.strategy_name, c.reasons)).toEqual(c.out);
  });
});

describe("the signal language", () => {
  it("a public signal never shows the symbol or the area, whatever is passed", () => {
    const text = formatSignal({ trend: "down", side: "seller", steps: ["pullback_reached_area"], symbol: "RELIANCE", area_range: [2800, 2810] }, "signal_public");
    expect(text).toBe("The market is in a downtrend; the pullback reached a seller area.");
    expect(check(text, "signal_public", { names: cases.security_names })).toEqual([]);
  });

  it("an own signal shows his symbol and the area as a range only", () => {
    expect(areaLabel("seller", 23100, 23000)).toBe("Seller area: 23,000–23,100");
    const text = formatSignal({ side: "seller", steps: ["handover_forming"], symbol: "nifty", area_range: [23000, 23100] }, "signal_own");
    expect(text).toBe("NIFTY · Seller area: 23,000–23,100. A handover is forming inside the area.");
    expect(check(text, "signal_own", { ownSymbols: ["NIFTY"] })).toEqual([]);
    expect(check("NIFTY at 23,050", "signal_own", { ownSymbols: ["NIFTY"] })).toEqual([{ kind: "number", detail: "23,050" }]);
  });

  it("withheld and shown lines", () => {
    expect(withheld("Buy above the high", "signal_own")).toBe(WITHHELD);
    expect(shownLines(["RSI 62", "a handover is forming", "target hit"], "signal_own")).toEqual(["a handover is forming"]);
    expect(isClean("the buyer area")).toBe(true);
  });

  it("Analyze a chart runs the shared call-word list (the same answers as before)", () => {
    for (const c of cases.call_words) expect(hasCallWords(c.text)).toBe(c.words.length > 0);
    expect(guardText("Sell below the low")).toBe(CHART_WITHHELD);
    expect(guardText("The seller area held")).toBe("The seller area held");
    expect(hasCallWords("sl is lower case")).toBe(false);
    expect(hasCallWords("SL")).toBe(true);
  });
});
