import { describe, expect, it } from "vitest";
import type { AiAction, BrokerTicket } from "../types";
import { KITE_BASKET_URL, brokerAppLink, kiteBasket, openBrokerAppLink } from "./brokerLinks";

const TICKET: BrokerTicket = { broker: "zerodha", exchange: "NSE", symbol: "RELIANCE", side: "SELL", quantity: 10, instrument_kind: "UNDERLYING",
                               publisher_api_key: "publisher-key" };

function proposal(over: Partial<AiAction> = {}): Pick<AiAction, "action" | "status" | "ticket"> {
  return { action: "EXIT_POSITION", status: "PROPOSED", ticket: TICKET, ...over };
}

describe("brokerAppLink", () => {
  it("builds the Kite Publisher basket form for an exit proposal", () => {
    const link = brokerAppLink(proposal());
    expect(link).not.toBeNull();
    expect(link!.method).toBe("POST");
    expect(link!.action).toBe(KITE_BASKET_URL);
    expect(KITE_BASKET_URL).toBe("https://kite.zerodha.com/connect/basket");
    expect(Object.keys(link!.fields).sort()).toEqual(["api_key", "data"]);
    expect(link!.fields.api_key).toBe("publisher-key");
    expect(JSON.parse(link!.fields.data)).toEqual([
      { variety: "regular", tradingsymbol: "RELIANCE", exchange: "NSE", transaction_type: "SELL", order_type: "MARKET", quantity: 10, readonly: false },
    ]);
    expect(link!.summary).toBe("SELL 10 RELIANCE (NSE)");
  });

  it("is null for proposals without a tradable instrument", () => {
    for (const action of ["PAUSE_DEPLOYMENT", "REVIEW_STRATEGY", "REDUCE_RISK"] as const) expect(brokerAppLink(proposal({ action }))).toBeNull();
  });

  it("is null without the flag (no ticket) and without a publisher key", () => {
    expect(brokerAppLink(proposal({ ticket: null }))).toBeNull();
    expect(brokerAppLink(proposal({ ticket: undefined }))).toBeNull();
    expect(brokerAppLink(proposal({ ticket: { ...TICKET, publisher_api_key: null } }))).toBeNull();
    expect(brokerAppLink(proposal({ ticket: { ...TICKET, publisher_api_key: "  " } }))).toBeNull();
  });

  it("is null once decided", () => {
    for (const status of ["APPROVED", "EXECUTED", "REJECTED", "EXPIRED", "FAILED"] as const) expect(brokerAppLink(proposal({ status }))).toBeNull();
  });

  it("returns null for every broker whose link format is not verified", () => {
    for (const broker of ["upstox", "dhan", "angel_one", "fyers", "groww", "kotak_neo", "icici_breeze", "5paisa", "shoonya", "alice_blue", null]) {
      expect(brokerAppLink(proposal({ ticket: { ...TICKET, broker, publisher_api_key: "k" } }))).toBeNull();
    }
  });

  it("only pre-fills plain NSE/BSE equity with a whole quantity", () => {
    expect(kiteBasket({ ...TICKET, exchange: "bse", symbol: "m&m", side: "BUY" })).toEqual([
      { variety: "regular", tradingsymbol: "M&M", exchange: "BSE", transaction_type: "BUY", order_type: "MARKET", quantity: 10, readonly: false }]);
    expect(kiteBasket({ ...TICKET, exchange: "NFO" })).toBeNull();
    expect(kiteBasket({ ...TICKET, instrument_kind: "OPTION" })).toBeNull();
    expect(kiteBasket({ ...TICKET, symbol: "NIFTY 50" })).toBeNull();
    expect(kiteBasket({ ...TICKET, quantity: 1.5 })).toBeNull();
    expect(kiteBasket({ ...TICKET, quantity: 0 })).toBeNull();
  });
});

describe("openBrokerAppLink", () => {
  it("submits a hidden form to the broker's page in a new tab and removes it", () => {
    const link = brokerAppLink(proposal())!;
    // A tiny stand-in for the document (the unit tests run without a DOM).
    type Node = Record<string, unknown> & { children: Node[]; style: Record<string, string> };
    const body: Node[] = [];
    const submitted: { action: unknown; method: unknown; target: unknown; rel: unknown; fields: Record<string, unknown> }[] = [];
    const doc = {
      body: { appendChild: (n: Node) => body.push(n) },
      createElement: (tag: string) => {
        const node: Node = { tag, children: [], style: {} };
        node.appendChild = (c: Node) => node.children.push(c);
        node.remove = () => body.splice(body.indexOf(node), 1);
        node.submit = () => submitted.push({ action: node.action, method: node.method, target: node.target, rel: node.rel,
                                             fields: Object.fromEntries(node.children.map((c) => [c.name, c.value])) });
        return node;
      },
    } as unknown as Document;
    openBrokerAppLink(link, doc);
    expect(submitted).toEqual([{ action: KITE_BASKET_URL, method: "post", target: "_blank", rel: "noopener noreferrer", fields: link.fields }]);
    expect(body).toEqual([]);
  });
});
