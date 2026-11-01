import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TicketBody } from "./OrderTicketV2";
import { LOTS_MISSING, LOTS_UNSET_TEXT, askOf, draftProblems, firstMissing, newDraft, unitsText, type TicketTarget } from "./ticketModel";
import { BasketStore } from "./basket";
import { lotsProblems, readyStrategy, basketProblems } from "./basketModel";
import { futureLeg } from "./hubModel";
import { LOTS_KEY, positionSizeOf, savedLots, sizesItself, type TradeField, type TradeSettingsView } from "../settings/tradeSettings";
import type { ChainRow } from "../optionChain/types";
import sheetSource from "./PositionActionsV2.tsx?raw";

/** M68 PR-2 (TS1-019 .. 023): no lots are pre-chosen - the ticket asks for them (focused, with "Save as my default"), a
 *  basket leg starts empty unless he saved his lots, a ready strategy uses only his own width and lots. */

const target = { underlying: "NIFTY", expiry: "2026-12-31", strike: 24800, right: "CE", label: "NIFTY 24800 CE", lotSize: 75, ltp: 120,
  bid: 119.5, ask: 120.5 } as TicketTarget;
const noop = () => undefined;
const body = (draft = newDraft("BUY"), defaults?: Parameters<typeof TicketBody>[0]["defaults"]) => renderToStaticMarkup(
  <TicketBody target={target} draft={draft} step={{ kind: "edit" }} busy={false} problem={null} idPrefix="t" defaults={defaults} saveDefaults
    onDraft={noop} onReview={noop} onPlace={noop} onBack={noop} onClose={noop} />);

const field = (key: string, value: unknown, saved = true): TradeField => ({
  key, label: key, kind: "whole", help: "", unit: "", choices: [], low: null, high: null, low_open: false, term: null, fact: false,
  wired: true, scope: "user", value, saved,
});
const view = (fields: TradeField[]): TradeSettingsView => ({ admin: false, page: "/settings/trade-settings",
  sections: [{ id: "position_size", title: "Position size", fields, today: null }] });

describe("M68 PR-2: the ticket asks for the lots", () => {
  it("a new ticket starts with no lots and asks them first", () => {
    const d = newDraft("BUY");
    expect(d.lots).toBeNull();
    expect(firstMissing(d)).toBe("lots");
    expect(draftProblems(d)[0]).toBe(LOTS_MISSING);
    expect(firstMissing({ ...d, lots: 2 })).toBe("order-type");
    expect(newDraft("BUY", { lots: 3 }).lots).toBe(3);                              // his own saved lots start it
    expect(unitsText(null, 75)).toBe("Lot size 75");
    expect(askOf(target, { ...d, lots: 2, orderType: "MARKET", product: "MIS" }, "ref").lots).toBe(2);
  });

  it("the empty lots field says so and offers no save; entered lots offer Save as my default, never for his own saved lots", () => {
    const empty = body();
    expect(empty).toMatch(/value=""[^>]*data-ticket="lots"|data-ticket="lots"[^>]*value=""/);
    expect(empty).toContain('data-ticket="lots-unset"');
    expect(empty).toContain(LOTS_UNSET_TEXT.replace("(", "("));
    expect(empty).not.toContain(`data-save-default="${LOTS_KEY}"`);
    expect(empty).toMatch(/disabled=""[^>]*data-ticket="review"|data-ticket="review"[^>]*disabled=""/);
    expect(body({ ...newDraft("BUY"), lots: 2 })).toContain(`data-save-default="${LOTS_KEY}"`);
    expect(body(newDraft("BUY", { lots: 2 }), { lots: 2 })).not.toContain(`data-save-default="${LOTS_KEY}"`);
  });
});

describe("M68 PR-2: his saved Position size", () => {
  it("only what he saved is read", () => {
    expect(positionSizeOf(null)).toEqual({});
    expect(savedLots(view([field(LOTS_KEY, null, false)]))).toBeNull();
    expect(savedLots(view([field(LOTS_KEY, 4)]))).toBe(4);
    expect(savedLots(view([field(LOTS_KEY, 4), field("position_size.mode", "FIXED_QUANTITY")]))).toBeNull();   // another way of sizing
    expect(sizesItself(view([]))).toBe(false);
    expect(sizesItself(view([field("position_size.quantity", 30), field("position_size.mode", "FIXED_QUANTITY")]))).toBe(true);
    expect(sizesItself(view([field("position_size.mode", "RISK_BASED")]))).toBe(true);
  });
});

describe("M68 PR-2: basket legs and ready strategies", () => {
  const rows = [24700, 24750, 24800, 24850, 24900].map((strike) => ({
    strike, call: { ltp: 100, iv: 14 }, put: { ltp: 90, iv: 15 },
  })) as unknown as ChainRow[];
  const inp = (width_steps: number | null, lots: number | null) => ({ underlying: "NIFTY", expiry: "2026-12-31", rows, around: 24800,
    settings: { width_steps, lots, stop_pct: null } });

  it("a leg with no lots of his own is empty and the basket asks for it", () => {
    expect(futureLeg({ underlying: "NIFTY", expiry: "2026-12-31", label: "F", ltp: 1 } as never, "BUY").lots).toBeNull();
    const store = new BasketStore([]);
    store.add({ ...futureLeg({ underlying: "NIFTY", expiry: "2026-12-31", label: "F", ltp: 1 } as never, "BUY"), stopLoss: null });
    store.add({ ...futureLeg({ underlying: "NIFTY", expiry: "2026-12-31", label: "F", ltp: 1 } as never, "BUY", 2), stopLoss: null });
    expect(store.snapshot()[0].lots).toBe(2);                                      // an empty leg takes the lots he gives it
    expect(lotsProblems([{ lots: 1 }, { lots: null }])).toEqual(["Leg 2: enter its lots."]);
    expect(basketProblems([{ ...store.snapshot()[0], lots: null }])).toContain("Leg 1: enter its lots.");
  });

  it("a ready strategy uses only his width and lots: no lots -> empty legs; a spread with no width asks for it", () => {
    const straddle = readyStrategy("Straddle", inp(null, null));                    // no width needed (both legs at the strike)
    expect(straddle.problem).toBeNull();
    expect(straddle.legs.map((l) => l.lots)).toEqual([null, null]);
    const spread = readyStrategy("Bull put spread", inp(null, 2));
    expect(spread.legs).toEqual([]);
    expect(spread.problem).toMatch(/^Needs your setting: Spread width/);
    expect(readyStrategy("Bull put spread", inp(1, 2)).legs.map((l) => [l.strike, l.lots])).toEqual([[24750, 2], [24800, 2]]);
  });
});

describe("M68 PR-2 (TS1-021): Exit part starts empty", () => {
  it("the sheet's lots start Not set and Confirm waits for his lots; the exit itself is never blocked", () => {
    expect(sheetSource).toContain("useState<number | null>(null)");
    expect(sheetSource).toContain('disabled={busy || (ask.kind === "part" && lots === null)}');
    expect(sheetSource).not.toMatch(/useState\(1\)/);
  });
});
