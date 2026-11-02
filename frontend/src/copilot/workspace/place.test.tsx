import { describe, expect, it, vi } from "vitest";
import placeSource from "./PlanPlaceV2.tsx?raw";
import ticketSource from "../../fno/OrderTicketV2.tsx?raw";
import { BasketStore, BASKET_KEY } from "../../fno/basket";
import type { OrderResult } from "../../fno/api";
import { prefillChips } from "../ticket/intent";
import { basketLegsOf, equityTicketOf, singleLegTicket, targetArm, type PlanState } from "./place";
import { EMPTY_EQUITY, EMPTY_EXITS, legOf, type LegDraft } from "./plan";

/**
 * M75 W-7 (owner order M75 CW-5 / CW-7; the PR plan's W-7 row): placing the plan goes ONLY through the existing ticket,
 * basket and equity ticket (PAPER = LIVE: the same routes, no mode in a body); every Place is his tap (ADR-0006); only
 * his own plan numbers go across; a field left Not set stays empty there. e2e/copilot-plan-v2.e2e.ts proves the clicks.
 */
const INST = { spot: "24500", lotSize: "75", step: "50" };
const SELL_CE: LegDraft = { direction: "SELL", optionType: "CE", strike: "24700", expiry: "2026-11-25", lots: "2", premium: "85" };
const BUY_CE: LegDraft = { ...SELL_CE, direction: "BUY", strike: "24900", premium: "30" };
const plan = (over: Partial<PlanState> = {}): PlanState => ({ underlying: "NIFTY 50", kind: "OPTIONS", legs: [legOf(SELL_CE, INST, "OPTIONS")!], equity: EMPTY_EQUITY, exits: EMPTY_EXITS, ...over });
const filled = (price: number): OrderResult => ({ status: "PLACED", reasons: [], order_id: 9, trade_id: 7, filled: { price, quantity: 150, lots: 2 } } as unknown as OrderResult);

describe("W-7: one leg goes to the ONE ticket with his plan's numbers", () => {
  it("contract, side, lots and price marked 'from your trade plan'; no stop when he set none", () => {
    const t = singleLegTicket(plan())!;
    expect(t).toMatchObject({ underlying: "NIFTY 50", expiry: "2026-11-25", strike: 24700, right: "CE", side: "SELL" });
    expect(t.prefill).toEqual({ lots: 2, price: "85", from: { lots: "plan", price: "plan" } });
    expect(prefillChips(t.prefill).map((c) => c.text)).toEqual(["2 lots from your trade plan", "limit price 85 from your trade plan"]);
  });
  it("his stop becomes the ticket's stop-loss level", () => {
    const t = singleLegTicket(plan({ exits: { ...EMPTY_EXITS, stopKind: "POINTS", stop: "40" } }))!;
    expect(t.prefill.stopLoss).toBe("125");
  });
  it("a future has no strike on the ticket", () => {
    const fut = legOf({ ...SELL_CE, optionType: null, strike: "", premium: "24550" }, INST, "FUTURE")!;
    expect(singleLegTicket(plan({ kind: "FUTURE", legs: [fut] }))).toMatchObject({ strike: null, right: "FUT" });
  });
  it("several legs are not a single ticket; equity is not an F&O ticket", () => {
    expect(singleLegTicket(plan({ legs: [legOf(SELL_CE, INST, "OPTIONS")!, legOf(BUY_CE, INST, "OPTIONS")!] }))).toBeNull();
    expect(singleLegTicket(plan({ kind: "EQUITY", legs: [] }))).toBeNull();
  });
});

describe("W-7: several legs go to the ONE basket, in a basket of their own", () => {
  it("the legs as the basket takes them; the sold leg's stop is left for the basket to ask", () => {
    const legs = basketLegsOf(plan({ legs: [legOf(BUY_CE, INST, "OPTIONS")!, legOf(SELL_CE, INST, "OPTIONS")!] }))!;
    expect(legs).toHaveLength(2);
    expect(legs[1]).toEqual({ underlying: "NIFTY 50", expiry: "2026-11-25", strike: 24700, right: "CE", side: "SELL", lots: 2, price: 85, label: null, stopLoss: null, iv: null });
  });
  it("a memory-only basket never writes the tab's own basket", () => {
    const written = new Map<string, string>();
    vi.stubGlobal("sessionStorage", { getItem: (k: string) => written.get(k) ?? null, setItem: (k: string, v: string) => written.set(k, v), removeItem: (k: string) => written.delete(k) });
    const store = new BasketStore([], null);
    store.add({ underlying: "NIFTY", expiry: "2026-11-25", strike: 24700, right: "CE", side: "SELL", lots: 1, price: 85, label: null });
    expect(store.snapshot()).toHaveLength(1);
    expect(written.has(BASKET_KEY)).toBe(false);
    new BasketStore([], BASKET_KEY).add({ underlying: "NIFTY", expiry: "2026-11-25", strike: 24700, right: "CE", side: "SELL", lots: 1, price: 85, label: null });
    expect(written.has(BASKET_KEY)).toBe(true);                                     // the tab's own basket still keeps its legs
    vi.unstubAllGlobals();
  });
});

describe("W-7: equity goes to the equity ticket", () => {
  it("side, entry, quantity and his stop / target levels; empty when he set none", () => {
    const eq = { direction: "BUY" as const, quantity: "10", price: "2500" };
    expect(equityTicketOf(plan({ kind: "EQUITY", legs: [], equity: eq }))).toEqual({ symbol: "NIFTY 50", direction: "LONG", entry: "2500", quantity: "10", stop_loss: "", target: "" });
    const withExits = equityTicketOf(plan({ kind: "EQUITY", legs: [], equity: eq, exits: { ...EMPTY_EXITS, stopKind: "PCT", stop: "2", targetKind: "POINTS", target: "100" } }))!;
    expect(withExits).toMatchObject({ stop_loss: "2450", target: "2600" });
    expect(equityTicketOf(plan({ kind: "EQUITY", legs: [], equity: EMPTY_EQUITY }))).toBeNull();
  });
});

describe("W-7: after a fill, his target is armed only on his tap, from the fill price", () => {
  it("the level is measured from the fill (the real entry)", () => {
    const p = plan({ exits: { ...EMPTY_EXITS, targetKind: "POINTS", target: "40" } });
    expect(targetArm(p, filled(80))).toEqual({ level: 40, rupees: 6000 });
    expect(targetArm(plan(), filled(80))).toBeNull();                                  // no target set: nothing
    expect(targetArm(p, { ...filled(80), status: "REFUSED", trade_id: null } as OrderResult)).toBeNull();
  });
  it("the place part uses only the existing ticket / basket / equity ticket and the existing exit route; each a tap", () => {
    for (const part of ['import("../../fno/OrderTicketV2")', 'import("../../fno/BasketPanelV2")', 'import("../../components/PaperTicketCard")', '"/paper/fno/resting/exit"']) {
      expect(placeSource).toContain(part);
    }
    for (const forbidden of ["fnoApi.place", "placeMulti", "basketApi", "/paper/fno/order", "live_trading", '"LIVE"', "useEffect("]) expect(placeSource).not.toContain(forbidden);
    // the ONE ticket still has its own confirm (Review) before its Buy / Sell
    expect(ticketSource).toContain("api.preview(");
  });
});
