import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import ticketSource from "./OrderTicketV2.tsx?raw";
import { TicketBody, advancedSummary, needsStop } from "./OrderTicketV2";
import { newDraft, type TicketTarget } from "./ticketModel";

/** UX-9 ticket LOOK: side, lots, order type and Review in view; product and the stop-loss under "More options". */

const target = { symbol: "NIFTY 24800 CE", lotSize: 75, ltp: 120, bid: 119.5, ask: 120.5 } as unknown as TicketTarget;
const noop = () => undefined;

describe("UX-9 ticket look", () => {
  it("the summary line says the product and the stop-loss as they stand", () => {
    expect(advancedSummary({ product: "MIS", stopLoss: "" })).toBe("MIS · no stop-loss");
    expect(advancedSummary({ product: "NRML", stopLoss: " 95.5 " })).toBe("NRML · stop-loss 95.5");
  });

  it("product and stop-loss are inside the closed More options; side, lots and order type before it", () => {
    const html = renderToStaticMarkup(<TicketBody target={target} draft={newDraft("BUY")} step={{ kind: "edit" }} busy={false} problem={null}
      idPrefix="x" onDraft={noop} onReview={noop} onPlace={noop} onBack={noop} onClose={noop} />);
    const adv = html.indexOf('data-ticket="advanced"');
    expect(adv).toBeGreaterThan(0);
    expect(html).not.toMatch(/<details[^>]*\sopen/);
    for (const before of ['data-ticket="side"', 'data-ticket="lots"', 'data-ticket="order-type"']) expect(html.indexOf(before)).toBeLessThan(adv);
    for (const inside of ['data-ticket="product"', 'data-ticket="stop"']) expect(html.indexOf(inside)).toBeGreaterThan(adv);
    expect(html).toContain("MIS · no stop-loss");
    // The "no product saved" note stays in view (outside the closed section).
    expect(html.indexOf('data-ticket="product-unset"')).toBeLessThan(adv);
  });

  it("a review that asks for a stop-loss opens More options; other problems leave it closed", () => {
    expect(needsStop("A sold option needs a stop-loss")).toBe(true);
    expect(needsStop("Not enough margin")).toBe(false);
    expect(needsStop(null)).toBe(false);
    const html = renderToStaticMarkup(<TicketBody target={target} draft={newDraft("SELL")} step={{ kind: "edit" }} busy={false}
      problem="A sold option needs a stop-loss" idPrefix="y" onDraft={noop} onReview={noop} onPlace={noop} onBack={noop} onClose={noop} />);
    expect(html).toMatch(/<details[^>]*data-ticket="advanced"[^>]*open=""/);
  });

  it("look only: the review and place calls are unchanged", () => {
    expect(ticketSource).toContain("onReview={() => void t.review()} onPlace={() => void t.place()}");
  });
});
