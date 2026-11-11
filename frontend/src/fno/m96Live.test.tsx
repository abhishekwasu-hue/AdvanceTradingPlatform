import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import type { FnoContract, OrderPreview, OrderResult } from "./api";
import { Review, TicketBody, TicketModeBadge } from "./OrderTicketV2";
import { LIVE_MARKET_ONLY, LIVE_OFF_TEXT, askOf, draftProblems, newDraft, resultText, type Draft, type TicketTarget } from "./ticketModel";

/**
 * M96-Q1 (owner, changed): the ticket's PAPER / LIVE choice - PAPER unless he picks LIVE; while LIVE is off (the owner's
 * switches) the LIVE choice says so in one sentence and cannot be reviewed or placed; the confirm step names the mode in
 * large text; the header chip follows the choice.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;
const TT: TicketTarget = { underlying: "NIFTY", expiry: "2026-11-03", strike: 24500, right: "CE", label: "NIFTY 03 NOV 24500 CE", lotSize: 75, ltp: 250, bid: 249.5, ask: 250.5 };
const CONTRACT: FnoContract = {
  underlying: "NIFTY", tradingsymbol: "NIFTY26NOV24500CE", exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-11-03", strike: 24500,
  right: "CE", lot_size: 75, tick_size: 0.05, label: "NIFTY 03 NOV 24500 CE", freeze_quantity: null,
};
const READY: Draft = { ...newDraft("BUY", { lots: 1, product: "MIS", orderType: "MARKET" }) };

function body(draft: Draft, live: { open: boolean; text: string | null } | null) {
  return renderToStaticMarkup(<TicketBody target={TT} draft={draft} step={{ kind: "edit" }} busy={false} problem={null} idPrefix="x" live={live}
    onDraft={noop} onReview={noop} onPlace={noop} onBack={noop} onClose={noop} />);
}

function preview(over: Partial<OrderPreview> = {}): OrderPreview {
  return {
    mode: "PAPER", practice: "", contract: CONTRACT, label: CONTRACT.label, side: "BUY", lots: 1, lot_size: 75, units: 75, order_type: "MARKET",
    product: "MIS", price: 250, price_source: "live", fill_price: 250, fill_note: null, stop_loss: null, margin: null, max_loss: null, charges: null,
    risk: {}, errors: [], refusals: [], warnings: [], can_place: true, confirm_label: null, as_of: "2026-10-27T05:00:00+00:00", ...over,
  };
}

describe("M96-Q1 ticket mode", () => {
  it("is PAPER unless he picks LIVE, and is sent with the order", () => {
    expect(READY.mode).toBe("PAPER");
    expect(askOf(TT, READY, "ref-12345678").mode).toBe("PAPER");
    expect(askOf(TT, { ...READY, mode: "LIVE" }, "ref-12345678").mode).toBe("LIVE");
    const html = body(READY, null);
    expect(html).toContain('aria-checked="true" data-option="PAPER"');
    expect(html).toContain('aria-checked="false" data-option="LIVE"');
    expect(html).toContain('data-ticket="mode"');
  });

  it("LIVE while the admin keeps it off: one plain sentence, and no Review", () => {
    const html = body({ ...READY, mode: "LIVE" }, { open: false, text: LIVE_OFF_TEXT });
    expect(text(html)).toContain("Live trading is switched off by the admin.");
    expect(html).toMatch(/<button type="button" disabled=""[^>]*data-ticket="review"/);
    const open = body({ ...READY, mode: "LIVE" }, { open: true, text: null });
    expect(open).not.toContain('data-ticket="live-off"');
    expect(open).not.toMatch(/disabled=""[^>]*data-ticket="review"/);
    expect(body(READY, { open: false, text: LIVE_OFF_TEXT })).not.toContain('data-ticket="live-off"');   // PAPER is untouched
  });

  it("LIVE from the ticket is a Market order, said plainly", () => {
    expect(draftProblems({ ...READY, mode: "LIVE", orderType: "LIMIT", price: "250" })).toEqual([LIVE_MARKET_ONLY]);
    expect(draftProblems({ ...READY, mode: "LIVE" })).toEqual([]);
    expect(draftProblems({ ...READY, orderType: "LIMIT", price: "250" })).toEqual([]);
  });

  it("the confirm step names the mode in large text", () => {
    const live = renderToStaticMarkup(<MemoryRouter><Review preview={preview({ mode: "LIVE" })} /></MemoryRouter>);
    expect(live).toMatch(/class="text-t18 font-semibold text-warn" data-ticket="confirm-mode">LIVE order: real money, your broker</);
    const paper = renderToStaticMarkup(<MemoryRouter><Review preview={preview()} /></MemoryRouter>);
    expect(paper).toMatch(/data-ticket="confirm-mode">PAPER order: practice, no real money</);
  });

  it("the header chip and the result follow the mode", () => {
    expect(renderToStaticMarkup(<TicketModeBadge mode="PAPER" />)).toContain('data-mode-badge="paper"');
    expect(renderToStaticMarkup(<TicketModeBadge mode="LIVE" />)).toContain('data-mode-badge="live"');
    const placed: OrderResult = { ...preview({ mode: "LIVE" }), status: "PLACED", reasons: [], order_id: 1, trade_id: 2, filled: { price: 250, quantity: 75, lots: 1 } };
    expect(resultText(placed)).toMatch(/^Sent to your broker \(LIVE\): Bought 1 lot \(75 qty\)/);
    expect(resultText({ ...placed, mode: "PAPER" })).toMatch(/^Placed in practice mode: /);
  });
});
