import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Evaluation, Leg } from "../optionsBuilder/model";
import { CompareView } from "./CompareSection";
import { MAX_SLOTS, addSlot, compareTable, overlay, signature, type CompareSlot } from "./compare";
import { tProgression as t } from "./i18n";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&lt;/g, "<").replace(/&amp;/g, "&").replace(/\s+/g, " ");

const leg = (strike: number, direction: "BUY" | "SELL" = "SELL", id = `l${strike}`): Leg =>
  ({ id, direction, option_type: "CE", strike, premium: 50, lots: 1, lot_size: 50, expiry: "2026-03-16", iv: 0.15 });
const slot = (key: string, legs: Leg[]): CompareSlot => ({ key, name: key, legs });

const ev = (over: Partial<Evaluation>): Evaluation => ({
  prices: [21000, 22000, 23000], today: [0, 0, 0], on_date: [0, 0, 0], at_expiry: [-100, 200, -100], days_forward: 0, iv_shift: 0, single_expiry: true,
  extremes: { max_profit: 5750, max_loss: -14250, unbounded_profit: false, unbounded_loss: false }, profitable: null, breakevens: [21815.4, 22184.6],
  summary: { expiry: "2026-03-16", t_years: 0.04, sigma: 0.15, pop: 0.62, expected_move: 655.2, expected_pnl: 10,
             greeks: { delta: 0, gamma: 0, theta: 312.4, vega: -900, rho: 0 }, method: "lognormal", model: "bs" },
  legs: [], disclaimer: "Model estimates.", net_premium: 5750, reward_to_risk: 0.4,
  margin: { value: 14250, estimated: true, basis: "max loss", note: "" },
  costs: { to_open: { stt: 0, exch: 0, sebi: 0, stamp: 0, brokerage: 0, tax: 0, total: 0, other: 0 }, round_trip: 0, brokerage_per_order: 20, orders: 4, as_of: "", note: "" },
  ...over,
});

describe("compare helpers (N2-8)", () => {
  it("adds at most three different strategies", () => {
    expect(signature([leg(22000, "SELL", "a")])).toBe(signature([leg(22000, "SELL", "b")]));          // ids do not matter
    let s: CompareSlot[] = [];
    for (const k of [22000, 22100, 22200]) s = addSlot(s, slot(`s${k}`, [leg(k)])).slots;
    expect(s.length).toBe(MAX_SLOTS);
    expect(addSlot(s, slot("x", [leg(22300)])).refused).toBe("full");
    expect(addSlot(s.slice(0, 1), slot("y", [leg(22000, "SELL", "z")])).refused).toBe("same");
  });

  it("lays the engine's numbers side by side", () => {
    const strangle = ev({ extremes: { max_profit: 6000, max_loss: null, unbounded_profit: false, unbounded_loss: true }, margin: { value: null, estimated: true, basis: "", note: "" },
                          reward_to_risk: null });
    const calendar = ev({ extremes: null, at_expiry: null, on_date: [-50, 80, -50], breakevens: null });
    const rows = compareTable([ev({}), strangle, calendar], t);
    expect(rows[0]).toEqual(["Net premium", "+₹5,750", "+₹5,750", "+₹5,750"]);
    expect(rows[2]).toEqual(["Max loss", "-₹14,250", "unbounded", "two expiries: no single expiry payoff"]);
    expect(rows[3]).toEqual(["Reward : risk", "0.40", "-", "0.40"]);
    expect(rows[5]).toEqual(["Margin (estimate)", "₹14,250", "broker only", "₹14,250"]);
    expect(rows[6]).toEqual(["Theta / day", "+₹312", "+₹312", "+₹312"]);
    expect(rows[7][1]).toBe("21,815, 22,185");
    expect(rows[8][1]).toBe("±655");
    const o = overlay([ev({}), calendar], 600, 200);
    expect(o.paths.length).toBe(2);
    expect(o.paths[1].startsWith("M0.0,")).toBe(true);
    expect(overlay([], 600, 200)).toEqual({ paths: [], zeroY: null });
  });
});

describe("CompareView (N2-8)", () => {
  it("draws one curve per strategy and the table, with no control", () => {
    const html = renderToStaticMarkup(<CompareView names={["Condor", "Strangle"]} evals={[ev({}), ev({ net_premium: 6000 })]} />);
    expect(html.match(/<path /g)?.length).toBe(2);
    const words = text(html);
    expect(words).toContain("Condor Strangle");
    expect(words).toContain("Net premium +₹5,750 +₹6,000");
    expect(html).not.toMatch(/<button/);
  });
});
