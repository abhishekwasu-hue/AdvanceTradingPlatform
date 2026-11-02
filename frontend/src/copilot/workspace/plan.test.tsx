import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import panelSource from "./PlanPanelV2.tsx?raw";
import { PlanPanelV2 } from "./PlanPanelV2";
import {
  EMPTY_EQUITY, EMPTY_EXITS, EMPTY_INSTRUMENT, EMPTY_LEG, exitLevel, exitLines, legMissing, legOf, savedExits, singleEntry,
  type LegDraft,
} from "./plan";
import { wireLegs } from "../../optionsBuilder/api";

/**
 * M75 W-6 (owner order M75 CW-4; the PR plan's W-6 row): the trade plan panel. Proofs here: every field Not set until he
 * fills it (M68), a leg reaches the builder's engine only when complete and exactly as the builder sends it, the exits
 * at entry use the M68 Exits fields with his own numbers, the margin is a labelled estimate, and the panel places nothing.
 * e2e/copilot-workspace-v2.e2e.ts proves the same in a browser (a leg added, the builder's numbers shown, phone sheet).
 */
const INST = { spot: "24500", lotSize: "75", step: "50" };
const FULL: LegDraft = { direction: "SELL", optionType: "CE", strike: "24600", expiry: "2026-11-25", lots: "2", premium: "85" };

describe("W-6: nothing is filled in for him (M68)", () => {
  it("the empty plan has no side, type, strike, lots, price or exit", () => {
    expect(EMPTY_LEG).toEqual({ direction: null, optionType: null, strike: "", expiry: "", lots: "", premium: "" });
    expect(EMPTY_INSTRUMENT).toEqual({ spot: "", lotSize: "", step: "" });
    expect(EMPTY_EQUITY).toEqual({ direction: null, quantity: "", price: "" });
    expect(Object.values(EMPTY_EXITS).every((v) => v === null || v === "")).toBe(true);
  });
  it("a new leg lists each field still Not set; it is added only when none is left", () => {
    expect(legMissing(EMPTY_LEG, EMPTY_INSTRUMENT, "OPTIONS")).toEqual(["Lot size", "Side", "Type", "Strike", "Expiry", "Lots", "Premium"]);
    expect(legOf({ ...FULL, lots: "" }, INST, "OPTIONS")).toBeNull();
    expect(legMissing(FULL, INST, "OPTIONS")).toEqual([]);
    // a future needs no strike and no type (it is a future); its price is asked as "Price"
    expect(legMissing({ ...EMPTY_LEG, direction: "BUY" }, INST, "FUTURE")).toEqual(["Expiry", "Lots", "Price"]);
  });
  it("the panel opens with only the instrument question, no field pre-picked", () => {
    const html = renderToStaticMarkup(<PlanPanelV2 symbol="NIFTY 50" evaluate={() => Promise.reject(new Error("not called"))} />);
    expect(html).toContain('data-plan-panel="none"');
    expect(html).toContain('data-plan="ask-kind"');
    expect(html).not.toContain('aria-checked="true"');
    expect(html).not.toContain('data-plan="place"');                     // W-7: nothing to place before he picks one
  });
});

describe("W-6: the legs are the Options Builder's (the same numbers for the same legs)", () => {
  it("a complete leg is the builder's leg, sent on the builder's wire unchanged", () => {
    const leg = legOf(FULL, INST, "OPTIONS");
    expect(leg).not.toBeNull();
    expect(wireLegs([leg!])).toEqual([{ direction: "SELL", option_type: "CE", strike: 24600, premium: 85, lots: 2, lot_size: 75, expiry: "2026-11-25", iv: null }]);
  });
  it("a future's entry price is its premium (the engine ignores a future's strike)", () => {
    const fut = legOf({ ...EMPTY_LEG, direction: "BUY", expiry: "2026-11-25", lots: "1", premium: "24550" }, INST, "FUTURE");
    expect(fut).toMatchObject({ option_type: "FUT", premium: 24550, strike: 24550 });
  });
  it("the panel reuses the builder's engine call and parts, and calls no order route itself (W-7's PlanPlaceV2 does, on his tap)", () => {
    for (const part of ["builderApi.evaluate", "<LegTable", "<PayoffCanvas", "<MetricsCard", "wireLegs(sent)"]) expect(panelSource).toContain(part);
    for (const route of ["/paper/fno/order", "/paper/ticket", "fnoApi", "basketApi", ".place("]) expect(panelSource).not.toContain(route);
  });
  it("the equity margin is labelled an estimate", () => {
    expect(panelSource).toContain("Margin (estimate)");
  });
});

describe("W-6: exits at entry (the M68 Exits fields, his own numbers)", () => {
  const legs = [legOf(FULL, INST, "OPTIONS")!];
  const entry = singleEntry("OPTIONS", legs, EMPTY_EQUITY)!;
  it("one leg is measured from its premium and units; a structure has no single entry", () => {
    expect(entry).toEqual({ direction: "SELL", price: 85, units: 150 });
    expect(singleEntry("OPTIONS", [...legs, ...legs], EMPTY_EQUITY)).toBeNull();
    expect(singleEntry("EQUITY", [], { direction: "BUY", quantity: "10", price: "2500" })).toEqual({ direction: "BUY", price: 2500, units: 10 });
  });
  it("a sold leg's stop is above its entry, its target below; points, % and rupees", () => {
    expect(exitLevel(entry, "POINTS", 20, "stop")).toEqual({ level: 105, rupees: 3000 });
    expect(exitLevel(entry, "PCT", 50, "target")).toEqual({ level: 42.5, rupees: 6375 });
    expect(exitLevel(entry, "RUPEES", 1500, "stop")).toEqual({ level: 95, rupees: 1500 });
    const bought = { direction: "BUY" as const, price: 100, units: 10 };
    expect(exitLevel(bought, "POINTS", 10, "stop")).toEqual({ level: 90, rupees: 100 });
    expect(exitLevel(bought, "POINTS", 200, "stop")).toBeNull();      // below zero: no level
  });
  it("a line only for each exit he set; nothing for a field left Not set", () => {
    expect(exitLines(EMPTY_EXITS, entry)).toEqual([]);
    const lines = exitLines({ ...EMPTY_EXITS, stopKind: "POINTS", stop: "20", trailKind: "ATR", trail: "1.5", timeMinutes: "90" }, entry);
    expect(lines.map((l) => l.key)).toEqual(["stop", "trail", "time"]);
    expect(lines[0].text).toContain("at 105");
    expect(exitLines({ ...EMPTY_EXITS, stopKind: "POINTS" }, entry)).toEqual([]);        // a measure without a value: nothing
  });
  it("'Use my saved exits' takes only the fields he saved, with the registry keys", () => {
    const fields = [
      { key: "exits.stop_loss_kind", value: "PCT", saved: true }, { key: "exits.stop_loss_value", value: 30, saved: true },
      { key: "exits.target_value", value: 50, saved: false }, { key: "exits.trailing_kind", value: "BOGUS", saved: true },
      { key: "exits.time_exit_at", value: "15:10:00", saved: true }, { key: "exits.combined_measure", value: "MTM_INR", saved: true },
    ];
    expect(savedExits(fields)).toEqual({ stopKind: "PCT", stop: "30", timeAt: "15:10", combined: "MTM_INR" });
    expect(savedExits([])).toEqual({});
  });
});
