/**
 * AUD-T2 (owner order ATP_ORDER_AUDIT_28OCT T2): the expiry-day and margin checklist (backend app/expiry_checklist; flags
 * `ui_v2` + `expiry_checklist`, 404 while off). Information only: "Add to basket" only adds a leg to this tab's basket
 * (src/fno/basket.ts); placing still goes through the basket's own confirm.
 */
import { request } from "../api/client";
import type { ContractRight, Side } from "./api";
import type { BasketStore } from "./basket";

export interface ExpiryLeg {
  underlying: string;
  expiry: string;
  strike: number;
  right: ContractRight;
  side: Side;
  lots: number;
  label: string;
}

export interface ExpiryRow {
  trade_id: number;
  label: string;
  symbol: string;
  underlying: string;
  right: ContractRight;
  strike: number | null;
  expiry: string;
  when: "today" | "next_session";
  side: Side;
  settlement: "cash" | "delivery";
  moneyness: "in" | "out" | "at" | null;
  priced: boolean;
  next_expiry: string | null;
  basket_leg: ExpiryLeg | null;
  headline: string;
  money_text: string | null;
  settlement_text: string;
  next_text: string;
}

export interface BrokerRule { broker: string; name: string; note: string; url: string }

export interface ExpiryChecklist {
  as_of: string;
  today: string;
  rows: ExpiryRow[];
  notes: string[];
  brokers: BrokerRule[];
}

export const expiryChecklistApi = {
  get: () => request<ExpiryChecklist>("/fno/expiry-checklist"),
};

/** Adds the next-expiry contract as ONE leg; never places anything. False when the basket is full (MAX_LEGS). */
export function addNextExpiry(store: BasketStore, leg: ExpiryLeg): boolean {
  return store.add({ underlying: leg.underlying, expiry: leg.expiry, strike: leg.strike, right: leg.right, side: leg.side,
    lots: leg.lots, price: null, label: leg.label, stopLoss: null, iv: null });
}
