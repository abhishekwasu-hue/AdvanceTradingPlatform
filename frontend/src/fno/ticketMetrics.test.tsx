import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import basketSource from "./BasketPanelV2.tsx?raw";
import { ConfirmView, confirmRiskLines } from "./BasketPanelV2";
import type { BasketPreview, OrderPreview } from "./api";
import type { RiskLine } from "./insights";
import ticketSource from "./OrderTicketV2.tsx?raw";
import { Review, reviewRiskLines } from "./OrderTicketV2";
import { TicketClock, basketSurfaces, ticketSurfaces, type TicketReport } from "./ticketMetrics";
import metricsSource from "./ticketMetrics.ts?raw";

/**
 * M76 MP-0: the baseline report of today's tickets - the warning surfaces counted at Place match what the confirm step
 * renders, one report per ticket, never awaited by Place, closed labels and numbers only. No UI change.
 */
const WARN: RiskLine = { tone: "warn", text: "Before the 03 Nov expiry: Made-up policy meeting on 29 Oct (market).", note: "From your events calendar." };
const INFO: RiskLine = { tone: "info", text: "Liquidity: quoted on both sides.", note: "" };

function preview(over: Partial<OrderPreview> = {}): OrderPreview {
  return {
    mode: "PAPER", practice: "Practice mode (PAPER)", contract: { underlying: "NIFTY", tradingsymbol: "X", exchange: "NFO", instrument_key: "NSE_FO|1",
      expiry: "2026-11-03", strike: 24500, right: "CE", lot_size: 75, tick_size: 0.05, label: "NIFTY 03 NOV 24500 CE", freeze_quantity: null },
    label: "NIFTY 03 NOV 24500 CE", side: "BUY", lots: 1, lot_size: 75, units: 75, order_type: "MARKET", product: "NRML", price: 150, price_source: "live",
    fill_price: 150.5, fill_note: null, stop_loss: null, margin: { value: 11287.5, basis: "The premium paid.", estimated: true },
    max_loss: { value: 11287.5, basis: "The premium you pay." }, charges: { total: 42.5, estimated: true }, risk: {},
    errors: [], refusals: [], warnings: [], can_place: true, confirm_label: null, as_of: "2026-10-27T05:00:00+00:00", ...over,
  } as OrderPreview;
}

function basket(over: Partial<BasketPreview> = {}, legIssues: { errors: string[]; refusals: string[] }[] = [{ errors: [], refusals: [] }, { errors: [], refusals: [] }]): BasketPreview {
  return {
    mode: "PAPER", practice: "", product: "MIS", as_of: "", refusals: [], totals_errors: [], totals: null, can_execute: true, confirm_label: "Place 2 PAPER orders",
    legs: legIssues.map((x, i) => ({ ...preview(), seq: i + 1, leg: i, role: i === 0 ? "hedge" : "short", ...x })), ...over,
  } as BasketPreview;
}

/** The surfaces in rendered markup, by the selectors the e2e baseline counts. */
function domCount(html: string): { cards: number; lines: number } {
  const listLines = (attr: string) => {
    const m = html.match(new RegExp(`${attr}[^>]*>(.*?)</ul>`));
    return m ? (m[1].match(/<li/g) ?? []).length : 0;
  };
  const warn = (html.match(/data-risk-part="warn"/g) ?? []).length;
  const refusals = listLines('data-ticket="refusals"') + listLines('data-basket="confirm-refusals"');
  const warnings = listLines('data-ticket="warnings"');
  const legLists = [...html.matchAll(/data-confirm-leg="\d+"[^>]*>(.*?)<\/li><\/ol>|data-confirm-leg="\d+"[^>]*>(.*?)(?=<li[^>]*data-confirm-leg|<\/ol>)/g)]
    .map((m) => (m[1] ?? m[2] ?? "")).map((leg) => { const u = leg.match(/<ul[^>]*>(.*?)<\/ul>/); return u ? (u[1].match(/<li/g) ?? []).length : 0; })
    .filter((n) => n > 0);
  const blocked = (html.match(/data-basket="blocked-by"/g) ?? []).length;
  return { cards: (warn ? 1 : 0) + (refusals ? 1 : 0) + (warnings ? 1 : 0) + legLists.length + blocked,
    lines: warn + refusals + warnings + legLists.reduce((s, n) => s + n, 0) + blocked };
}

describe("M76 MP-0: each of today's warning surfaces is counted", () => {
  it("nothing to warn about: no card, no line", () => {
    expect(ticketSurfaces(preview(), [INFO, null])).toEqual({ cards: 0, lines: 0 });
  });

  it("a warn line of 'Before you place' (events, liquidity, margin, risk): one card, one line each; info lines do not count", () => {
    expect(ticketSurfaces(preview(), [WARN, INFO, { ...WARN, text: "Liquidity: wide." }])).toEqual({ cards: 1, lines: 2 });
  });

  it("the server's warning texts (expiry day, chain price, F&O ban, risk notes): one card, one line each", () => {
    expect(ticketSurfaces(preview({ warnings: ["This contract expires today.", "In the F&O ban list today."] }), [])).toEqual({ cards: 1, lines: 2 });
  });

  it("a refusal (his own limit) is a list plus the 'cannot place' line above the buttons: two cards", () => {
    const p = preview({ refusals: ["Your risk per trade allows 4 lot(s)."], can_place: false });
    expect(ticketSurfaces(p, [])).toEqual({ cards: 2, lines: 2 });
  });

  it("an exchange error (tick size, expiry passed) counts the same way", () => {
    expect(ticketSurfaces(preview({ errors: ["Price must be a multiple of 0.05."], can_place: false }), [])).toEqual({ cards: 2, lines: 2 });
  });

  it("a Risk limits block (M64) is one card", () => {
    const p = preview({ can_place: false, refusals: ["Max lots per order 2."], risk: { blocks: [{}] } as OrderPreview["risk"] });
    expect(ticketSurfaces(p, [])).toEqual({ cards: 2, lines: 2 });
  });

  it("all together, worst case today: four separate surfaces", () => {
    const p = preview({ warnings: ["This contract expires today."], refusals: ["Your risk per trade allows 4 lot(s)."], can_place: false });
    expect(ticketSurfaces(p, [WARN])).toEqual({ cards: 4, lines: 4 });
  });

  it("the basket: each leg's issue list is its own card, then the basket's refusals and the 'cannot place' line", () => {
    expect(basketSurfaces(basket(), [INFO])).toEqual({ cards: 0, lines: 0 });
    const p = basket({ can_execute: false, refusals: ["You already hold 3 positions, your limit."] },
      [{ errors: ["Price must be a multiple of 0.05."], refusals: [] }, { errors: [], refusals: ["Stop required on a sold option."] }]);
    expect(basketSurfaces(p, [WARN])).toEqual({ cards: 5, lines: 5 });
  });
});

describe("M76 MP-0: the count matches what the confirm step renders", () => {
  it("the single-order Review", () => {
    for (const p of [preview(), preview({ warnings: ["This contract expires today.", "No live price just now."] }),
      preview({ refusals: ["Your risk per trade allows 4 lot(s)."], can_place: false })]) {
      const lines = reviewRiskLines(p, null, null, WARN);
      const html = renderToStaticMarkup(<Review preview={p} liquidity={WARN} />);
      const fromDom = domCount(html);
      const counted = ticketSurfaces(p, lines);
      const blocked = p.can_place ? 0 : 1;   // the "cannot place" line sits in the action bar, outside `Review`
      expect({ cards: fromDom.cards + blocked, lines: fromDom.lines + blocked }).toEqual(counted);
    }
  });

  it("the basket confirm step", () => {
    const p = basket({}, [{ errors: [], refusals: ["Stop required on a sold option."] }, { errors: [], refusals: [] }]);
    const checks = [WARN, INFO];
    const html = renderToStaticMarkup(<ConfirmView preview={p} busy={false} onBack={() => undefined} onPlace={() => undefined} checks={checks} />);
    expect(domCount(html)).toEqual(basketSurfaces(p, confirmRiskLines(p, null, checks)));
  });
});

describe("M76 MP-0: one report per ticket, never in Place's way", () => {
  function clock() {
    let t = 0;
    const sent: TicketReport[] = [];
    const c = new TicketClock("ticket", (b) => { sent.push(b); }, () => t);
    return { c, sent, at: (v: number) => { t = v; } };
  }

  it("accept and continue: Place with warnings shown sends the counts on screen and the times", () => {
    const { c, sent, at } = clock();
    at(1000); c.opened();
    at(1500); c.reviewStarted();
    at(1740); c.reviewed();
    c.shown({ cards: 2, lines: 3 });
    at(2300); c.placed({ cards: 2, lines: 3 });
    expect(sent).toEqual([{ surface: "ticket", device: "desktop", outcome: "placed", cards_shown: 2, lines_total: 3, lines_visible: 3,
      ms_open_to_place: 1300, ms_review_to_place: 560, checks_ms: 240, pending_at_place: 0 }]);
    c.placed(); c.closed();
    expect(sent).toHaveLength(1);                                         // one report per ticket
  });

  it("the check time is the Review preview; inside or over the 300 ms budget is readable from it", () => {
    const { c, sent, at } = clock();
    at(0); c.opened();
    at(10); c.reviewStarted();
    at(320); c.reviewed();
    c.shown({ cards: 0, lines: 0 });
    at(400); c.placed();
    expect(sent[0].checks_ms).toBe(310);                                  // over 300 ms: the series shows it
    expect(sent[0].pending_at_place).toBe(0);                             // today Place waits for the preview: nothing pending
  });

  it("closed after a Review without Place: one report with the last counts and no times; never reviewed: nothing", () => {
    const { c, sent, at } = clock();
    at(0); c.opened(); c.closed();
    expect(sent).toEqual([]);
    c.opened(); c.reviewStarted(); at(200); c.reviewed();
    c.shown({ cards: 2, lines: 2 });
    c.shown(null);                                                        // Back to the fields: what he saw is kept
    c.closed();
    expect(sent).toMatchObject([{ outcome: "closed", cards_shown: 2, lines_total: 2, ms_open_to_place: null, ms_review_to_place: null }]);
  });

  it("a failing report changes nothing (best effort)", () => {
    const c = new TicketClock("basket", () => { throw new Error("network"); });
    c.opened(); c.reviewed(); c.shown({ cards: 1, lines: 1 });
    expect(() => c.placed()).not.toThrow();
  });

  it("Place never awaits the report and stays read-only: the ticket and the basket call it before their own order call", () => {
    for (const [src, fn, call] of [[ticketSource, "async function place()", "await api.place"], [basketSource, "async function execute()", "await api.execute"]] as const) {
      const body = src.slice(src.indexOf(fn), src.indexOf(call, src.indexOf(fn)));
      expect(body).toContain("metrics.current?.placed(shown)");
      expect(body).not.toMatch(/await[^;]*metrics/);
    }
    expect(metricsSource).not.toMatch(/await /);                          // the sender is fire and forget
    expect(metricsSource).toContain('"/ui/ticket-metrics"');
    // nothing that identifies a person, an instrument or an order goes into the report
    expect(metricsSource).not.toMatch(/tradingsymbol|instrument_key|user_id|email|client_ref/);
  });

  it("no UI change: the confirm steps render the same lines through the shared helpers", () => {
    expect(ticketSource).toContain('<RiskChecksV2 testId="ticket" lines={reviewRiskLines(p, budget, events, liquidity)} />');
    expect(basketSource).toContain('<RiskChecksV2 testId="basket-confirm" lines={confirmRiskLines(p, budget, checks)} />');
  });
});
