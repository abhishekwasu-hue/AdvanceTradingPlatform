/**
 * FNO-1: the order ticket's pure parts (tested in ticket.test.tsx) - the draft, lots to units, which price fields an order
 * type needs, the keyboard shortcuts, the request body, and the words of a result. The server checks and computes every
 * figure the confirm step shows (backend app/paper/fno_ticket.py); nothing here makes one up.
 */
import { formatINR, formatNumber, formatPrice } from "../ui/format";
import type { ContractRight, HeldLeg, OrderAsk, OrderResult, OrderType, Product, Right, Side } from "./api";

/** What the ticket is for: one contract of the chain on screen, with the chain's own prices (the price helper).
 * FNO-3: or a future of the futures chain (`right` "FUT", `strike` null). */
export interface TicketTarget {
  underlying: string;
  expiry: string;
  strike: number | null;
  right: ContractRight;
  /** "NIFTY 28 OCT 22800 CE" (a future "NIFTY 28 OCT FUT") once the contract is read; null before. */
  label: string | null;
  lotSize: number | null;
  ltp: number | null;
  bid: number | null;
  ask: number | null;
}

export interface Draft {
  side: Side;
  lots: number;
  orderType: OrderType;
  product: Product;
  price: string;
  trigger: string;
  stopLoss: string;
}

export const ORDER_TYPES: { value: OrderType; label: string }[] = [
  { value: "MARKET", label: "Market" }, { value: "LIMIT", label: "Limit" }, { value: "SL", label: "SL" }, { value: "SL-M", label: "SL-M" },
];
export const PRODUCTS: { value: Product; label: string; hint: string }[] = [
  { value: "MIS", label: "MIS", hint: "Intraday" }, { value: "NRML", label: "NRML", hint: "Carry forward" },
];
export const PRACTICE_TITLE = "Practice mode (PAPER)";
export const PRACTICE_TEXT = "No real order is sent and no money moves.";
export const MAX_LOTS = 500;

/** Settings part C: the lots / product / order type the user's own defaults start a ticket with (only the ones set). */
export interface DraftDefaults { lots?: number; product?: Product; orderType?: OrderType }

/** A fresh ticket. With the user's defaults (src/settings/tradingDefaults.ts ticketPrefill) those values start it - the side
 * never comes from a default - and nothing else changes: no defaults gives today's draft exactly. */
export function newDraft(side: Side, defaults?: DraftDefaults): Draft {
  const draft: Draft = { side, lots: 1, orderType: "MARKET", product: "MIS", price: "", trigger: "", stopLoss: "" };
  if (!defaults) return draft;
  if (defaults.lots !== undefined) draft.lots = clampLots(defaults.lots);
  if (defaults.orderType !== undefined) draft.orderType = defaults.orderType;
  if (defaults.product !== undefined) draft.product = defaults.product;
  return draft;
}

/** Which ticket fields still show the value they took from the defaults (a changed field loses its mark). */
export function draftFromDefaults(d: Draft, defaults?: DraftDefaults): { lots: boolean; orderType: boolean; product: boolean } {
  return {
    lots: defaults?.lots !== undefined && clampLots(defaults.lots) === d.lots,
    orderType: defaults?.orderType !== undefined && defaults.orderType === d.orderType,
    product: defaults?.product !== undefined && defaults.product === d.product,
  };
}

/** Units = lots x the instrument master's lot size; null while the lot size is not known. */
export function unitsOf(lots: number, lotSize: number | null | undefined): number | null {
  return typeof lotSize === "number" && lotSize > 0 && Number.isFinite(lots) ? lots * lotSize : null;
}

export function unitsText(lots: number, lotSize: number | null | undefined): string {
  const units = unitsOf(lots, lotSize);
  if (units === null) return `${lots} lot${lots === 1 ? "" : "s"}`;
  return `${lots} lot${lots === 1 ? "" : "s"} = ${formatNumber(units, 0)} qty (lot size ${formatNumber(lotSize ?? 0, 0)})`;
}

export function clampLots(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(MAX_LOTS, Math.max(1, Math.round(value)));
}

/** The price fields an order type needs: Limit a limit price, SL both, SL-M the trigger only, Market none. */
export function needs(orderType: OrderType): { price: boolean; trigger: boolean } {
  return { price: orderType === "LIMIT" || orderType === "SL", trigger: orderType === "SL" || orderType === "SL-M" };
}

const num = (text: string): number | null => {
  const v = Number(text.replace(/,/g, "").trim());
  return text.trim() !== "" && Number.isFinite(v) && v > 0 ? v : null;
};

/** The stop-loss field's own words: needed for a sold option and for a future (either side), optional otherwise. */
export function stopHint(side: Side, right: ContractRight = "CE"): string {
  if (right === "FUT") return "(needed for a future)";
  return side === "SELL" ? "(needed for a sold option)" : "(optional)";
}

/** What is missing before the ticket can be reviewed (the server checks everything else). */
export function draftProblems(d: Draft, right: ContractRight = "CE"): string[] {
  const out: string[] = [];
  const n = needs(d.orderType);
  if (n.price && num(d.price) === null) out.push("Enter the limit price");
  if (n.trigger && num(d.trigger) === null) out.push("Enter the trigger price");
  if (right === "FUT" && num(d.stopLoss) === null) out.push("A future needs a stop-loss");
  else if (d.side === "SELL" && num(d.stopLoss) === null) out.push("A sold option needs a stop-loss");
  if (d.stopLoss.trim() !== "" && num(d.stopLoss) === null) out.push("The stop-loss is not a price");
  return out;
}

export function askOf(target: TicketTarget, d: Draft, clientRef: string): OrderAsk {
  const n = needs(d.orderType);
  return {
    underlying: target.underlying, expiry: target.expiry, strike: target.right === "FUT" ? null : target.strike, right: target.right, side: d.side,
    lots: clampLots(d.lots), order_type: d.orderType, product: d.product,
    price: n.price ? num(d.price) : null, trigger_price: n.trigger ? num(d.trigger) : null, stop_loss: num(d.stopLoss),
    chain_ltp: target.ltp && target.ltp > 0 ? target.ltp : null, client_ref: clientRef,
  };
}

/** Desktop shortcuts: B = Buy, S = Sell - never while typing in a field, never with a modifier key. */
export function shortcutSide(e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey">, target: EventTarget | null): Side | null {
  if (e.ctrlKey || e.metaKey || e.altKey) return null;
  const el = target as { tagName?: string; isContentEditable?: boolean } | null;
  const tag = (el?.tagName ?? "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return null;
  const k = e.key.toLowerCase();
  return k === "b" ? "BUY" : k === "s" ? "SELL" : null;
}

export function sideWord(side: Side, past = false): string {
  return side === "BUY" ? (past ? "Bought" : "Buy") : (past ? "Sold" : "Sell");
}

export function resultText(r: OrderResult): string {
  if (r.status === "PLACED" && r.filled) {
    const lots = r.filled.lots;
    return `Placed in practice mode: ${sideWord(r.side, true)} ${formatNumber(lots, lots % 1 ? 2 : 0)} lot${lots === 1 ? "" : "s"} `
      + `(${formatNumber(r.filled.quantity, 0)} qty) of ${r.label} at ${formatPrice(r.filled.price)}.`;
  }
  return r.reasons.length ? `Not placed: ${r.reasons[0]}` : "Not placed.";
}

export function moneyText(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? formatINR(v) : "–";
}

/** A held leg in one line: "Bought 1 lot at 100.00". */
export function heldText(h: HeldLeg): string {
  return `${sideWord(h.side, true)} ${formatNumber(h.lots, h.lots % 1 ? 2 : 0)} lot${h.lots === 1 ? "" : "s"} at ${formatPrice(h.entry_price)}`;
}

/** A held leg's P&L at the chain's live price (the server's last mark when the chain has none); null when unknown. */
export function heldPnl(h: HeldLeg, ltp: number | null | undefined): number | null {
  if (typeof ltp === "number" && Number.isFinite(ltp) && ltp > 0) {
    return Math.round((h.side === "BUY" ? 1 : -1) * (ltp - h.entry_price) * h.quantity * 100) / 100;
  }
  return h.pnl;
}

export function heldKey(strike: number, right: Right): string {
  return `${strike}|${right}`;
}

export function newClientRef(): string {
  try {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  } catch { /* no crypto: fall back */ }
  return `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}
