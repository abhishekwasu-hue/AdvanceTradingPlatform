/**
 * FNO-1 / FNO-2 (owner order section 5): the basket's legs, kept in this browser - "Add to basket" on the chain adds one,
 * a ready strategy or a saved basket replaces them, the badge shows the count everywhere the store is read, and the
 * basket panel (src/fno/BasketPanelV2.tsx) edits them. Nothing here places an order.
 *
 * Kept in sessionStorage so a refresh keeps the legs; every read and write is guarded (private windows, blocked
 * storage), and the basket still works in memory without it.
 */
import { useSyncExternalStore } from "react";
import type { Right, Side } from "./api";

export interface BasketLeg {
  id: string;
  underlying: string;
  expiry: string;
  strike: number;
  right: Right;
  side: Side;
  lots: number;
  /** The price the panel plans with: the chain's when the leg was added, or the user's own. Placing uses the live price. */
  price: number | null;
  /** "NIFTY 28 OCT 22800 CE" when known. */
  label: string | null;
  /** A sold leg's stop-loss (premium), needed to place it, as on the order ticket. */
  stopLoss?: number | null;
  /** The chain's IV in percent (13.2 = 13.2 %) when known; otherwise it is solved from the price. */
  iv?: number | null;
}

export type NewLeg = Omit<BasketLeg, "id">;

export const BASKET_KEY = "atp_fno_basket";
/** The Options Builder's limit for one strategy (backend app/paper/fno_basket.py MAX_LEGS). */
export const MAX_LEGS = 12;

function load(): BasketLeg[] {
  try {
    const raw = JSON.parse(sessionStorage.getItem(BASKET_KEY) ?? "[]") as unknown;
    return Array.isArray(raw) ? (raw as BasketLeg[]).filter((l) => l && typeof l.strike === "number" && typeof l.underlying === "string") : [];
  } catch {
    return [];
  }
}

let counter = 0;
function newId(leg: NewLeg): string {
  counter += 1;
  return `${leg.underlying}|${leg.expiry}|${leg.strike}|${leg.right}|${leg.side}|${Date.now().toString(36)}${counter}`;
}

const sameContract = (a: NewLeg, b: NewLeg) => a.underlying === b.underlying && a.expiry === b.expiry && a.strike === b.strike && a.right === b.right;

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
  add(leg: NewLeg): boolean {
    const same = this.legs.find((l) => sameContract(l, leg) && l.side === leg.side);
    if (same) {
      this.set(this.legs.map((l) => (l === same ? { ...l, lots: l.lots + leg.lots, price: leg.price ?? l.price, iv: leg.iv ?? l.iv } : l)));
      return true;
    }
    if (this.legs.length >= MAX_LEGS) return false;
    this.set([...this.legs, { ...leg, id: newId(leg) }]);
    return true;
  }

  /** Changes one leg (side, lots, price, stop, strike...). */
  update(id: string, patch: Partial<NewLeg>): void {
    this.set(this.legs.map((l) => (l.id === id ? { ...l, ...patch } : l)));
  }

  /** Every leg at once (a ready strategy, a saved basket). At most MAX_LEGS are kept. */
  replace(legs: NewLeg[]): void { this.set(legs.slice(0, MAX_LEGS).map((l) => ({ ...l, id: newId(l) }))); }

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
