/**
 * FNO-1: the order ticket's pure parts (tested in ticket.test.tsx) - the draft, lots to units, which price fields an order
 * type needs, the keyboard shortcuts, the request body, and the words of a result. The server checks and computes every
 * figure the confirm step shows (backend app/paper/fno_ticket.py); nothing here makes one up.
 */
import { PAPER_TEXT, PAPER_WORD } from "../ui/paperWords";
import { formatINR, formatNumber, formatPrice } from "../ui/format";
import type { GlossaryKey } from "../ui/glossary";
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
  orderType: OrderType | null;  // M68 PR-1: null until the user picks one or saved his own (never a platform default)
  product: Product | null;      // null until the user picks one (owner F1 / TT-12: never a platform default)
  price: string;
  trigger: string;
  stopLoss: string;
}

/** M63 I-7: `term` names the glossary entry behind the (?) the ticket shows for a trading term (no effect on the order). */
export const ORDER_TYPES: { value: OrderType; label: string; term?: GlossaryKey }[] = [
  { value: "MARKET", label: "Market" }, { value: "LIMIT", label: "Limit" }, { value: "SL", label: "SL", term: "sl" }, { value: "SL-M", label: "SL-M", term: "sl-m" },
];
/** Owner F1 (27 Oct, TT-12): with no product saved in My trading defaults nothing is pre-selected - the user picks MIS
 *  or NRML before Review / Execute (disabled until then, with this one line why). */
export const PRODUCT_UNSET_TEXT = "Pick MIS or NRML to continue (none saved in your Trade settings).";
export const PRODUCT_MISSING = "Pick a product: MIS or NRML";
/** M68 PR-1 (TS1-003): no order type is pre-selected either - the ticket asks for it, with "Save as my default". */
export const ORDER_TYPE_UNSET_TEXT = "Pick how the order is sent: Market, Limit, SL or SL-M.";
export const ORDER_TYPE_MISSING = "Pick an order type: Market, Limit, SL or SL-M";
/** The ticket fields a new user is asked for, in the order the ticket asks them (each with "Save as my default"). */
export type AskField = "order-type" | "product";

/** The first field still Not set (focused by the ticket), or null when both are chosen. */
export function firstMissing(d: Pick<Draft, "orderType" | "product">): AskField | null {
  if (d.orderType === null) return "order-type";
  if (d.product === null) return "product";
  return null;
}

export const PRODUCTS: { value: Product; label: string; hint: string; term: GlossaryKey }[] = [
  { value: "MIS", label: "MIS", hint: "Intraday", term: "mis" }, { value: "NRML", label: "NRML", hint: "Carry forward", term: "nrml" },
];
// UI-28 U1.1 (owner, 28 Oct): the mode is labelled once, as a "PAPER" chip in the header; the sentence is its tooltip only.
// CT-1 C8: the words live with the one badge (ui/PaperBadge); these names stay for the pieces that import them.
export const PRACTICE_TITLE = PAPER_WORD;
export const PRACTICE_TEXT = PAPER_TEXT;
export const MAX_LOTS = 500;

/** Settings part C: the lots / product / order type the user's own defaults start a ticket with (only the ones set). */
export interface DraftDefaults { lots?: number; product?: Product; orderType?: OrderType }

/** A fresh ticket. With the user's defaults (src/settings/tradingDefaults.ts ticketPrefill) those values start it - the side
 * never comes from a default - and nothing else changes: no defaults gives today's draft exactly. */
export function newDraft(side: Side, defaults?: DraftDefaults): Draft {
  const draft: Draft = { side, lots: 1, orderType: null, product: null, price: "", trigger: "", stopLoss: "" };
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
export function needs(orderType: OrderType | null): { price: boolean; trigger: boolean } {
  return { price: orderType === "LIMIT" || orderType === "SL", trigger: orderType === "SL" || orderType === "SL-M" };
}

const num = (text: string): number | null => {
  const v = Number(text.replace(/,/g, "").trim());
  return text.trim() !== "" && Number.isFinite(v) && v > 0 ? v : null;
};

/** The stop-loss field's own words. DEF-0 D7 (owner, 28 Oct): a stop is the trader's choice on every side - only his own
 * setting "A sold option or a future needs a stop-loss" asks for one, and the server says so in the review. */
export function stopHint(_side: Side, _right: ContractRight = "CE"): string {
  return "(optional)";
}

/** What is missing before the ticket can be reviewed (the server checks everything else). */
export function draftProblems(d: Draft, _right: ContractRight = "CE"): string[] {
  const out: string[] = [];
  if (d.orderType === null) out.push(ORDER_TYPE_MISSING);
  if (d.product === null) out.push(PRODUCT_MISSING);
  const n = needs(d.orderType);
  if (n.price && num(d.price) === null) out.push("Enter the limit price");
  if (n.trigger && num(d.trigger) === null) out.push("Enter the trigger price");
  if (d.stopLoss.trim() !== "" && num(d.stopLoss) === null) out.push("The stop-loss is not a price");
  return out;
}

export function askOf(target: TicketTarget, d: Draft, clientRef: string): OrderAsk {
  const n = needs(d.orderType);
  return {
    underlying: target.underlying, expiry: target.expiry, strike: target.right === "FUT" ? null : target.strike, right: target.right, side: d.side,
    // draftProblems asks for a missing order type / product first; the server asks again if one is still missing.
    lots: clampLots(d.lots), order_type: d.orderType as OrderType, product: d.product as Product,
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
  return typeof v === "number" && Number.isFinite(v) ? formatINR(v, { paise: true }) : "–";
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
  } catch { /* intentional: no crypto: fall back */ }
  return `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}


/** DEF-0 D6 (owner, 28 Oct): why the order cannot be placed, in one line, and where to change it. Only a rule the
 * trader set himself, a broker / exchange answer or a LIVE switch can hold an order back (an "Information only" line
 * never does); his own limits link to the place he changes them. Null when it can be placed. */
export function blockedBy(p: { can_place: boolean; errors: string[]; refusals: string[] }): { text: string; link: string | null } | null {
  if (p.can_place) return null;
  const text = p.errors[0] ?? p.refusals[0] ?? "This order cannot be placed right now.";
  const own = /your (setting|limit|risk per trade|own|minimum|daily loss)|my limits|limit of|today's loss limit|losses in a row|positions, your limit|your limit is|max lots per order|experience level/i.test(text);
  // M64 RM-3: his own limits link to Settings › Risk limits; an organisation ceiling says "Ask your admin" in its sentence.
  return { text, link: own && !/ask your admin/i.test(text) ? "/settings/risk-limits" : null };
}

/** The basket's confirm step: the first reason it cannot be placed (a leg's error or refusal, then the basket's own), or
 * null when it can - the same sentence and "My limits" link as `blockedBy`. */
export function basketBlockedBy(p: { can_execute: boolean; refusals: string[]; legs: { errors: string[]; refusals: string[] }[] }):
  { text: string; link: string | null } | null {
  if (p.can_execute) return null;
  return blockedBy({ can_place: false, errors: p.legs.flatMap((l) => l.errors), refusals: [...p.legs.flatMap((l) => l.refusals), ...p.refusals] });
}
