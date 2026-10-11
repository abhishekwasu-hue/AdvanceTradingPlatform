import type { AiAction, BrokerTicket } from "../types";

/**
 * PW5 "Trade in your broker app": turns a proposal's closing-order facts (the server's `ticket`, sent only with the
 * `broker_app_links` flag on) into a link that opens a pre-filled order in the user's own broker app. The user reviews
 * and confirms it there; ATP sends nothing to any broker API (Phase 0: no order from ATP).
 *
 * Only brokers whose link format is documented in docs/BROKER_DATA_TERMS.md are implemented - today Zerodha's Kite
 * Publisher basket (an HTML form POST of `api_key` + a JSON `data` array to kite.zerodha.com/connect/basket), which
 * needs the platform's publisher api_key. Every other broker returns null ("not verified"); a URL is never guessed.
 * Pure functions, except `openBrokerAppLink`, which submits the form in a new tab.
 */
export const KITE_BASKET_URL = "https://kite.zerodha.com/connect/basket";

/** Phase 0 scope: cash equity on NSE/BSE, where ATP's symbol is the exchange's trading symbol. Derivatives need the
 * broker's own contract symbol, which ATP's instrument master does not carry for Kite - no link rather than a wrong one. */
const KITE_EXCHANGES = new Set(["NSE", "BSE"]);
const KITE_SYMBOL = /^[A-Z0-9&-]{1,40}$/;

export interface KiteBasketOrder {
  variety: "regular";
  tradingsymbol: string;
  exchange: string;
  transaction_type: "BUY" | "SELL";
  order_type: "MARKET";
  quantity: number;
  /** false: the user can still change the order in Kite before placing it. */
  readonly: false;
}

export interface BrokerAppLink {
  broker: "zerodha";
  brokerLabel: string;
  method: "POST";
  action: string;
  fields: { api_key: string; data: string };
  /** e.g. "SELL 10 RELIANCE (NSE)" - shown next to the button so the user knows what will be pre-filled. */
  summary: string;
}

/** The Kite Publisher basket for one ticket, or null when it is not a plain NSE/BSE equity order. */
export function kiteBasket(ticket: BrokerTicket): KiteBasketOrder[] | null {
  const exchange = (ticket.exchange || "").toUpperCase();
  const symbol = (ticket.symbol || "").toUpperCase();
  if (ticket.instrument_kind !== "UNDERLYING" || !KITE_EXCHANGES.has(exchange) || !KITE_SYMBOL.test(symbol)) return null;
  if (!Number.isInteger(ticket.quantity) || ticket.quantity <= 0 || (ticket.side !== "BUY" && ticket.side !== "SELL")) return null;
  return [{ variety: "regular", tradingsymbol: symbol, exchange, transaction_type: ticket.side, order_type: "MARKET", quantity: ticket.quantity, readonly: false }];
}

/** The link for a proposal, or null: not an open exit proposal, flag off (no ticket), broker not verified, no key. */
export function brokerAppLink(a: Pick<AiAction, "action" | "status" | "ticket">): BrokerAppLink | null {
  if (a.status !== "PROPOSED" || a.action !== "EXIT_POSITION") return null;     // PAUSE / REDUCE / REVIEW: no instrument
  const ticket = a.ticket;
  if (!ticket) return null;                                                       // flag off, or nothing open to trade
  switch ((ticket.broker || "").toLowerCase()) {
    case "zerodha": {
      const key = (ticket.publisher_api_key || "").trim();
      const basket = key ? kiteBasket(ticket) : null;
      if (!basket) return null;
      const o = basket[0];
      return { broker: "zerodha", brokerLabel: "Kite", method: "POST", action: KITE_BASKET_URL,
               fields: { api_key: key, data: JSON.stringify(basket) }, summary: `${o.transaction_type} ${o.quantity} ${o.tradingsymbol} (${o.exchange})` };
    }
    default:
      return null;                                                                // not verified - see BROKER_DATA_TERMS.md
  }
}

/** Opens the broker's own page in a new tab by submitting the form there. Nothing else is sent anywhere. */
export function openBrokerAppLink(link: BrokerAppLink, doc: Document = document): void {
  const form = doc.createElement("form");
  form.method = link.method.toLowerCase();
  form.action = link.action;
  form.target = "_blank";
  form.rel = "noopener noreferrer";
  form.style.display = "none";
  for (const [name, value] of Object.entries(link.fields)) {
    const input = doc.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    form.appendChild(input);
  }
  doc.body.appendChild(form);
  try { form.submit(); } finally { form.remove(); }
}
