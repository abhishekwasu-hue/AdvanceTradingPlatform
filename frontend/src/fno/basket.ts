/**
 * FNO-1 (owner order section 5, first part): the basket's legs, kept in this browser for now - "Add to basket" on the
 * chain adds one, the badge shows the count everywhere the store is read. The basket panel (payoff, hedge margin,
 * Execute basket) is the next PR; nothing here places an order.
 *
 * Kept in sessionStorage so a refresh keeps the legs; every read and write is guarded (private windows, blocked
 * storage), and the basket still works in memory without it.
 */
import { useSyncExternalStore } from "react";
import type { ContractRight, Side } from "./api";

export interface BasketLeg {
  id: string;
  underlying: string;
  expiry: string;
  /** FNO-3: 0 for a future (it has no strike). */
  strike: number;
  /** CE / PE, or FUT for a future (FNO-3: "Add to basket" on the futures chain). */
  right: ContractRight;
  side: Side;
  lots: number;
  /** The price the chain showed when the leg was added (the panel re-prices it). */
  price: number | null;
  /** "NIFTY 28 OCT 22800 CE" when known. */
  label: string | null;
}

export const BASKET_KEY = "atp_fno_basket";
export const MAX_LEGS = 20;

function load(): BasketLeg[] {
  try {
    const raw = JSON.parse(sessionStorage.getItem(BASKET_KEY) ?? "[]") as unknown;
    return Array.isArray(raw) ? (raw as BasketLeg[]).filter((l) => l && typeof l.strike === "number" && typeof l.underlying === "string") : [];
  } catch {
    return [];
  }
}

export class BasketStore {
  private legs: BasketLeg[];
  private readonly listeners = new Set<() => void>();

  constructor(initial: BasketLeg[] = load()) { this.legs = initial.slice(0, MAX_LEGS); }

  readonly subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  };

  readonly snapshot = (): BasketLeg[] => this.legs;

  /** Adds a leg; the same contract and side adds its lots to the leg already there. False when the basket is full. */
  add(leg: Omit<BasketLeg, "id">): boolean {
    const same = this.legs.find((l) => l.underlying === leg.underlying && l.expiry === leg.expiry && l.strike === leg.strike
      && l.right === leg.right && l.side === leg.side);
    if (same) {
      this.set(this.legs.map((l) => (l === same ? { ...l, lots: l.lots + leg.lots, price: leg.price ?? l.price } : l)));
      return true;
    }
    if (this.legs.length >= MAX_LEGS) return false;
    const id = `${leg.underlying}|${leg.expiry}|${leg.strike}|${leg.right}|${leg.side}|${Date.now().toString(36)}`;
    this.set([...this.legs, { ...leg, id }]);
    return true;
  }

  remove(id: string): void { this.set(this.legs.filter((l) => l.id !== id)); }

  clear(): void { this.set([]); }

  private set(next: BasketLeg[]): void {
    this.legs = next;
    try { sessionStorage.setItem(BASKET_KEY, JSON.stringify(next)); } catch { /* storage unavailable: kept in memory */ }
    this.listeners.forEach((fn) => fn());
  }
}

/** The one basket of this tab. */
export const basket = new BasketStore();

export function useBasket(store: BasketStore = basket): BasketLeg[] {
  return useSyncExternalStore(store.subscribe, store.snapshot, store.snapshot);
}
