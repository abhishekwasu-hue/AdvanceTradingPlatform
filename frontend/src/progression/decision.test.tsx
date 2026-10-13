import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { RuinCalc, WhatIfResult } from "../types";
import { RuinResultView, WhatIfView } from "./DecisionToolsCard";
import { chartGeometry, pnl, RUIN_DEFAULTS, ruinBody, ruinPrefill } from "./decision";
import { LOSING, OK, TOO_FEW } from "./samples";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&lt;/g, "<").replace(/&amp;/g, "&").replace(/\s+/g, " ");

// As the server answers calculate(0.6, 1.0, 2.0, 20.0) (tests/test_n2_decision_tools.py).
const CALC: RuinCalc = {
  inputs: { win_rate: 0.6, payoff_ratio: 1, risk_pct: 2, ruin_pct: 20 }, expectancy_r: 0.2,
  kelly: { fraction: 0.2, status: "ok", formula: "f = (p·b − q) / (b·(p + q))" },
  rows: [
    { risk_pct: 0.5, ruin_probability: 8.97e-8, ruin_status: "ok", losses_to_ruin: 40, growth_per_trade_pct: 0.0975, chosen: false },
    { risk_pct: 2, ruin_probability: 0.01734, ruin_status: "ok", losses_to_ruin: 10, growth_per_trade_pct: 0.3801, chosen: true },
    { risk_pct: 50, ruin_probability: 0.6, ruin_status: "ok", losses_to_ruin: 1, growth_per_trade_pct: -3.42, chosen: false },
  ],
  method: "lundberg_upper_bound", note: "A model of these inputs, not a forecast; nothing here sizes or places a trade",
};

const WI: WhatIfResult = {
  underlying: "NIFTY 50", skipped: [], prices: [90, 100, 110], curves: { now: [-5, 0, 5], scenario: [-4, 1, 6], expiry: [-10, 0, 10] },
  at_spot: { now: 0, scenario: 1250.4, expiry: -480 }, greeks: { delta: 0.5, gamma: 0.01, theta: -2.5, vega: 3.25, rho: 0 },
  spot: 100, days_forward: 3, iv_shift: 0.02, max_days_forward: 8.2, expiry: "2026-10-20", scenario_at: "2026-10-15T05:00:00+00:00",
  as_of: "2026-10-12T05:00:00+00:00", legs: [], note: "Black-Scholes estimates at the spot and IVs you entered. A model, not a forecast.",
};

describe("decision helpers (N2-3)", () => {
  it("prefills from the user's own figures only when they are there", () => {
    expect(ruinPrefill(OK)).toEqual({ inputs: { winRatePct: "60.0", payoff: "2.00", riskPct: "1", ruinPct: "10" }, prefilled: true });
    expect(ruinPrefill(TOO_FEW)).toEqual({ inputs: RUIN_DEFAULTS, prefilled: false });
    expect(ruinPrefill(undefined).prefilled).toBe(false);
    expect(ruinPrefill(LOSING).inputs.winRatePct).toBe("25.0");
  });

  it("builds the request body or refuses unusable inputs", () => {
    expect(ruinBody({ winRatePct: "60", payoff: "1.5", riskPct: "2", ruinPct: "50" })).toEqual({ win_rate: 0.6, payoff_ratio: 1.5, risk_pct: 2, ruin_pct: 50 });
    for (const bad of [{ winRatePct: "", payoff: "1", riskPct: "1", ruinPct: "50" }, { winRatePct: "100", payoff: "1", riskPct: "1", ruinPct: "50" },
                       { winRatePct: "50", payoff: "0", riskPct: "1", ruinPct: "50" }, { winRatePct: "50", payoff: "1", riskPct: "100", ruinPct: "50" },
                       { winRatePct: "50", payoff: "x", riskPct: "1", ruinPct: "50" }, { winRatePct: "50", payoff: "1", riskPct: "1", ruinPct: "101" }]) {
      expect(ruinBody(bad)).toBeNull();
    }
  });

  it("lays the curves out with the zero line and the spot marker", () => {
    const g = chartGeometry(WI, 600, 200);
    expect(g.paths.map((p) => p.key)).toEqual(["expiry", "now", "scenario"]);
    expect(g.paths[0].d.startsWith("M0.0,")).toBe(true);
    expect(g.spotX).toBe(300);
    expect(g.zeroY).not.toBeNull();
    expect(g.yMin).toBeLessThan(-10);
    expect(g.yMax).toBeGreaterThan(10);
    const flat = chartGeometry({ ...WI, curves: { now: [0, 0, 0], scenario: [0, 0, 0] } }, 600, 200);
    expect(flat.paths.map((p) => p.key)).toEqual(["now", "scenario"]);
    expect(Number.isFinite(flat.zeroY ?? NaN)).toBe(true);
  });

  it("writes signed rupees", () => {
    expect(pnl(1250.4)).toBe("+₹1,250");
    expect(pnl(-480)).toBe("-₹480");
    expect(pnl(0.2)).toBe("₹0");
  });
});

describe("decision views (N2-3)", () => {
  it("shows the calculator's rows, marks the input and colours negative growth", () => {
    const html = renderToStaticMarkup(<RuinResultView calc={CALC} />);
    const words = text(html);
    expect(words).toContain("Expectancy per trade: 0.20 R");
    expect(words).toContain("Kelly reference: 20.0% of capital lost on a losing trade");
    expect(words).toContain("2% (your input) 1.73% 10 +0.380%");
    expect(words).toContain("0.5% < 0.01% 40 +0.098%");
    expect(words).toContain("50% 60.00% 1 -3.420%");
    expect(html).toContain("text-down");
    expect(html).not.toMatch(/<button/);
  });

  it("draws the what-if with a legend and the values at the spot", () => {
    const html = renderToStaticMarkup(<WhatIfView result={WI} />);
    const words = text(html);
    expect(html.match(/<path /g)?.length).toBe(3);
    expect(words).toContain("Now (T+0)");
    expect(words).toContain("Scenario: +3 days, IV +2.0");
    expect(words).toContain("At expiry 2026-10-20");
    expect(words).toContain("At 100: now ₹0 · scenario +₹1,250 · expiry -₹480");
    expect(words).toContain("delta 0.50");
    expect(html).toContain('role="img"');
  });
});
