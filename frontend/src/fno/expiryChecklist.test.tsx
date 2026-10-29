import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BasketStore } from "./basket";
import { addToBasketNote, ExpiryChecklistListV2 } from "./ExpiryChecklistV2";
import { addNextExpiry, type ExpiryChecklist, type ExpiryRow } from "./expiryChecklist";

/**
 * AUD-T2: the expiry check on Positions - rendering (only rows that need his eye open, explanations and the broker's
 * own link in Details, the no-price sentence) and "Add to basket" (adds exactly one leg, places nothing).
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const ADVICE = /\b(should|recommend\w*|advice|advise|best|target\w*|stop[- ]?loss|you must|guarantee\w*)\b/i;

const row = (over: Partial<ExpiryRow> = {}): ExpiryRow => ({
  trade_id: 1, label: "NIFTY 04 NOV 24500 CE", symbol: "NIFTY 24500 CE 04 NOV 26", underlying: "NIFTY", right: "CE", strike: 24500,
  expiry: "2026-11-04", when: "next_session", side: "SELL", settlement: "cash", moneyness: "in", priced: true,
  next_expiry: "2026-11-11",
  basket_leg: { underlying: "NIFTY", expiry: "2026-11-11", strike: 24500, right: "CE", side: "SELL", lots: 2, label: "NIFTY 11 NOV 24500 CE" },
  headline: "NIFTY 04 NOV 24500 CE: expires next session, Wed 4 Nov 2026.", money_text: "In the money now: price is above the strike.",
  settlement_text: "Index contract: settles in cash, no shares change hands.", next_text: "Next expiry: Wed 11 Nov 2026.", ...over,
});

const view = (rows: ExpiryRow[], over: Partial<ExpiryChecklist> = {}): ExpiryChecklist => ({
  as_of: "2026-11-03T06:00:00Z", today: "2026-11-03", rows,
  notes: ["Index F&O settles in cash. No shares change hands at expiry.", "Information only. Nothing is closed or placed for you."],
  brokers: [], ...over,
});

afterEach(() => { vi.unstubAllGlobals(); });

describe("ExpiryChecklistListV2", () => {
  it("renders nothing without rows", () => {
    expect(renderToStaticMarkup(<ExpiryChecklistListV2 view={view([])} store={new BasketStore([])} />)).toBe("");
  });

  it("shows when it expires, in the money and the next expiry once, explanations in Details", () => {
    const html = renderToStaticMarkup(<ExpiryChecklistListV2 view={view([row()])} store={new BasketStore([])} />);
    const t = text(html);
    expect(t).toContain("Expiry check: 1 position");
    expect(t).toContain("expires next session, Wed 4 Nov 2026");
    expect(t).toContain("In the money");
    expect(t).toContain("Cash settled");
    expect(t).toContain("Next expiry: Wed 11 Nov 2026.");
    expect(html).toContain('data-expiry="add-to-basket"');
    expect(html).toMatch(/<details data-details="positions.expiry-checklist"[^>]*>(?:(?!<\/details>).)*Index F&amp;O settles in cash/s);
    expect(t.split("Next expiry").length).toBe(2);                 // no repeated line
    expect(t).not.toContain("Index contract: settles in cash");   // the cash line lives in Details, not open
    expect(t).not.toMatch(ADVICE);
  });

  it("a stock position shows delivery open and the broker's own help link in Details", () => {
    const stock = row({ underlying: "RELIANCE", settlement: "delivery", settlement_text: "Stock option: settles by delivery of shares if in the money at expiry." });
    const html = renderToStaticMarkup(<ExpiryChecklistListV2 store={new BasketStore([])} view={view([stock], {
      brokers: [{ broker: "zerodha", name: "Zerodha", note: "Zerodha raises the margin near expiry.", url: "https://support.zerodha.com/x" }] })} />);
    expect(text(html)).toContain("Stock option: settles by delivery of shares");
    expect(html).toContain('href="https://support.zerodha.com/x"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("no price: the plain sentence, no money chip", () => {
    const html = renderToStaticMarkup(<ExpiryChecklistListV2 store={new BasketStore([])} view={view([row({
      moneyness: null, priced: false, money_text: "No live price for NIFTY now, so in or out of the money is not known." })])} />);
    expect(text(html)).toContain("No live price for NIFTY now");
    expect(text(html)).not.toContain("In the money");
  });

  it("no next contract in the list: no Add to basket", () => {
    const html = renderToStaticMarkup(<ExpiryChecklistListV2 store={new BasketStore([])} view={view([row({ basket_leg: null, next_text: "Next expiry: not in the contract list yet." })])} />);
    expect(html).not.toContain("add-to-basket");
  });
});

describe("Add to basket", () => {
  it("adds exactly one next-expiry leg and places nothing", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const store = new BasketStore([]);
    const leg = row().basket_leg!;
    expect(addToBasketNote(store, leg)).toBe("Added to basket: NIFTY 11 NOV 24500 CE. Review it there.");
    const legs = store.snapshot();
    expect(legs).toHaveLength(1);
    expect(legs[0]).toMatchObject({ underlying: "NIFTY", expiry: "2026-11-11", strike: 24500, right: "CE", side: "SELL", lots: 2, price: null });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("keeps the legs already in the basket and says when it is full", () => {
    const store = new BasketStore([]);
    store.add({ underlying: "BANKNIFTY", expiry: "2026-11-25", strike: 52000, right: "PE", side: "BUY", lots: 1, price: null, label: null });
    expect(addNextExpiry(store, row().basket_leg!)).toBe(true);
    expect(store.snapshot().map((l) => l.underlying)).toEqual(["BANKNIFTY", "NIFTY"]);
    const full = new BasketStore([]);
    for (let k = 0; k < 12; k += 1) full.add({ underlying: "X", expiry: "2026-11-25", strike: 100 + k, right: "CE", side: "BUY", lots: 1, price: null, label: null });
    expect(addToBasketNote(full, row().basket_leg!)).toBe("The basket is full. Remove a leg first.");
    expect(full.snapshot()).toHaveLength(12);
  });
});
