import { describe, expect, it } from "vitest";
import {
  cone, contractKey, daysBetween, evaluatedById, isCurrent, niceTicks, rangePctFor, repriceModelLegs, type Evaluation, hedgeFirst, linear, linePath, money, netPremium, rewardToRisk, signAreas, snapStrike, thumbnailShape,
  yDomain, type Leg, type TemplateInfo,
} from "./model";

const leg = (over: Partial<Leg>): Leg => ({ id: "x", direction: "BUY", option_type: "CE", strike: 100, premium: 5, lots: 1, lot_size: 50, expiry: "2026-03-16", iv: 0.15, ...over });

describe("legs", () => {
  it("lists hedges first and keeps the order within each side", () => {
    const legs = [leg({ id: "s1", direction: "SELL" }), leg({ id: "b1" }), leg({ id: "s2", direction: "SELL" }), leg({ id: "b2" })];
    expect(hedgeFirst(legs).map((l) => l.id)).toEqual(["b1", "b2", "s1", "s2"]);
  });
  it("snaps a strike to the instrument's grid and never below one step", () => {
    expect(snapStrike(22137, 50)).toBe(22150);
    expect(snapStrike(22124, 50)).toBe(22100);
    expect(snapStrike(10, 50)).toBe(50);
    expect(snapStrike(22137, 0)).toBe(22137);                       // no step known: left as typed
  });
  it("adds premiums as money: credit positive, debit negative", () => {
    expect(netPremium([leg({ direction: "SELL", premium: 100 }), leg({ premium: 40 })])).toBe((100 - 40) * 50);
    expect(netPremium([leg({ premium: 10, lots: 2 })])).toBe(-1000);
    // review P1-c: a futures leg's entry price is not premium (a covered call is a credit, not a ₹1 crore debit)
    expect(netPremium([leg({ option_type: "FUT", premium: 22000 }), leg({ direction: "SELL", premium: 100 })])).toBe(100 * 50);
  });
  it("gives reward to risk only when both ends are finite and there is a risk", () => {
    expect(rewardToRisk({ max_profit: 3000, max_loss: -6000, unbounded_profit: false, unbounded_loss: false })).toBe(0.5);
    expect(rewardToRisk({ max_profit: 3000, max_loss: null, unbounded_profit: false, unbounded_loss: true })).toBeNull();
    expect(rewardToRisk({ max_profit: 3000, max_loss: 0, unbounded_profit: false, unbounded_loss: false })).toBeNull();
    expect(rewardToRisk(null)).toBeNull();
    expect(rewardToRisk({ max_profit: -500, max_loss: -6000, unbounded_profit: false, unbounded_loss: false })).toBeNull();   // no profit anywhere
  });
});

describe("the payoff canvas geometry", () => {
  const sx = linear([0, 10], [0, 100]);
  const sy = linear([-5, 5], [100, 0]);
  it("maps both ways", () => {
    expect(sx(5)).toBe(50);
    expect(sx.invert(50)).toBe(5);
    expect(sy(5)).toBe(0);
  });
  it("keeps zero in the y domain and pads it", () => {
    const [lo, hi] = yDomain([[10, 20, 30]]);
    expect(lo).toBeLessThan(0);
    expect(hi).toBeGreaterThan(30);
    expect(yDomain([[0, 0]])).toEqual([-1.16, 1.16]);
  });
  it("draws a curve point by point", () => {
    expect(linePath([0, 10], [0, 5], sx, sy)).toBe("M0.00,50.00L100.00,0.00");
  });
  it("splits profit and loss at the exact zero crossing", () => {
    const { profit, loss } = signAreas([0, 4, 10], [-4, 4, 4], sx, sy);
    expect(loss).toHaveLength(1);
    expect(profit).toHaveLength(1);
    expect(loss[0]).toContain("L20.00,50.00");                     // the crossing at x = 2 (y = 0 -> screen 50)
    expect(profit[0].startsWith("M20.00,50.00")).toBe(true);
    expect(signAreas([0, 10], [0, 0], sx, sy)).toEqual({ profit: [], loss: [] });
  });
  it("draws the cone one and two expected moves from spot", () => {
    expect(cone(100, 5)).toEqual({ inner: [95, 105], outer: [90, 110] });
  });
});

describe("template thumbnails", () => {
  const condor: TemplateInfo = {
    family: "Neutral", what: "", two_expiries: false,
    legs: [{ direction: "BUY", option_type: "CE", offset: 2, lots: 1, expiry_slot: 0 }, { direction: "BUY", option_type: "PE", offset: -2, lots: 1, expiry_slot: 0 },
      { direction: "SELL", option_type: "CE", offset: 1, lots: 1, expiry_slot: 0 }, { direction: "SELL", option_type: "PE", offset: -1, lots: 1, expiry_slot: 0 }],
  };
  it("draws the expiry shape (a condor: flat top in the middle, flat floors outside)", () => {
    const y = thumbnailShape(condor, 61) ?? [];                     // x from -3 to 3 in widths
    expect(y[30]).toBe(0);                                          // at the money: between the shorts
    expect(y[0]).toBe(-1);                                          // beyond a wing: the width lost
    expect(y[60]).toBe(-1);
  });
  it("draws nothing for a calendar (its far leg is alive at the near expiry: no expiry shape to show)", () => {
    const cal: TemplateInfo = { family: "Volatility", what: "", two_expiries: true,
      legs: [{ direction: "BUY", option_type: "CE", offset: 0, lots: 1, expiry_slot: 1 }, { direction: "SELL", option_type: "CE", offset: 0, lots: 1, expiry_slot: 0 }] };
    expect(thumbnailShape(cal)).toBeNull();
  });
});

describe("words and numbers", () => {
  it("writes money in rupees with Indian grouping and a real minus", () => {
    expect(money(123456.7)).toBe("₹1,23,457");
    expect(money(-42.5)).toBe("−₹42.50");
    expect(money(null)).toBe("–");
  });
  it("counts days between dates, never negative", () => {
    expect(daysBetween("2026-03-02", "2026-03-16")).toBe(14);
    expect(daysBetween("2026-03-16", "2026-03-02")).toBe(0);
  });
});

describe("model premiums and greeks follow their leg, by id", () => {
  const ev = (sent: Leg[], theoretical: number[]) => ({ sent, evaluation: { legs: theoretical.map((t) => ({ theoretical: t, greeks: { delta: t } })) } as unknown as Evaluation });
  it("re-prices only legs the model priced, and returns the same array when nothing moved", () => {
    const legs = [leg({ id: "m", premium: 100, premium_source: "model" }), leg({ id: "t", premium: 90, premium_source: "manual" })];
    expect(repriceModelLegs(legs, ev(legs, [120, 80])).map((l) => l.premium)).toEqual([120, 90]);
    const repriced = repriceModelLegs(legs, ev(legs, [120, 80]))[0];
    expect(repriced.priced_for).toBe(contractKey(repriced));
    const same = [{ ...repriced }];
    expect(repriceModelLegs(same, ev(same, [119.4]))).toBe(same);          // the clock moved, not the contract: no loop
    expect(repriceModelLegs(legs, { sent: legs, evaluation: { legs: [] } as unknown as Evaluation })).toBe(legs);   // another shape: untouched
  });
  it("matches by id, not position, and never writes an old strike's price after a further move", () => {
    const a = leg({ id: "a", strike: 100, premium: 5, premium_source: "model", priced_for: "stale" });
    const b = leg({ id: "b", strike: 110, premium: 3, premium_source: "model", priced_for: "stale" });
    const reply = ev([a, b], [7, 4]);
    expect(repriceModelLegs([b, a], reply).map((l) => [l.id, l.premium])).toEqual([["b", 4], ["a", 7]]);   // reordered
    const moved = { ...a, strike: 105 };                                                                     // dragged on since
    expect(repriceModelLegs([moved, b], reply)[0]).toBe(moved);
  });
  it("shows a leg's greeks only while it is the contract that was evaluated", () => {
    const a = leg({ id: "a" });
    const b = leg({ id: "b", strike: 110 });
    const reply = ev([a, b], [1, 2]);
    expect(Object.keys(evaluatedById([a, b], reply))).toEqual(["a", "b"]);
    expect(evaluatedById([b], reply).b.greeks.delta).toBe(2);                       // a removed: b keeps its own row
    expect(evaluatedById([{ ...a, direction: "SELL" }, b], reply).a).toBeUndefined(); // side switched: not a's old numbers
    expect(isCurrent([a, b], reply)).toBe(true);
    expect(isCurrent([b], reply)).toBe(false);
    expect(isCurrent([a, { ...b, lots: 2 }], reply)).toBe(false);
    expect(isCurrent([a, b], null)).toBe(false);
    const params = { spot: 100, daysForward: 0, ivShift: 0 };
    const withParams = { ...reply, params };
    expect(isCurrent([a, b], withParams, params)).toBe(true);
    expect(isCurrent([a, b], withParams, { ...params, daysForward: 3 })).toBe(false);   // a slider moved since
    expect(isCurrent([a, b], withParams, { ...params, spot: 101 })).toBe(false);
  });
});

describe("chart range", () => {
  it("covers every strike with a margin, within 8 % and 60 %", () => {
    expect(rangePctFor(22000, [leg({ strike: 22000 })])).toBe(8);
    const wide = rangePctFor(1000, [leg({ strike: 800 }), leg({ strike: 1200 })]);
    expect(wide).toBeGreaterThan(20);
    expect(1000 * (1 - wide / 100)).toBeLessThan(800);
    expect(rangePctFor(100, [leg({ strike: 1000 })])).toBe(60);
    expect(rangePctFor(1000, [leg({ option_type: "FUT", strike: 5000 })])).toBe(8);   // a future's "strike" is not drawn
  });
});

describe("axis ticks", () => {
  it("are round numbers inside the domain and include zero when it is inside", () => {
    expect(niceTicks(-2398, 6302)).toEqual([-2000, 0, 2000, 4000, 6000]);
    expect(niceTicks(0, 1)).toEqual([0, 0.2, 0.4, 0.6, 0.8, 1]);
    expect(niceTicks(5, 5)).toEqual([5]);
  });
});
