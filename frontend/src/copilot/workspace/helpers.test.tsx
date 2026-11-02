import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Leg } from "../../optionsBuilder/model";
import { addHedge, atmOf, mirrorLegs, reverseLegs, rollExpiry, rollStrikes, savedHelpers, solvedIvs } from "./helpers";
import helpersSource from "./helpers.ts?raw";
import componentSource from "./PlanHelpersV2.tsx?raw";
import panelSource from "./PlanPanelV2.tsx?raw";
import { PlanHelpersV2 } from "./PlanHelpersV2";

/** M76 MP-8 (E-1): the helpers only prepare the plan's legs - from his legs, his step, the spot and his saved values. */
const leg = (over: Partial<Leg>): Leg => ({ id: "s", direction: "SELL", option_type: "PE", strike: 24400, premium: 60, lots: 2, lot_size: 75,
  expiry: "2031-03-27", iv: null, premium_source: "manual", ...over });
const SPREAD = [leg({ id: "h", direction: "BUY", strike: 24300, premium: 35 }), leg({ id: "s" })];
const IV = { h: 0.15, s: 0.14 };
const ok = (r: ReturnType<typeof reverseLegs>) => { if ("missing" in r) throw new Error(r.missing); return r.legs; };

describe("plan helpers", () => {
  it("reverse turns every side and keeps the contracts, prices and lots", () => {
    const out = ok(reverseLegs(SPREAD));
    expect(out.map((l) => [l.direction, l.strike, l.premium, l.lots, l.premium_source])).toEqual([["SELL", 24300, 35, 2, "manual"], ["BUY", 24400, 60, 2, "manual"]]);
    expect(reverseLegs([])).toEqual({ missing: "a leg" });
  });

  it("mirror reflects strikes around the ATM strike, CE <-> PE, model-priced at the solved IV", () => {
    expect(atmOf(24512, 50)).toBe(24500);
    expect(atmOf(null, 50)).toBeNull();
    const out = ok(mirrorLegs(SPREAD, 24500, IV));
    expect(out.map((l) => [l.direction, l.option_type, l.strike, l.iv, l.premium_source, l.lots])).toEqual([
      ["BUY", "CE", 24700, 0.15, "model", 2], ["SELL", "CE", 24600, 0.14, "model", 2]]);
    expect(out.every((l) => l.priced_for === undefined)).toBe(true);           // re-priced by the builder's rule on the next reply
    expect(mirrorLegs(SPREAD, null, IV)).toEqual({ missing: "the spot and the strike step" });
    expect(mirrorLegs(SPREAD, 24500, {})).toEqual({ missing: "the plan's numbers (wait for them)" });
    expect("missing" in mirrorLegs([leg({ option_type: "FUT" })], 24500, IV)).toBe(true);
  });

  it("roll strikes by his one step; roll expiry to his date; a future keeps its own", () => {
    expect(ok(rollStrikes(SPREAD, 1, 50, IV)).map((l) => l.strike)).toEqual([24350, 24450]);
    expect(ok(rollStrikes(SPREAD, -1, 50, IV)).map((l) => l.strike)).toEqual([24250, 24350]);
    expect(rollStrikes(SPREAD, 1, null, IV)).toEqual({ missing: "the strike step" });
    const fut = leg({ id: "f", option_type: "FUT", strike: 24500, premium: 24500, iv: null });
    const rolled = ok(rollExpiry([...SPREAD, fut], "2031-04-24", IV));
    expect(rolled.map((l) => l.expiry)).toEqual(["2031-04-24", "2031-04-24", "2031-03-27"]);
    expect(rolled[2]).toBe(fut);
    expect(rollExpiry(SPREAD, "", IV)).toEqual({ missing: "the new expiry" });
    expect(rollExpiry(SPREAD, "2031-03-27", IV)).toEqual({ missing: "an expiry different from the plan's" });
  });

  it("add hedge: his distance only (saved or typed), a bought leg further out per sold option, never a duplicate", () => {
    const naked = [leg({ id: "s" }), leg({ id: "c", option_type: "CE", strike: 24600 })];
    const out = ok(addHedge(naked, 2, 50, { s: 0.14, c: 0.13 }));
    expect(out.slice(0, 2).map((l) => [l.direction, l.option_type, l.strike, l.lots, l.expiry])).toEqual([
      ["BUY", "PE", 24300, 2, "2031-03-27"], ["BUY", "CE", 24700, 2, "2031-03-27"]]);
    expect(addHedge(naked, null, 50, IV)).toEqual({ missing: "how many strikes further out" });
    expect(addHedge([leg({ direction: "BUY" })], 2, 50, IV)).toEqual({ missing: "a sold option leg" });
    expect(addHedge(SPREAD, 2, 50, IV)).toEqual({ missing: "a hedge not in the plan already" });
  });

  it("saved values are only the fields he saved; the solved IVs are the engine's", () => {
    expect(savedHelpers([{ key: "options.hedge_offset", value: 3, saved: true }, { key: "position_size.mode", value: "FIXED_LOTS", saved: true },
      { key: "position_size.lots", value: 2, saved: true }])).toEqual({ hedgeOffset: 3, lots: 2 });
    expect(savedHelpers([{ key: "options.hedge_offset", value: 3, saved: false }, { key: "position_size.lots", value: 2, saved: true }]))
      .toEqual({ hedgeOffset: null, lots: null });                              // not saved / size not by fixed lots: nothing
    expect(savedHelpers(undefined)).toEqual({ hedgeOffset: null, lots: null });
    expect(solvedIvs(SPREAD, [{ iv: 0.15 }, { iv: 0.14 }])).toEqual(IV);
    expect(solvedIvs(SPREAD, [{ iv: 0.15 }])).toEqual({});
  });
});

describe("the helpers panel", () => {
  it("prepares only: no order call anywhere in the helpers, and Place stays the one ticket's", () => {
    for (const s of [helpersSource, componentSource]) {
      expect(s).not.toMatch(/request\(|fetch\(|\/order|basket\/execute|placeOrder|exit-now/);
    }
    expect(panelSource).toContain("<PlanHelpersV2 ");
    expect(panelSource).toContain("repriceModelLegs(legs, evaluation)");
    const html = renderToStaticMarkup(<PlanHelpersV2 legs={SPREAD} atm={24500} step={50} ivById={IV} savedHedgeOffset={null} onLegs={() => undefined} />);
    expect(html).toContain("Nothing is sent until you press Place.");
    expect(html).toContain('placeholder="Not set"');                         // no distance pre-filled without his saved one
    const saved = renderToStaticMarkup(<PlanHelpersV2 legs={SPREAD} atm={24500} step={50} ivById={IV} savedHedgeOffset={3} onLegs={() => undefined} />);
    expect(saved).toContain('value="3"');
  });
});
