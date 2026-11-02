import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { wireLegs } from "../../optionsBuilder/api";
import type { Evaluation, Leg } from "../../optionsBuilder/model";
import panelSource from "./PlanPanelV2.tsx?raw";
import source from "./RiskPreviewV2.tsx?raw";
import { greeksPath, ivRequest, RiskPreviewV2, type RiskPreviewApi } from "./RiskPreviewV2";
import { ivPointsOf, riskRows, uniformUnits } from "./riskPreview";

/** M76 MP-4: the risk preview's figures are the builder's / the Greeks route's, only added up here. */
const LEG = (over: Partial<Leg> = {}): Leg => ({ id: "a", direction: "SELL", option_type: "PE", strike: 24500, premium: 120, lots: 2, lot_size: 75, expiry: "2026-11-25", iv: null, ...over } as Leg);
const LEGS = [LEG({ id: "h", direction: "BUY", strike: 24400, premium: 80 }), LEG()];

function evaluation(over: Partial<Evaluation> = {}, today = [-6000, 0, 3000, 6000]): Evaluation {
  return {
    prices: [24300, 24400, 24500, 24600], today, on_date: today, at_expiry: today, days_forward: 0, iv_shift: 0, single_expiry: true,
    extremes: { max_profit: 6000, max_loss: -9000, unbounded_profit: false, unbounded_loss: false }, profitable: null, breakevens: [24460],
    summary: { expiry: "2026-11-25", t_years: 0.06, sigma: 0.13, pop: 0.6, expected_move: 400, expected_pnl: 100,
      greeks: { delta: 12.5, gamma: 0, theta: 210, vega: -300, rho: 0 }, method: "", model: "bs" },
    legs: [], disclaimer: "", net_premium: 6000, reward_to_risk: null,
    margin: { value: 45000, estimated: true, basis: "", note: "" },
    costs: { to_open: { stt: 0, exch: 0, sebi: 0, stamp: 0, brokerage: 0, tax: 0, total: 0, other: 0 }, round_trip: 0, brokerage_per_order: 0, orders: 2, as_of: "", note: "" },
    ...over,
  } as Evaluation;
}
const BOOK = { net: { net_delta: -40, net_theta: 100, net_vega: 50 }, underlying: { net_delta: -30, net_theta: 80, net_vega: 20, legs: 3 }, problem: null };
const row = (rows: ReturnType<typeof riskRows>, key: string) => rows.find((r) => r.key === key);

describe("risk preview rows", () => {
  it("max loss / profit in rupees and points; break-even with its distance from spot", () => {
    const rows = riskRows({ evaluation: evaluation(), legs: LEGS, spot: 24500, balance: 200000, book: BOOK, ivPoints: null, ivEvaluation: null });
    expect(uniformUnits(LEGS)).toBe(150);
    expect(row(rows, "max_loss")).toMatchObject({ value: "−₹9,000", sub: "60.00 points", tone: "down" });
    expect(row(rows, "max_profit")).toMatchObject({ value: "₹6,000", sub: "40.00 points", tone: "up" });
    expect(row(rows, "breakevens")).toMatchObject({ value: "24,460.00", sub: "−40.00 points from spot" });
  });

  it("no points figure when the legs differ in size", () => {
    const legs = [LEGS[0], LEG({ lots: 3 })];
    expect(uniformUnits(legs)).toBeNull();
    expect(row(riskRows({ evaluation: evaluation(), legs, spot: 24500, balance: null, book: BOOK, ivPoints: null, ivEvaluation: null }), "max_loss")?.sub).toBeUndefined();
  });

  it("balance after the margin = his PAPER balance minus the builder's estimate; Not known otherwise", () => {
    const r = riskRows({ evaluation: evaluation(), legs: LEGS, spot: 24500, balance: 200000, book: BOOK, ivPoints: null, ivEvaluation: null });
    expect(row(r, "margin_after")).toMatchObject({ value: "₹1,55,000", sub: "PAPER balance ₹2,00,000 · margin estimate ₹45,000" });
    const none = riskRows({ evaluation: evaluation({ margin: { value: null, estimated: true, basis: "", note: "" } }), legs: LEGS, spot: 24500, balance: 200000, book: BOOK, ivPoints: null, ivEvaluation: null });
    expect(row(none, "margin_after")?.value).toBe("Not known");
    const noBalance = riskRows({ evaluation: evaluation(), legs: LEGS, spot: 24500, balance: null, book: BOOK, ivPoints: null, ivEvaluation: null });
    expect(row(noBalance, "margin_after")?.sub).toBe("Your PAPER balance is not known.");
  });

  it("book and same-underlying Greeks after = the route's sums + the builder's net Greeks", () => {
    const r = riskRows({ evaluation: evaluation(), legs: LEGS, spot: 24500, balance: null, book: BOOK, ivPoints: null, ivEvaluation: null });
    expect(row(r, "book")).toMatchObject({ value: "Δ −27.50 · Θ +310.00 · V −250.00", sub: "Now Δ −40.00 · Θ +100.00 · V +50.00" });
    expect(row(r, "same_underlying")).toMatchObject({ value: "Δ −17.50 · Θ +290.00 · V −280.00", sub: "3 open option legs now · Δ −30.00" });
    const failed = riskRows({ evaluation: evaluation(), legs: LEGS, spot: 24500, balance: null, book: { net: null, underlying: null, problem: "No broker session" }, ivPoints: null, ivEvaluation: null });
    expect(row(failed, "book")).toMatchObject({ value: "Not known", sub: "No broker session" });
    expect(row(failed, "same_underlying")).toBeUndefined();
  });

  it("IV row only with his saved points: the builder's today value at spot, shifted minus base; margin not known", () => {
    const base = { evaluation: evaluation(), legs: LEGS, spot: 24500, balance: null, book: BOOK };
    expect(row(riskRows({ ...base, ivPoints: null, ivEvaluation: null }), "iv")).toBeUndefined();
    const shifted = evaluation({}, [-7000, -1000, 1800, 5000]);
    expect(row(riskRows({ ...base, ivPoints: 5, ivEvaluation: shifted }), "iv")).toMatchObject({
      label: "IV +5.00 points (model)", value: "−₹1,200 today", tone: "down", sub: "Margin under this IV: not known." });
    expect(ivPointsOf([{ key: "warnings.iv_rise_pct", value: 5, saved: false }])).toBeNull();     // Not set until he saves it
    expect(ivPointsOf([{ key: "warnings.iv_rise_pct", value: 5, saved: true }])).toBe(5);
  });
});

describe("RiskPreviewV2", () => {
  it("reads his own book for this underlying; the IV request is the builder's with his points; no order route", () => {
    expect(greeksPath("NIFTY 50")).toBe("/positions/greeks?mine=true&underlying=NIFTY+50");
    expect(ivRequest(LEGS, 24500, "2026-11-02", 5)).toMatchObject({ iv_shift: 0.05, spot: 24500, days_forward: 0, as_of: "2026-11-02", points: 241 });
    expect(ivRequest(LEGS, 24500, "2026-11-02", 5).legs).toEqual(wireLegs(LEGS));
    expect(source).not.toMatch(/\/paper\/fno\/order|\/orders?\b|placeOrder|method: "POST"/);
    expect(panelSource).toMatch(/<RiskPreviewV2 /);
  });

  it("first paint: the builder's figures at once, the book row says it is being read", () => {
    const api: RiskPreviewApi = { greeks: async () => { throw new Error("x"); }, balance: async () => null, evaluate: async () => evaluation() };
    const html = renderToStaticMarkup(<RiskPreviewV2 underlying="NIFTY 50" evaluation={evaluation()} legs={LEGS} spot={24500} asOf="2026-11-02" ivPoints={null} api={api} />);
    expect(html).toContain("−₹9,000");
    expect(html).toContain("Reading…");
    expect(html).not.toContain("data-risk-row=\"iv\"");
  });
});
