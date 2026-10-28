import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi, type Mock } from "vitest";
import { ApiError } from "../api/errors";
import { rows as chainRows } from "../optionChain/chain.fixture";
import type { Evaluation } from "../optionsBuilder/model";
import { kiteFnoBasketLink, KITE_BASKET_URL } from "../proposals/brokerLinks";
import type { BasketNumbers, BasketPreview, BasketResult } from "./api";
import { BasketStore, MAX_LEGS, type BasketLeg, type NewLeg } from "./basket";
import {
  aboutMoney, basketAsk, basketProblems, builderHandoff, contractLabel, legsOf, payoffSentence, readyStrategy, remainingLegs, resultHeadline,
  saveBaskets, savedOf, stopFor, strikeStep, type StrategyInputs,
} from "./basketModel";
import { BasketBody, ConfirmView, ResultView, type BodyHandlers } from "./BasketPanelV2";
import panelSource from "./BasketPanelV2.tsx?raw";
import tradingSource from "./ChainTradingV2.tsx?raw";

/**
 * FNO-2 (owner order sections 5, 8b-c and 9): the basket panel - editing legs, the ready strategies from a fixture chain
 * with the user's own settings, the payoff in plain words, the confirm step (total margin and max loss before placing),
 * the per-leg result of a partial fill, saved baskets, the broker-app basket and the Options Builder hand-over. No DOM:
 * the panel's views hold no state, so they are rendered directly and their handlers called.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const ADVICE = /\b(should|recommend\w*|advice|advise|best|target\w*|guarantee\w*|sure[- ]?shot)\b/i;
const EXPAND = new Set(["BasketBody", "LegRow", "Figure", "ConfirmView", "ResultView"]);
function elements(node: ReactNode, out: ReactElement[] = []): ReactElement[] {
  if (Array.isArray(node)) { node.forEach((n) => elements(n, out)); return out; }
  if (!isValidElement(node)) return out;
  const type = node.type as unknown;
  if (typeof type === "function" && EXPAND.has((type as { name: string }).name)) return elements((type as (p: unknown) => ReactNode)(node.props), out);
  out.push(node);
  elements((node.props as { children?: ReactNode }).children, out);
  return out;
}
type Props = Record<string, unknown> & { onClick?: () => void; onBlur?: (e: unknown) => void; disabled?: boolean };
const all = (els: ReactElement[], attr: string) => els.filter((e) => (e.props as Record<string, unknown>)[attr] !== undefined).map((e) => e.props as Props);
const by = (els: ReactElement[], attr: string, value: unknown) => {
  const el = els.find((e) => (e.props as Record<string, unknown>)[attr] === value);
  expect(el, `${attr}=${String(value)}`).toBeTruthy();
  return el!.props as Props;
};

const EXPIRY = "2026-11-03";
const leg = (over: Partial<BasketLeg> = {}): BasketLeg => ({
  id: "l1", underlying: "NIFTY", expiry: EXPIRY, strike: 24500, right: "PE", side: "SELL", lots: 1, price: 200, label: null, stopLoss: 300, iv: 13, ...over,
});

function handlers(): BodyHandlers & Record<keyof BodyHandlers, Mock> {
  const names: (keyof BodyHandlers)[] = ["onLeg", "onRemove", "onReady", "onAround", "onProduct", "onStrike", "onReview", "onFlip", "onClear",
    "onTool", "onSaveName", "onSave", "onLoad", "onDelete", "onBuilder", "onBrokerApp"];
  return Object.fromEntries(names.map((n) => [n, vi.fn()])) as unknown as BodyHandlers & Record<keyof BodyHandlers, Mock>;
}

/** A bull put spread's expiry curve (sell 24500 PE at 200, buy 24300 PE at 100, lot 75) on a grid, as the engine draws it. */
function bullPut(): Evaluation {
  const prices = Array.from({ length: 241 }, (_, i) => 23500 + i * 8.33333);
  const pnl = (s: number) => ((200 - Math.max(24500 - s, 0)) - (100 - Math.max(24300 - s, 0))) * 75;
  return {
    prices, at_expiry: prices.map(pnl), today: prices.map(pnl), on_date: prices.map(pnl), days_forward: 0, iv_shift: 0, single_expiry: true,
    extremes: { max_profit: 7500, max_loss: -7500, unbounded_profit: false, unbounded_loss: false }, profitable: [[24400, null]], breakevens: [24400],
    summary: { expiry: EXPIRY, t_years: 0.02, sigma: 0.13, pop: 0.62, expected_move: 400, expected_pnl: 100,
      greeks: { delta: 12.3, gamma: -0.004, theta: 410, vega: -800, rho: 1 }, method: "", model: "bs" },
    legs: [], disclaimer: "Model estimates.", net_premium: 7500, reward_to_risk: 1,
    margin: { value: 7500, estimated: true, basis: "Defined risk: at least the max loss.", note: "" },
    costs: { to_open: { stt: 1, exch: 1, sebi: 0, stamp: 0, brokerage: 40, tax: 7, total: 49, other: 8 }, round_trip: 98, brokerage_per_order: 20, orders: 2, as_of: "2026-10-27", note: "" },
  };
}

function numbers(): BasketNumbers {
  return {
    ...bullPut(), ok: true, errors: [], lot_sizes: [75, 75], labels: [], order: [1, 0],
    margin: { ...bullPut().margin, hedge: {
      with_hedge: { value: 7500, estimated: true, basis: "Defined risk: at least the max loss." },
      without_hedge: { value: null, estimated: true, basis: "Short options not covered by long ones: needs the broker's margin." },
      benefit: null, hedged: true, source: "estimate", note: "ATP's estimate, not your broker's number.",
    } },
  };
}

function body(legs: BasketLeg[], over: Partial<Parameters<typeof BasketBody>[0]> = {}) {
  const on = handlers();
  const node = (
    <BasketBody legs={legs} numbers={null} problems={[]} settings={null} rows={chainRows()} pick={24500} atm={24500} spot={24480} product="MIS"
      note={null} tool={null} saved={null} saveName="" idPrefix="b" on={on} {...over} />
  );
  return { on, els: elements(node), html: renderToStaticMarkup(node) };
}

describe("FNO-2 basket: the legs", () => {
  it("each leg shows its side, the contract as traders write it, lots, price and a sold leg's stop - all editable", () => {
    const legs = [leg({ id: "a", side: "BUY", strike: 24300, price: 100, stopLoss: null }), leg({ id: "b" })];
    const { on, els, html } = body(legs);
    const words = text(html);
    expect(words).toContain("NIFTY 03 NOV 24300 PE");
    expect(words).toContain("NIFTY 03 NOV 24500 PE");
    expect(all(els, "data-leg-stop")).toHaveLength(1);                         // only the sold leg asks for a stop
    all(els, "data-leg-side")[0].onClick!();
    all(els, "data-leg-more")[1].onClick!();
    all(els, "data-leg-remove")[0].onClick!();
    all(els, "data-leg-price")[1].onBlur!({ target: { value: "212.5" } });
    all(els, "data-leg-stop")[0].onBlur!({ target: { value: "" } });
    expect(on.onLeg.mock.calls).toEqual([["a", { side: "SELL" }], ["b", { lots: 2 }], ["b", { price: 212.5 }], ["b", { stopLoss: null }]]);
    expect(on.onRemove).toHaveBeenCalledWith("a");
    expect(all(els, "data-leg-less")[0].disabled).toBe(true);                   // never below one lot
    by(els, "data-basket", "execute").onClick!();
    expect(on.onReview).toHaveBeenCalled();
  });

  it("the store edits, replaces, merges the same contract and keeps at most the builder's leg limit", () => {
    const store = new BasketStore([]);
    store.add({ ...leg(), side: "BUY", stopLoss: null });
    store.add({ ...leg(), side: "BUY", stopLoss: null, lots: 2 });
    expect(store.snapshot().map((l) => l.lots)).toEqual([3]);
    const id = store.snapshot()[0].id;
    store.update(id, { strike: 24600, label: null });
    expect(store.snapshot()[0].strike).toBe(24600);
    store.replace(Array.from({ length: MAX_LEGS + 3 }, (_, i) => ({ ...leg(), strike: 20000 + i })));
    expect(store.snapshot()).toHaveLength(MAX_LEGS);
    expect(new Set(store.snapshot().map((l) => l.id)).size).toBe(MAX_LEGS);
    store.clear();
    expect(store.snapshot()).toEqual([]);
  });

  it("the request: IV as a fraction, a stop only on sold legs, no mode; missing stops are named before the confirm step", () => {
    const ask = basketAsk([leg({ side: "BUY", stopLoss: 90 }), leg()], 24480, "NRML");
    expect(ask).toEqual({ spot: 24480, product: "NRML", legs: [
      { underlying: "NIFTY", expiry: EXPIRY, strike: 24500, right: "PE", side: "BUY", lots: 1, price: 200, stop_loss: null, iv: 0.13 },
      { underlying: "NIFTY", expiry: EXPIRY, strike: 24500, right: "PE", side: "SELL", lots: 1, price: 200, stop_loss: 300, iv: 0.13 }] });
    expect(JSON.stringify(ask)).not.toMatch(/mode|LIVE/);
    expect(basketProblems([leg({ stopLoss: null })])).toEqual([]);          // DEF-0 D7: a stop is his choice
    expect(basketProblems([leg(), leg({ underlying: "BANKNIFTY" })])[0]).toContain("one underlying");
    expect(basketProblems([])).toEqual(["Add a leg first."]);
    expect(contractLabel("NIFTY", "2026-10-28", 22800, "CE")).toBe("NIFTY 28 OCT 22800 CE");
  });
});

describe("FNO-2 basket: ready strategies from the chain on screen", () => {
  const inputs = (over: Partial<StrategyInputs> = {}): StrategyInputs => ({
    underlying: "NIFTY", expiry: EXPIRY, rows: chainRows(), around: 24500, settings: { width_steps: 2, lots: 1, stop_pct: 50 }, ...over,
  });
  const shape = (legs: NewLeg[]) => legs.map((l) => [l.side, l.strike, l.right, l.price, l.stopLoss ?? null, l.lots]);

  it("bull put and bear call spreads: the strike sold, the wing the user's width away, hedge first, stop from the user's setting", () => {
    expect(strikeStep(chainRows())).toBe(100);
    expect(shape(readyStrategy("Bull put spread", inputs()).legs)).toEqual([["BUY", 24300, "PE", 100, null, 1], ["SELL", 24500, "PE", 200, 300, 1]]);
    expect(shape(readyStrategy("Bear call spread", inputs({ settings: { width_steps: 3, lots: 2, stop_pct: 40 } })).legs))
      .toEqual([["BUY", 24800, "CE", 100, null, 2], ["SELL", 24500, "CE", 250, 350, 2]]);
    expect(readyStrategy("Bull put spread", inputs()).legs.every((l) => l.iv !== null && l.label === null)).toBe(true);
  });

  it("iron condor, straddle and strangle around the picked strike; a missing strike is said plainly", () => {
    const condor = readyStrategy("Iron condor", inputs({ settings: { width_steps: 1, lots: 1, stop_pct: 50 } }));
    expect(shape(condor.legs).map((l) => l.slice(0, 3))).toEqual([["BUY", 24300, "PE"], ["BUY", 24700, "CE"], ["SELL", 24400, "PE"], ["SELL", 24600, "CE"]]);
    const wide = readyStrategy("Iron condor", inputs());
    expect(wide.legs).toEqual([]);
    expect(wide.problem).toBe("The chain has no 24,100 strike for the 2026-11-03 expiry. Pick another strike or a smaller width.");
    expect(shape(readyStrategy("Straddle", inputs({ around: 24400 })).legs)).toEqual([["BUY", 24400, "CE", 300, null, 1], ["BUY", 24400, "PE", 150, null, 1]]);
    expect(shape(readyStrategy("Strangle", inputs({ settings: { width_steps: 1, lots: 1, stop_pct: 50 } })).legs).map((l) => l.slice(0, 3)))
      .toEqual([["BUY", 24600, "CE"], ["BUY", 24400, "PE"]]);
  });

  it("a calendar sells this expiry and buys the next one; without the next chain it says so", () => {
    expect(readyStrategy("Calendar", inputs()).problem).toContain("next expiry");
    const cal = readyStrategy("Calendar", inputs({ next: { expiry: "2026-11-10", rows: chainRows() } }));
    expect(cal.legs.map((l) => [l.side, l.expiry, l.strike, l.right])).toEqual([["BUY", "2026-11-10", 24500, "CE"], ["SELL", EXPIRY, 24500, "CE"]]);
  });

  it("the stop of a sold leg is on the tick and above the price", () => {
    expect(stopFor(200, 50)).toBe(300);
    expect(stopFor(101.35, 50)).toBe(152.05);
    expect(stopFor(null, 50)).toBeNull();
  });

  it("the panel names where the settings came from, or that a neutral start is used", () => {
    const own = body([], { settings: { source: "mine on NIFTY", width_steps: 4, lots: 2, stop_pct: 60, note: "From your settings (mine on NIFTY): wings 4 strikes apart." } });
    expect(text(own.html)).toContain("From your settings (mine on NIFTY)");
    by(own.els, "data-strategy", "Iron condor").onClick!();
    expect(own.on.onReady).toHaveBeenCalledWith("Iron condor");
    expect(text(body([]).html)).toContain("edit any leg");
  });
});

describe("FNO-2 basket: the numbers and the payoff in plain words", () => {
  it("one sentence for the outcomes at expiry (8b-c)", () => {
    expect(payoffSentence(bullPut(), "NIFTY")).toBe("If NIFTY closes below 24,400 at expiry you lose up to about ₹7,500; above 24,400 you keep up to about ₹7,500.");
    // A flat region reads "about"; a bought option "make"s; an open-ended loss says it grows.
    const prices = Array.from({ length: 101 }, (_, i) => 24000 + i * 10);
    const longCall = { prices, at_expiry: prices.map((s) => (Math.max(s - 24500, 0) - 100) * 75), breakevens: [24600], net_premium: -7500,
      extremes: { max_profit: null, max_loss: -7500, unbounded_profit: true, unbounded_loss: false } };
    expect(payoffSentence(longCall, "NIFTY")).toBe("If NIFTY closes below 24,600 at expiry you lose up to about ₹7,500; above 24,600 you make about ₹30,000 or more, more the higher it closes.");
    const nakedCall = { ...longCall, at_expiry: longCall.at_expiry.map((y) => -y), net_premium: 7500,
      extremes: { max_profit: 7500, max_loss: null, unbounded_profit: false, unbounded_loss: true } };
    expect(payoffSentence(nakedCall, "NIFTY")).toContain("above 24,600 you lose about ₹30,000 or more, more the higher it closes");
    expect(payoffSentence({ ...bullPut(), at_expiry: null, extremes: null }, "NIFTY")).toContain("expire on different dates");
    expect(aboutMoney(4183)).toBe("₹4,200");
    expect(aboutMoney(-187)).toBe("₹190");
    expect(ADVICE.test(payoffSentence(bullPut(), "NIFTY"))).toBe(false);
  });

  it("the panel shows the payoff, max profit / loss, breakevens, PoP, net premium, Greeks and the margin WITH the hedge", () => {
    const { html, els, on } = body([leg({ id: "a", side: "BUY", strike: 24300, price: 100, stopLoss: null }), leg({ id: "b" })], { numbers: numbers() });
    const words = text(html);
    for (const part of ["Net premium received ₹7,500.00", "Max profit ₹7,500.00", "Max loss −₹7,500.00", "Breakevens 24,400", "Probability of profit 62.0%",
      "Margin (estimate) ₹7,500.00", "Defined risk: at least the max loss. With the hedge legs.", "Without the hedge legs From your broker",
      "needs the broker's margin", "ATP's estimate, not your broker's number.", "In plain words",
      "If NIFTY closes below 24,400 at expiry", "Delta 12.3", "Theta 410"]) {
      expect(words).toContain(part);
    }
    expect(html).toContain("aria-label=\"Payoff: at expiry, today");                // the builder's payoff canvas (expiry + T+0)
    by(els, "data-tool", "builder").onClick!();
    by(els, "data-tool", "broker-app").onClick!();
    expect(on.onBuilder).toHaveBeenCalled();
    expect(on.onBrokerApp).toHaveBeenCalled();
    expect(ADVICE.test(words.replace(/Ready strategies/g, ""))).toBe(false);
  });
});

describe("FNO-2 basket: the confirm step and the result", () => {
  const previewLeg = (seq: number, legIdx: number, side: "BUY" | "SELL", over: Record<string, unknown> = {}) => ({
    seq, leg: legIdx, role: side === "BUY" ? "hedge" : "short", mode: "PAPER", practice: "", contract: null, label: `NIFTY 03 NOV ${side === "BUY" ? 24300 : 24500} PE`,
    side, lots: 1, lot_size: 75, units: 75, order_type: "MARKET", product: "MIS", price: 100, price_source: "live", fill_price: side === "BUY" ? 100 : 200,
    fill_note: null, stop_loss: side === "SELL" ? 300 : null, margin: null, max_loss: null, charges: null, risk: {}, errors: [], refusals: [], warnings: [],
    can_place: true, confirm_label: null, as_of: "", ...over,
  });
  const preview = (over: Partial<BasketPreview> = {}): BasketPreview => ({
    mode: "PAPER", practice: "Practice mode (PAPER)", product: "MIS", as_of: "", refusals: [], totals_errors: [],
    legs: [previewLeg(1, 1, "BUY"), previewLeg(2, 0, "SELL")] as BasketPreview["legs"],
    totals: { margin: numbers().margin, extremes: numbers().extremes, net_premium: 7500, costs: 49, breakevens: [24400] },
    can_execute: true, confirm_label: "Place 2 PAPER orders, hedge legs first", ...over,
  });

  it("shows the total margin and the max loss before placing, hedge legs first; nothing places without the tap", () => {
    const onPlace = vi.fn();
    const onBack = vi.fn();
    const node = <ConfirmView preview={preview()} busy={false} onBack={onBack} onPlace={onPlace} />;
    const words = text(renderToStaticMarkup(node));
    expect(words).toContain("Total margin (estimate) ₹7,500.00");
    expect(words).toContain("Max loss −₹7,500.00");
    expect(words.indexOf("1. Buy NIFTY 03 NOV 24300 PE")).toBeLessThan(words.indexOf("2. Sell NIFTY 03 NOV 24500 PE"));
    expect(words).toContain("Hedge");
    const els = elements(node);
    expect(onPlace).not.toHaveBeenCalled();
    by(els, "data-basket", "place").onClick!();
    by(els, "data-basket", "back").onClick!();
    expect(onPlace).toHaveBeenCalledTimes(1);
    expect(onBack).toHaveBeenCalledTimes(1);
    const refused = elements(<ConfirmView preview={preview({ can_execute: false, confirm_label: null, refusals: ["This basket opens 2 positions; ..."] })}
      busy={false} onBack={onBack} onPlace={onPlace} />);
    expect(by(refused, "data-basket", "place").disabled).toBe(true);
    expect(by(elements(<ConfirmView preview={null} busy={false} onBack={onBack} onPlace={onPlace} />), "data-basket", "place").disabled).toBe(true);
  });

  it("a partial fill is shown per leg with what to do; the remaining legs go again only on the user's tap", () => {
    const result: BasketResult = { mode: "PAPER", status: "PARTIAL", reasons: [], placed: 1, next_steps: ["1 leg filled and is open in practice mode."],
      legs: [{ seq: 1, leg: 1, label: "NIFTY 03 NOV 24300 PE", side: "BUY", lots: 1, status: "PLACED", reasons: [], filled: { price: 100.1, quantity: 75, lots: 1 }, order_id: 1, trade_id: 2 },
        { seq: 2, leg: 0, label: "NIFTY 03 NOV 24500 PE", side: "SELL", lots: 1, status: "REFUSED", reasons: ["You already hold 3 positions, your limit."], filled: null, order_id: null, trade_id: null }] };
    const on = { onRemaining: vi.fn(), onEdit: vi.fn(), onClear: vi.fn(), onClose: vi.fn() };
    const node = <ResultView result={result} {...on} />;
    const words = text(renderToStaticMarkup(node));
    expect(words).toContain("1 of 2 legs filled; 1 did not.");
    expect(words).toContain("Filled");
    expect(words).toContain("Refused");
    expect(words).toContain("You already hold 3 positions, your limit.");
    expect(on.onRemaining).not.toHaveBeenCalled();
    by(elements(node), "data-basket", "remaining").onClick!();
    expect(on.onRemaining).toHaveBeenCalledTimes(1);
    expect(remainingLegs(["sell 24500", "buy 24300"], result)).toEqual(["sell 24500"]);
    expect(resultHeadline({ ...result, status: "PLACED", placed: 2 })).toBe("All 2 legs placed in practice mode, hedge legs first.");
    const none = elements(<ResultView result={{ ...result, status: "REFUSED", placed: 0, reasons: ["Nothing was placed."] }} {...on} />);
    expect(none.some((e) => (e.props as Props)["data-basket"] === "remaining")).toBe(false);
    by(none, "data-basket", "edit").onClick!();
    expect(on.onEdit).toHaveBeenCalled();
  });

  it("the panel is a bottom sheet on a phone and a drawer from md, in the PAPER frame; the badge opens it", () => {
    expect(panelSource).toContain("<PracticeFrame />");
    expect(panelSource).toContain("fixed inset-x-0 bottom-0");
    expect(panelSource).toContain("md:right-0");
    expect(panelSource).not.toMatch(/text-\[\d+px\]/);
    expect(panelSource).not.toMatch(/mode:\s*"LIVE"|placeOrder/);
    expect(tradingSource).toContain("onBasket={() => setBasketOpen(true)}");
  });
});

describe("FNO-2 basket: save / load, broker app, Options Builder", () => {
  it("a saved basket keeps the legs (price and a sold leg's stop) and loads back the same", () => {
    const legs = [leg({ side: "BUY", strike: 24300, price: 100, stopLoss: 90 }), leg()];
    const s = savedOf(legs, " Bull put ", "NRML", new Date("2026-10-27T05:00:00Z"));
    expect(s).toEqual({ name: "Bull put", underlying: "NIFTY", product: "NRML", saved_at: "2026-10-27T05:00:00.000Z", legs: [
      { expiry: EXPIRY, strike: 24300, right: "PE", side: "BUY", lots: 1, price: 100 },
      { expiry: EXPIRY, strike: 24500, right: "PE", side: "SELL", lots: 1, price: 200, stop_loss: 300 }] });
    expect(legsOf(s).map((l) => [l.side, l.strike, l.price, l.stopLoss])).toEqual([["BUY", 24300, 100, null], ["SELL", 24500, 200, 300]]);
  });

  it("saving on a stale version shows the other device's list (no overwrite)", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new ApiError(409, "conflict", "", JSON.stringify({ detail: { version: 5, current: { items: { abcd1234: { name: "x" } } } } })));
    const out = await saveBaskets({}, 4, fetcher as never);
    expect(out.ok).toBe(false);
    expect(!out.ok && out.conflict?.version).toBe(5);
    expect(fetcher.mock.calls[0][0]).toBe("/me/prefs/fno_baskets?version=4");
  });

  it("Trade in your broker app: a Kite basket of the server's legs (hedge first), nothing sent by ATP", () => {
    const link = kiteFnoBasketLink([{ tradingsymbol: "NIFTY26N0324300PE", exchange: "NFO", transaction_type: "BUY", quantity: 75 },
      { tradingsymbol: "NIFTY26N0324500PE", exchange: "NFO", transaction_type: "SELL", quantity: 75 }], "pubkey");
    expect(link?.action).toBe(KITE_BASKET_URL);
    expect(JSON.parse(link!.fields.data).map((o: { transaction_type: string }) => o.transaction_type)).toEqual(["BUY", "SELL"]);
    expect(link?.summary).toBe("BUY 75 NIFTY26N0324300PE, SELL 75 NIFTY26N0324500PE");
    expect(kiteFnoBasketLink([{ tradingsymbol: "RELIANCE", exchange: "NSE", transaction_type: "BUY", quantity: 1 }], "k")).toBeNull();
    expect(kiteFnoBasketLink([{ tradingsymbol: "X", exchange: "NFO", transaction_type: "BUY", quantity: 1 }], "")).toBeNull();
  });

  it("Open in Options Builder hands over the legs with prices and lot sizes", () => {
    const h = builderHandoff([leg({ side: "BUY", strike: 24300, price: 100 }), leg()], [75, 75], 24480, 100);
    expect(h).toMatchObject({ underlying: "NIFTY", spot: 24480, step: 100, lot_size: 75, near_expiry: EXPIRY, next_expiry: null });
    expect(h?.legs[1]).toEqual({ direction: "SELL", option_type: "PE", strike: 24500, premium: 200, lots: 1, lot_size: 75, expiry: EXPIRY, iv: 0.13 });
    expect(builderHandoff([leg({ price: null })], [75], 24480, 100)).toBeNull();
  });
});

describe("owner F1: no product saved - the basket pre-selects none", () => {
  it("Execute stays disabled with one line why until the user picks MIS or NRML", () => {
    const none = body([leg()], { product: null });
    expect(none.html).toContain('data-basket="product-unset"');
    expect(none.html).toMatch(/disabled=""[^>]*data-basket="execute"|data-basket="execute"[^>]*disabled=""/);
    expect(none.html).not.toMatch(/aria-checked="true"[^>]*data-option="(MIS|NRML)"/);
    const picked = body([leg()], { product: "NRML" });
    expect(picked.html).not.toContain('data-basket="product-unset"');
    expect(picked.html).not.toMatch(/disabled=""[^>]*data-basket="execute"|data-basket="execute"[^>]*disabled=""/);
  });
});
