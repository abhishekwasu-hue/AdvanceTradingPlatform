import type { AiAction, BrokerTicket } from "../types";
import { formatPrice } from "../ui/format";

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

const KITE_FNO_EXCHANGES = new Set(["NFO", "BFO"]);

/** FNO-2: an F&O basket for Kite, from the server's legs (Kite's own contract symbols from the instrument master,
 * hedge legs first). Null when any leg is not a plain whole-quantity order on NFO / BFO or there is no publisher key. */
export function kiteFnoBasketLink(orders: { tradingsymbol: string; exchange: string; transaction_type: "BUY" | "SELL"; quantity: number }[],
                                  apiKey: string): BrokerAppLink | null {
  const key = (apiKey || "").trim();
  if (!key || !orders.length) return null;
  const basket: KiteBasketOrder[] = [];
  for (const o of orders) {
    const exchange = (o.exchange || "").toUpperCase();
    const symbol = (o.tradingsymbol || "").toUpperCase();
    if (!KITE_FNO_EXCHANGES.has(exchange) || !KITE_SYMBOL.test(symbol) || !Number.isInteger(o.quantity) || o.quantity <= 0) return null;
    if (o.transaction_type !== "BUY" && o.transaction_type !== "SELL") return null;
    basket.push({ variety: "regular", tradingsymbol: symbol, exchange, transaction_type: o.transaction_type, order_type: "MARKET", quantity: o.quantity, readonly: false });
  }
  return { broker: "zerodha", brokerLabel: "Kite", method: "POST", action: KITE_BASKET_URL, fields: { api_key: key, data: JSON.stringify(basket) },
           summary: basket.map((o) => `${o.transaction_type} ${o.quantity} ${o.tradingsymbol}`).join(", ") };
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

// --- Route 3 (BIZ-1 B4): the broker table, and the order facts the user copies -------------------------------------

/**
 * One row per broker. `home` is the broker's own official address (its company domain only - never a guessed order
 * URL). `prefill` is "verified" only where a documented pre-filled order format is implemented above (Kite Publisher);
 * every other broker opens its own home and ATP shows the order facts to copy (BIZ1-Q3 default). The same table, with
 * the sources, is in docs/BROKER_DATA_TERMS.md "Trade in your broker app: brokers".
 */
export interface BrokerRow { id: string; label: string; home: string; prefill: "verified" | "not verified" }
export const BROKER_TABLE: readonly BrokerRow[] = [
  { id: "zerodha", label: "Kite", home: "https://kite.zerodha.com", prefill: "verified" },
  { id: "angelone", label: "Angel One", home: "https://www.angelone.in", prefill: "not verified" },
  { id: "upstox", label: "Upstox", home: "https://upstox.com", prefill: "not verified" },
  { id: "fyers", label: "Fyers", home: "https://fyers.in", prefill: "not verified" },
  { id: "dhan", label: "Dhan", home: "https://dhan.co", prefill: "not verified" },
  { id: "groww", label: "Groww", home: "https://groww.in", prefill: "not verified" },
  { id: "kotak", label: "Kotak Neo", home: "https://www.kotaksecurities.com", prefill: "not verified" },
];
const BROKER_ALIASES: Record<string, string> = { angel: "angelone", angel_one: "angelone", kotak_neo: "kotak", kotakneo: "kotak" };

/** The table row of a broker name as ATP stores it ("zerodha", "angelone", "angel_one", ...), or null. */
export function brokerRow(name: string | null | undefined): BrokerRow | null {
  const key = (name || "").trim().toLowerCase();
  const id = BROKER_ALIASES[key] ?? key;
  return BROKER_TABLE.find((b) => b.id === id) ?? null;
}

/** The user's own order, as the sheet shows it to copy - only what he entered or saved; null = not set (his choice). */
export interface OrderFacts {
  /** "RELIANCE", "NIFTY 28 OCT 22800 CE", or a basket's legs in `legs`. */
  instrument: string;
  exchange?: string | null;
  side: "BUY" | "SELL" | null;
  quantity: number | null;
  lots?: number | null;
  orderType: string | null;
  product: string | null;
  price: number | null;
  trigger?: number | null;
  /** A basket / structure: one line per leg in order words ("Buy 1 lot NIFTY 22800 CE"). */
  legs?: readonly string[];
}

export interface FactRow { name: string; value: string | null }

const TYPE_WORDS: Record<string, string> = { MARKET: "Market", LIMIT: "Limit", SL: "Stop-loss limit", "SL-M": "Stop-loss market" };

/** The rows of the copy sheet, in the broker's own order-form order. `value` null = not set by him (his choice there). */
export function factRows(f: OrderFacts): FactRow[] {
  const rows: FactRow[] = [{ name: "Symbol", value: f.instrument + (f.exchange ? ` (${f.exchange.toUpperCase()})` : "") }];
  if (f.legs && f.legs.length) {
    f.legs.forEach((l, i) => rows.push({ name: `Leg ${i + 1}`, value: l }));
  } else {
    rows.push({ name: "Side", value: f.side === "BUY" ? "Buy" : f.side === "SELL" ? "Sell" : null });
    rows.push({ name: "Quantity", value: f.quantity && f.quantity > 0
      ? `${f.quantity}${f.lots ? ` (${f.lots} lot${f.lots === 1 ? "" : "s"})` : ""}` : null });
  }
  rows.push({ name: "Order type", value: f.orderType ? TYPE_WORDS[f.orderType] ?? f.orderType : null });
  rows.push({ name: "Product", value: f.product });
  if (f.price !== null && f.price > 0) rows.push({ name: "Price", value: formatPrice(f.price) });
  if (f.trigger !== null && f.trigger !== undefined && f.trigger > 0) rows.push({ name: "Trigger price", value: formatPrice(f.trigger) });
  return rows;
}

/** All set rows as one text block for "Copy all". */
export function factsText(f: OrderFacts): string {
  return factRows(f).filter((r) => r.value !== null).map((r) => `${r.name}: ${(r.value as string).replace(/,/g, "")}`).join("\n");
}

/**
 * B3: the Kite link for a plain NSE / BSE equity entry built from the user's own order - only when it is a Market order
 * of a whole quantity he set (the verified Kite format carries no price / product); otherwise null (the copy sheet).
 */
export function kiteEntryLink(f: OrderFacts, apiKey: string | null | undefined): BrokerAppLink | null {
  const key = (apiKey || "").trim();
  if (!key || f.legs?.length || f.orderType !== "MARKET" || !f.side || f.quantity === null || !f.exchange) return null;   // no exchange: not cash
  const basket = kiteBasket({ broker: "zerodha", exchange: f.exchange, symbol: f.instrument, side: f.side, quantity: f.quantity,
                              instrument_kind: "UNDERLYING", publisher_api_key: key });
  if (!basket) return null;
  const o = basket[0];
  return { broker: "zerodha", brokerLabel: "Kite", method: "POST", action: KITE_BASKET_URL,
           fields: { api_key: key, data: JSON.stringify(basket) }, summary: `${o.transaction_type} ${o.quantity} ${o.tradingsymbol} (${o.exchange})` };
}
