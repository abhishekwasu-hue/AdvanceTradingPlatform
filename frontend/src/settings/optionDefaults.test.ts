import { describe, expect, it } from "vitest";
import { EMPTY_LEG, completeLegs, optionDefaultsOf, optionsMissing, savedLegsOf, withOwnOptions, type OptionChoice } from "./optionDefaults";
import type { TradeSettingsView } from "./tradeSettings";

/** M68 PR-4 (TS1-035 to 040, 042): no option choice is pre-selected; his own Trade settings › Options pre-fill; else asked. */

const IDS = { kind: "k", position: "p", expiry: "e", strike: "s", offset: "o", width: "w", ratio: "r", legs: "l" };
const base: OptionChoice = { kind: "OPTION", structure: "SINGLE", position: "", expiryRule: "", strikeRule: "", strikeOffset: 0, spreadWidth: 0, ratio: 0, legs: [] };

function view(values: Record<string, unknown>): TradeSettingsView {
  const fields = Object.entries(values).map(([key, value]) => ({ key, value, saved: true }));
  return { sections: [{ id: "options", title: "Options", fields }] } as unknown as TradeSettingsView;
}

describe("optionsMissing: the form asks, ATP never picks", () => {
  it("asks what to trade, then the expiry, the side, the strike and its distance - in that order", () => {
    expect(optionsMissing({ ...base, kind: "" }, IDS)?.field).toBe("k");
    expect(optionsMissing(base, IDS)).toMatchObject({ field: "e", text: expect.stringContaining("Needs your setting: Expiry") });
    expect(optionsMissing({ ...base, expiryRule: "NEAREST" }, IDS)?.field).toBe("p");
    expect(optionsMissing({ ...base, expiryRule: "NEAREST", position: "BUY" }, IDS)?.field).toBe("s");
    expect(optionsMissing({ ...base, expiryRule: "NEAREST", position: "BUY", strikeRule: "OTM" }, IDS)?.field).toBe("o");
    expect(optionsMissing({ ...base, expiryRule: "NEAREST", position: "BUY", strikeRule: "OTM", strikeOffset: 2 }, IDS)).toBeNull();
    expect(optionsMissing({ ...base, expiryRule: "NEAREST", position: "BUY", strikeRule: "ATM" }, IDS)).toBeNull();
  });
  it("a future needs only its expiry; the underlying nothing", () => {
    expect(optionsMissing({ ...base, kind: "FUTURE" }, IDS)?.field).toBe("e");
    expect(optionsMissing({ ...base, kind: "FUTURE", expiryRule: "NEXT" }, IDS)).toBeNull();
    expect(optionsMissing({ ...base, kind: "UNDERLYING" }, IDS)).toBeNull();
  });
  it("a structure: its width, a ratio spread its ratio; a straddle names its own strike", () => {
    const e = { ...base, expiryRule: "NEAREST" as const };
    expect(optionsMissing({ ...e, structure: "BULL_PUT_SPREAD", strikeRule: "ATM" }, IDS)?.field).toBe("w");
    expect(optionsMissing({ ...e, structure: "CALL_RATIO_SPREAD", strikeRule: "ATM", spreadWidth: 2 }, IDS)?.field).toBe("r");
    expect(optionsMissing({ ...e, structure: "CALL_RATIO_SPREAD", strikeRule: "ATM", spreadWidth: 2, ratio: 3 }, IDS)).toBeNull();
    expect(optionsMissing({ ...e, structure: "SHORT_STRADDLE" }, IDS)).toBeNull();
    // a condor sells out of the money by its definition; how far is his own
    expect(optionsMissing({ ...e, structure: "IRON_CONDOR", spreadWidth: 2 }, IDS)?.field).toBe("o");
    expect(optionsMissing({ ...e, structure: "IRON_CONDOR", spreadWidth: 2, strikeOffset: 3 }, IDS)).toBeNull();
  });
  it("custom legs start empty and each leg must be drawn in full", () => {
    const e = { ...base, expiryRule: "NEAREST" as const, structure: "CUSTOM" as const };
    expect(optionsMissing(e, IDS)?.field).toBe("l");
    expect(optionsMissing({ ...e, legs: [EMPTY_LEG, EMPTY_LEG] }, IDS)?.text).toContain("every leg");
    const legs = [{ right: "PE", role: "SHORT", strike_rule: "ATM", strike_offset: 0, ratio: 1 }, { right: "PE", role: "LONG", strike_rule: "OTM", strike_offset: 25, ratio: 7 }] as const;
    expect(optionsMissing({ ...e, legs }, IDS)).toBeNull();
    expect(completeLegs(legs)).toHaveLength(2);
    expect(savedLegsOf(legs)).toEqual([{ right: "PE", side: "SELL", strike_rule: "ATM", offset: 0, ratio: 1 }, { right: "PE", side: "BUY", strike_rule: "OTM", offset: 25, ratio: 7 }]);
  });
});

describe("his own Trade settings › Options pre-fill", () => {
  it("reads only what he saved", () => {
    expect(optionDefaultsOf(null)).toEqual({});
    expect(optionDefaultsOf(view({ "options.expiry_rule": "NEXT", "options.strike_rule": "OTM", "options.strike_offset": 2, "options.option_position": "WRITE",
      "options.spread_width": 3, "options.ratio": 3, "options.custom_legs": [{ right: "CE", side: "SELL", strike_rule: "OTM", offset: 1, ratio: 1 }] })))
      .toEqual({ expiryRule: "NEXT", strikeRule: "OTM", strikeOffset: 2, position: "WRITE", spreadWidth: 3, ratio: 3,
        customLegs: [{ right: "CE", role: "SHORT", strike_rule: "OTM", strike_offset: 1, ratio: 1 }] });
  });
  it("fills a one-tap request only where it names nothing", () => {
    const own = { expiryRule: "NEXT" as const, position: "WRITE" as const, strikeRule: "OTM" as const, strikeOffset: 2 };
    expect(withOwnOptions({ instrument_kind: "OPTION" as const }, own)).toEqual({ instrument_kind: "OPTION", expiry_rule: "NEXT", option_position: "WRITE", strike_rule: "OTM", strike_offset: 2 });
    expect(withOwnOptions({ instrument_kind: "OPTION" as const, option_position: "BUY" as const }, own).option_position).toBe("BUY");
    expect(withOwnOptions({ instrument_kind: "UNDERLYING" as const }, own)).toEqual({ instrument_kind: "UNDERLYING" });
    expect(withOwnOptions({ instrument_kind: "OPTION" as const }, {})).toEqual({ instrument_kind: "OPTION" });
  });
});
