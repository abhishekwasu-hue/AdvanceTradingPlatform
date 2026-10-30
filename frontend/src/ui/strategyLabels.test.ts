import { describe, expect, it } from "vitest";
import { BANNED_UI_WORDS } from "../ux/bannedUiWords";
import { INDICATOR_LABEL, exitRuleWords, libraryChips, metricLabel, paramChips, paramLabel, timeframeWords, wordsOf } from "./strategyLabels";

describe("SB-UX SU8: the one strategy label map", () => {
  it("names every engine indicator in plain words, never by its id", () => {
    expect(INDICATOR_LABEL.PDH).toBe("Previous day high");
    expect(INDICATOR_LABEL.OR_HIGH).toBe("Opening range high");
    for (const [id, label] of Object.entries(INDICATOR_LABEL)) {
      if (!["EMA", "SMA", "RSI", "ADX", "ATR", "VWAP"].includes(id)) expect(label).not.toBe(id);
      expect(label).not.toMatch(/_/);
    }
  });

  it("writes settings as chips, never JSON", () => {
    const chips = paramChips({ ema_fast: 9, target_rr: [1.5, 2], tf: "5min", last_entry: "14:30", pullback_bars: 3, flag: false });
    expect(chips).toEqual(["Fast EMA 9", "Targets (R) 1.5 / 2", "Timeframe 5-minute", "Last entry at 14:30", "Pullback bars 3", "Flag Off"]);
    for (const c of chips) expect(c).not.toMatch(/[{}[\]"_]/);
    expect(paramChips({})).toEqual([]);
    expect(paramChips(null)).toEqual([]);
  });

  it("REV-B2: the Library never shows a built-in's no-op reward:risk chip", () => {
    expect(libraryChips({ ema_fast: 9, target_rr: [1.5, 2.5], min_rr: 1.2 })).toEqual(["Fast EMA 9", "Targets (R) 1.5 / 2.5"]);
    expect(libraryChips({ ema_fast: 9 })).toEqual(paramChips({ ema_fast: 9 }));
    expect(libraryChips(null)).toEqual([]);
  });

  it("falls back to words for an unknown key and names the scores and exits", () => {
    expect(wordsOf("htf_ema_x")).toBe("Htf ema x");
    expect(paramLabel("rsi_period")).toBe("RSI length");
    expect(metricLabel("net_pnl")).toBe("Net P&L");
    expect(timeframeWords("15min")).toBe("15-minute");
    expect(exitRuleWords({ trailing_stop_pct: 1.5, break_even_at_r: 1, time_exit_minutes: 30, time_exit_at: "15:00" }))
      .toBe("Trailing stop 1.5%, Break-even at 1 R, Exit after 30 min, Flat at 15:00");
    expect(exitRuleWords(null)).toBe("None");
  });

  it("no label trips the banned-words guard", () => {
    const all = [...Object.values(INDICATOR_LABEL), ...paramChips({ ema_fast: 9, rsi_period: 14, atr_mult_sl: 1, st_mult: 3 })];
    for (const w of BANNED_UI_WORDS) for (const text of all) expect(text, w.name).not.toMatch(w.pattern);
  });
});
