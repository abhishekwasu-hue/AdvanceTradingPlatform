import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ExpectedMove, ExposureView } from "../types";
import { ExpectedMoveControls } from "./ExpectedMoveLayer";
import { ExposureViewPanel } from "./ExposureSection";
import { CONE_LAYER_ID, coneLayer, rupees0, strongestPairs } from "./exposure";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&lt;/g, "<").replace(/&amp;/g, "&").replace(/\s+/g, " ");

// Shaped as tests/test_n2_exposure_cone.py's assembled view.
const VIEW: ExposureView = {
  underlyings: [
    { underlying: "AAA", legs: 2, modes: ["PAPER"], notional: 1440, net_quantity: 6, option_premium: 0, risk_at_stop: 70, legs_without_stop: [],
      sector: "Information Technology", beta: 1.0, return_days: 70 },
    { underlying: "NIFTY 50", legs: 2, modes: ["PAPER"], notional: 0, net_quantity: 0, option_premium: 2250, risk_at_stop: 6750,
      legs_without_stop: ["NIFTY24OCT25000CE"], sector: "Index", beta: null, return_days: 70 },
  ],
  sectors: [{ sector: "Index", underlyings: 1, notional: 0, option_premium: 2250, risk_at_stop: 6750 },
            { sector: "Information Technology", underlyings: 1, notional: 1440, option_premium: 0, risk_at_stop: 70 }],
  pairs: [{ a: "AAA", b: "CCC", r: 0.75, days: 70 }, { a: "AAA", b: "NIFTY 50", r: -0.92, days: 70 }],
  move_together: [{ a: "AAA", b: "CCC", r: 0.75, days: 70 }, { a: "AAA", b: "NIFTY 50", r: -0.92, days: 70 }],
  move_together_threshold: 0.7, min_points: 20, lookback_days: 90,
  totals: { underlyings: 2, legs: 4, risk_at_stop: 6820, notional: 1440 }, market: "NIFTY 50", market_days: 70,
  note: "From your own open positions at their entry prices and the exchange's daily closes.",
};

const MOVE: ExpectedMove = {
  instrument: "RELIANCE", close: 2500, close_date: "2026-10-09", sigma: 0.252, method: "hv20", window: 20, horizon: 5,
  cone: [], selected: { sessions: 5, move_1sd: 79.37, low_1sd: 2422, high_1sd: 2580.5, low_2sd: 2346.7, high_2sd: 2663.4 },
  note: "A lognormal model of past daily moves. Not a forecast.",
};

describe("exposure helpers (N2-4)", () => {
  it("formats rupees and orders the pairs by strength", () => {
    expect(rupees0(1440.4)).toBe("₹1,440");
    expect(rupees0(-6750)).toBe("-₹6,750");
    expect(strongestPairs(VIEW).map((p) => p.b)).toEqual(["NIFTY 50", "CCC"]);
  });

  it("draws the cone as two bands with the close line", () => {
    const layer = coneLayer(MOVE, "±1 sd (5 sessions)", "±2 sd (5 sessions)");
    expect(CONE_LAYER_ID).toBe("expected_move");
    expect(layer.bands?.map((b) => [b.low, b.high, b.label])).toEqual([[2346.7, 2663.4, "±2 sd (5 sessions)"], [2422, 2580.5, "±1 sd (5 sessions)"]]);
    expect(layer.lines?.[0].price).toBe(2500);
  });
});

describe("exposure views (N2-4)", () => {
  it("shows the table, sectors, pairs and no control beyond reading", () => {
    const html = renderToStaticMarkup(<ExposureViewPanel view={VIEW} />);
    const words = text(html);
    expect(words).toContain("2 underlyings · 4 legs · risk at stops ₹6,820");
    expect(words).toContain("AAA Information Technology 2 ₹1,440 ₹0 ₹70 1.00");
    expect(words).toContain("NIFTY 50 Index 2 ₹0 ₹2,250 ₹6,750 (1 leg without a stop) -");
    expect(words).toContain("Index: 1 underlyings · risk at stops ₹6,750");
    expect(words).toContain("Moving together (|r| ≥ 0.7 over 90 days of closes):");
    expect(words.indexOf("AAA and NIFTY 50: r = -0.92")).toBeLessThan(words.indexOf("AAA and CCC: r = 0.75"));
    expect(html).not.toMatch(/<button|<input/);
    expect(text(renderToStaticMarkup(<ExposureViewPanel view={{ ...VIEW, underlyings: [] }} />))).toContain("No open position of yours.");
    expect(text(renderToStaticMarkup(<ExposureViewPanel view={{ ...VIEW, move_together: [] }} />))).toContain("No pair of your underlyings moved together at |r| ≥ 0.7.");
  });

  it("shows the cone toggle, and the range once on", () => {
    const off = renderToStaticMarkup(<ExpectedMoveControls on={false} horizon={5} move={null} failed={false} onToggle={() => undefined} onHorizon={() => undefined} />);
    expect(off).toContain('aria-pressed="false"');
    expect(off).not.toContain("<select");
    const words = text(renderToStaticMarkup(<ExpectedMoveControls on horizon={5} move={MOVE} failed={false} onToggle={() => undefined} onHorizon={() => undefined} />));
    expect(words).toContain("RELIANCE · close 2,500 on 2026-10-09 · σ 25.2% (hv20) · 5 sessions: ±1 sd 2,422-2,580.5, ±2 sd 2,346.7-2,663.4");
    expect(text(renderToStaticMarkup(<ExpectedMoveControls on horizon={5} move={null} failed onToggle={() => undefined} onHorizon={() => undefined} />)))
      .toContain("could not be computed");
  });
});
