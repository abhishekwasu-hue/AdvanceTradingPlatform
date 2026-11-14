import { ORDER_TYPE_KEY, PRODUCT_KEY, positionSizeOf, type TradeSettingsView } from "../settings/tradeSettings";
import type { OrderFacts } from "./brokerLinks";

/**
 * Route 3 (BIZ-1 B2 / B3): the order facts "Trade in your broker app" shows or pre-fills - built ONLY from the user's own
 * entered order (ticket, basket, Reverse) or his own saved Trade settings (signals, scans, paper-bot signals). Never an
 * engine value: no stop-loss, target or price from a signal, no lots or quantity he did not set. Pure functions.
 */

const num = (text: string | null | undefined): number | null => {
  const v = Number((text ?? "").trim());
  return (text ?? "").trim() !== "" && Number.isFinite(v) && v > 0 ? v : null;
};

/** B2 - the single-leg ticket: what he entered (side, lots, order type, product, his own price / trigger). */
export function ticketFacts(target: { underlying: string; label: string | null; lotSize: number | null },
                            draft: { side: "BUY" | "SELL"; lots: number | null; orderType: string | null; product: string | null; price: string; trigger: string }): OrderFacts {
  const lots = draft.lots && draft.lots > 0 ? draft.lots : null;
  const priced = draft.orderType === "LIMIT" || draft.orderType === "SL";
  const triggered = draft.orderType === "SL" || draft.orderType === "SL-M";
  return { instrument: target.label ?? target.underlying, side: draft.side, lots, quantity: lots && target.lotSize ? lots * target.lotSize : null,
           orderType: draft.orderType, product: draft.product, price: priced ? num(draft.price) : null, trigger: triggered ? num(draft.trigger) : null };
}

/** B2 - the basket: one line per leg in his own words (side, lots, contract), Market orders as the basket places them. */
export function basketFacts(legs: readonly { side: "BUY" | "SELL"; lots: number | null; name: string }[], product: string | null): OrderFacts {
  return { instrument: legs.length ? legs[0].name.split(" ")[0] : "", side: null, quantity: null, orderType: "MARKET", product, price: null,
           legs: legs.map((l) => `${l.side === "BUY" ? "Buy" : "Sell"} ${l.lots ?? "?"} lot${l.lots === 1 ? "" : "s"} ${l.name}`) };
}

/** B2 - Reverse: the NEW position only - the other side, his held quantity (the button shows only while LIVE is not open
 * for him, so the position being reversed is his PAPER one and has nothing to close at the broker). */
export function reverseFacts(position: { symbol: string; exchange?: string | null; direction: string; quantity: number; instrument_kind?: string | null },
                             product: string | null): OrderFacts {
  const equity = (position.instrument_kind ?? "UNDERLYING") === "UNDERLYING";
  return { instrument: position.symbol, exchange: equity ? position.exchange ?? "NSE" : null, side: position.direction === "LONG" ? "SELL" : "BUY",
           quantity: position.quantity > 0 && Number.isInteger(position.quantity) ? position.quantity : null,
           orderType: "MARKET", product, price: null };
}

/** His own saved order defaults for an entry that has no ticket (signals, scans, bots): size, order type, product. */
export function ownEntrySettings(view: TradeSettingsView | null | undefined): { quantity: number | null; lots: number | null; orderType: string | null; product: string | null } {
  const v = (key: string) => view?.sections.flatMap((s) => s.fields).find((f) => f.key === key && f.saved)?.value;
  const size = positionSizeOf(view);
  const text = (x: unknown) => (typeof x === "string" && x ? x : null);
  return {
    quantity: size.mode === "FIXED_QUANTITY" && size.quantity !== undefined ? size.quantity : null,
    lots: (size.mode === undefined || size.mode === "FIXED_LOTS") && size.lots !== undefined ? size.lots : null,
    orderType: text(v(ORDER_TYPE_KEY)),
    product: text(v(PRODUCT_KEY)),
  };
}

/**
 * B3 - a signal / scan match / paper-bot signal: the symbol and the side of HIS signal's own direction; size, order type
 * and product only from his saved settings (a stock quantity only from "Fixed quantity"; an index has no cash order,
 * so no quantity). No price: a signal carries no order price of his. `legs`: an option / structure proposal's own legs.
 */
export function entryFacts(signal: { symbol: string; exchange?: string | null; direction: "LONG" | "SHORT" | null; legs?: readonly string[]; index?: boolean },
                           own: ReturnType<typeof ownEntrySettings>): OrderFacts {
  const legs = signal.legs && signal.legs.length ? signal.legs : undefined;
  return {
    instrument: signal.symbol, exchange: legs || signal.index ? null : (signal.exchange || "NSE"),
    side: legs ? null : signal.direction === "LONG" ? "BUY" : signal.direction === "SHORT" ? "SELL" : null,
    quantity: legs || signal.index ? null : own.quantity, orderType: own.orderType, product: own.product, price: null, legs,
  };
}
