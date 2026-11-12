import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import type { FnoContract, OrderPreview, OrderResult } from "./api";
import { ActionSheetBody, basketChipLabel, type ActionTarget } from "./ChainActionSheetV2";
import { Review, TicketBody, advancedSummary, needsStop } from "./OrderTicketV2";
import { askOf, draftProblems, newDraft, resultText, type TicketTarget } from "./ticketModel";
import { leg } from "../optionChain/chain.fixture";

/**
 * M96 Q2 (owner "Default yes"): the ticket's optional Target - empty by default (never a platform value), checked only as
 * a price (the server checks the side against the fill), sent with the order, named in the confirm step and in the result.
 * M96 bug: the basket chips say the lots a leg is really added with, or no number when none is saved (M68: never "1 lot").
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;
const TT: TicketTarget = { underlying: "NIFTY", expiry: "2026-11-03", strike: 24500, right: "CE", label: "NIFTY 03 NOV 24500 CE", lotSize: 75, ltp: 250, bid: 249.5, ask: 250.5 };
const CONTRACT: FnoContract = {
  underlying: "NIFTY", tradingsymbol: "NIFTY26NOV24500CE", exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-11-03", strike: 24500,
  right: "CE", lot_size: 75, tick_size: 0.05, label: "NIFTY 03 NOV 24500 CE", freeze_quantity: null,
};

function preview(over: Partial<OrderPreview> = {}): OrderPreview {
  return {
    mode: "PAPER", practice: "", contract: CONTRACT, label: CONTRACT.label, side: "BUY", lots: 2, lot_size: 75, units: 150, order_type: "MARKET",
    product: "MIS", price: 250, price_source: "live", fill_price: 250, fill_note: null, stop_loss: null, margin: null, max_loss: null, charges: null,
    risk: {}, errors: [], refusals: [], warnings: [], can_place: true, confirm_label: "Place PAPER order: Buy 2 lots (150 qty)",
    as_of: "2026-10-27T05:00:00+00:00", ...over,
  };
}

describe("M96 Q2 ticket Target", () => {
  it("is empty by default: no platform value, none sent", () => {
    const d = newDraft("BUY", { lots: 2, product: "MIS", orderType: "MARKET" });
    expect(d.target).toBe("");
    expect(askOf(TT, d, "ref-12345678").target).toBeNull();
    const html = renderToStaticMarkup(<TicketBody target={TT} draft={d} step={{ kind: "edit" }} busy={false} problem={null}
      idPrefix="x" onDraft={noop} onReview={noop} onPlace={noop} onBack={noop} onClose={noop} />);
    expect(html).toMatch(/<input id="x-target"[^>]*data-ticket="target" value=""\/>/);
    expect(text(html)).toContain("Target for the position (optional)");
    expect(advancedSummary(d)).toBe("MIS · no stop-loss");               // named only when he enters one
  });

  it("his own value is sent as a price; anything else is a plain sentence", () => {
    const d = { ...newDraft("BUY", { lots: 1, product: "MIS", orderType: "MARKET" }), target: "312.5" };
    expect(askOf(TT, d, "ref-12345678").target).toBe(312.5);
    expect(draftProblems(d)).toEqual([]);
    expect(advancedSummary(d)).toBe("MIS · no stop-loss · target 312.5");
    for (const bad of ["abc", "-5", "0"]) expect(draftProblems({ ...d, target: bad })).toEqual(["The target is not a price"]);
    // the server's wrong-side sentence opens "More options" to show the field
    expect(needsStop("The target (240.00) must be above the price (250.00) for a bought option")).toBe(true);
  });

  it("the confirm step names the target; none entered - no Target line", () => {
    expect(text(renderToStaticMarkup(<MemoryRouter><Review preview={preview({ target: 312.5 })} /></MemoryRouter>))).toMatch(/Target 312\.50 Waits as a limit exit after the fill/);
    expect(renderToStaticMarkup(<MemoryRouter><Review preview={preview()} /></MemoryRouter>)).not.toContain('data-line="target"');
  });

  it("the result says the target was armed (or why not)", () => {
    const placed: OrderResult = { ...preview({ target: 312.5 }), status: "PLACED", reasons: [], order_id: 1, trade_id: 2,
      filled: { price: 250, quantity: 75, lots: 1 }, target_order: { armed: true, text: "Target armed at 312.50." } };
    expect(resultText(placed)).toMatch(/at 250\.00\. Target armed at 312\.50\.$/);
    expect(resultText({ ...placed, target_order: null })).toMatch(/at 250\.00\.$/);
  });
});

describe("M96 basket chip label", () => {
  it("says his saved lots, or no number when none is saved (never a default 1)", () => {
    expect(basketChipLabel("BUY", null)).toBe("Buy leg");
    expect(basketChipLabel("SELL", undefined)).toBe("Sell leg");
    expect(basketChipLabel("BUY", 1)).toBe("Buy leg, 1 lot");
    expect(basketChipLabel("SELL", 3)).toBe("Sell leg, 3 lots");
  });

  it("the sheet's chips use it", () => {
    const target: ActionTarget = { underlying: "NIFTY", expiry: "2026-11-03", strike: 24500, right: "CE", spotSymbol: "NIFTY 50", leg: leg({ ltp: 250 }) };
    const sheet = (basketLots?: number | null) => text(renderToStaticMarkup(<MemoryRouter>
      <ActionSheetBody target={target} contract={CONTRACT} contractProblem={null} held={[]} panel="basket" note={null} watchlists={null} level=""
        watchlistsOn={false} alertsOn={false} basketLots={basketLots} onTrade={noop} onExit={noop} onPanel={noop} onBasket={noop}
        onWatchlist={noop} onLevel={noop} onAlert={noop} onChart={noop} />
    </MemoryRouter>));
    expect(sheet(null)).toMatch(/Buy leg Sell leg/);
    expect(sheet(null)).not.toContain("1 lot");
    expect(sheet(2)).toContain("Buy leg, 2 lots");
  });
});
