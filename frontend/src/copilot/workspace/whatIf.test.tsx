import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { wireLegs } from "../../optionsBuilder/api";
import type { Evaluation, Leg } from "../../optionsBuilder/model";
import type { AnnotationArea } from "./annotations";
import panelSource from "./PlanPanelV2.tsx?raw";
import source from "./WhatIfV2.tsx?raw";
import workspaceSource from "./ChartWorkspaceV2.tsx?raw";
import {
  EMPTY_SCENARIO, anyShift, areaRows, daysOf, readScenarios, scenarioRequest, scenarioRows, scenarioWords, shiftsOf, signed, slopeAt, toDraft, toSaved,
} from "./whatIf";
import { WhatIfV2, type WhatIfApi } from "./WhatIfV2";

/** M76 MP-6: what-if maths are the builder's curves read at a price; nothing is chosen for him. */
const LEGS = [{ id: "a", direction: "SELL", option_type: "CE", strike: 24700, premium: 85, lots: 2, lot_size: 75, expiry: "2026-11-25", iv: null } as Leg];
const PRICES = [24300, 24400, 24500, 24600, 24700, 24800];
function ev(today: number[], onDate = today, atExpiry: number[] | null = [12750, 12750, 12750, 12750, 12750, 5250]): Evaluation {
  return { prices: PRICES, today, on_date: onDate, at_expiry: atExpiry, days_forward: 0, iv_shift: 0, single_expiry: true, extremes: null, profitable: null,
    breakevens: [24785], summary: { expiry: "", t_years: 0, sigma: 0, pop: 0, expected_move: 0, expected_pnl: 0, greeks: { delta: 0, gamma: 0, theta: 0, vega: 0, rho: 0 }, method: "", model: "" },
    legs: [], disclaimer: "", net_premium: 0, reward_to_risk: null, margin: { value: null, estimated: true, basis: "", note: "" },
    costs: { to_open: { stt: 0, exch: 0, sebi: 0, stamp: 0, brokerage: 0, tax: 0, total: 0, other: 0 }, round_trip: 0, brokerage_per_order: 0, orders: 1, as_of: "", note: "" } } as Evaluation;
}
const BASE = ev([9000, 8000, 6000, 3000, -1000, -6000]);
const area = (over: Partial<AnnotationArea>): AnnotationArea => ({ role: "supply", side: "seller", low: 24600, high: 24700, low_text: "24,600", high_text: "24,700",
  timeframe: "1H", state: "fresh", state_words: null, tests: 0, words: "", ...over });

describe("what-if rules", () => {
  it("every shift blank = not applied; typed values parse as signed numbers", () => {
    expect(shiftsOf(EMPTY_SCENARIO)).toEqual({ spotMove: null, ivChange: null, days: null });
    expect(anyShift(shiftsOf(EMPTY_SCENARIO))).toBe(false);
    expect(signed("+100")).toBe(100);
    expect(signed("−2.5")).toBe(-2.5);
    expect(signed("abc")).toBeNull();
    expect(daysOf("3")).toBe(3);
    expect(daysOf("1.5")).toBeNull();
    expect(daysOf("-1")).toBeNull();
  });

  it("the request is the builder's with his IV points, days and a range that reaches the moved price", () => {
    const r = scenarioRequest(LEGS, 24500, "2026-11-02", { spotMove: 2000, ivChange: 3, days: 2 });
    expect(r.legs).toEqual(wireLegs(LEGS));
    expect(r).toMatchObject({ iv_shift: 0.03, days_forward: 2, spot: 24500, as_of: "2026-11-02", points: 241 });
    expect(r.range_pct).toBeGreaterThanOrEqual(Math.ceil((2000 / 24500) * 100 + 2));
    expect(scenarioRequest(LEGS, 24500, "2026-11-02", { spotMove: null, ivChange: null, days: null })).toMatchObject({ iv_shift: 0, days_forward: 0 });
  });

  it("scenario rows: now at spot, the scenario curve at spot + move, expiry, and the curve's slope", () => {
    const scen = ev([8800, 7600, 5400, 2000, -2200, -7400], [8500, 7200, 5000, 1500, -2600, -7900]);
    const rows = scenarioRows(BASE, scen, 24500, { spotMove: 100, ivChange: 2, days: 1 });
    const v = (k: string) => rows.find((r) => r.key === k);
    expect(v("price")?.value).toBe("24,600.00");
    expect(v("now")).toMatchObject({ value: "+₹6,000", tone: "up" });
    expect(v("scenario")).toMatchObject({ value: "+₹1,500", sub: "−₹4,500 against now" });        // on_date, as days > 0
    expect(v("expiry")?.value).toBe("+₹12,750");
    expect(v("delta")?.value).toBe("−₹35.00 per point");                                            // (1500 - 5000) / 100
    const t0 = scenarioRows(BASE, scen, 24500, { spotMove: 100, ivChange: null, days: null });
    expect(t0.find((r) => r.key === "scenario")?.value).toBe("+₹2,000");                            // today's curve at T+0
    expect(scenarioRows(BASE, scen, 24500, { spotMove: 5000, ivChange: null, days: null }).find((r) => r.key === "scenario")?.value).toBe("Outside the payoff range");
    expect(slopeAt(PRICES, null, 24500)).toBeNull();
    expect(scenarioWords({ spotMove: -50, ivChange: 2, days: 1 })).toBe("Spot −50 points · IV +2 points · 1 day forward");
  });

  it("area rows: the plan at both edges of each next area of the reading, today and at expiry", () => {
    const rows = areaRows(BASE, { above: area({}), inside: null, below: area({ side: "buyer", role: "demand", low: 24300, high: 24400, low_text: "24,300", high_text: "24,400", timeframe: "Daily" }) });
    expect(rows.map((r) => r.key)).toEqual(["above", "below"]);
    expect(rows[0]).toMatchObject({ label: "Next seller area above (1H)", range: "24,600–24,700", today: "+₹3,000 to −₹1,000", expiry: "+₹12,750 to +₹12,750", tone: "fg" });
    expect(rows[1]).toMatchObject({ label: "Next buyer area below (Daily)", today: "+₹9,000 to +₹8,000", tone: "up" });
    expect(areaRows(BASE, { above: area({ low: 30000, high: 30100 }), inside: null, below: null })).toEqual([]);   // outside the payoff range
    expect(areaRows(BASE, null)).toEqual([]);
  });

  it("saved scenarios: his name and at least one shift; malformed prefs are left out", () => {
    expect(toSaved("", { spotMove: "100", ivChange: "", days: "" })).toBeNull();
    expect(toSaved("Gap up", EMPTY_SCENARIO)).toBeNull();
    const s = toSaved("Gap up", { spotMove: "150", ivChange: "-2", days: "" });
    expect(s).toEqual({ name: "Gap up", spot_move: 150, iv_change: -2, days: null });
    expect(toDraft(s!)).toEqual({ spotMove: "150", ivChange: "-2", days: "" });
    expect(readScenarios({ scenarios: [s, { bad: 1 }, null] })).toEqual([s]);
    expect(readScenarios(null)).toEqual([]);
  });
});

describe("WhatIfV2", () => {
  it("opens with every shift Not applied, calls no order route, and the payoff shows the scenario", () => {
    const api: WhatIfApi = { evaluate: async () => BASE, load: async () => ({ list: [], version: 0 }), save: async () => ({ list: [], version: 1 }) };
    const html = renderToStaticMarkup(<WhatIfV2 base={BASE} legs={LEGS} spot={24500} asOf="2026-11-02" annotations={null} api={api} />);
    expect((html.match(/placeholder="Not applied"/g) ?? []).length).toBe(3);
    expect(html).not.toContain("data-whatif=\"rows\"");
    expect(source).not.toMatch(/\/paper\/|\/orders?\b|placeOrder/);
    expect(panelSource).toMatch(/evaluation=\{scenario\?\.evaluation \?\? evaluation\.evaluation\}/);
    // the areas reach the plan only from the underlying's own chart, never a contract's (its prices are not the spot)
    expect(workspaceSource).toMatch(/read\.status === "ready" && !isContract\(pick\.exchange\) \? read\.read\.annotations/);
  });
});
