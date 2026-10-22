/**
 * F&O workspace 8b, slice 4 (owner order section 8b-i): the pure parts of the desktop F&O cockpit layout on My
 * positions (tested in desk.test.tsx) - which underlying the panels follow, his own strikes on it, and the part of the
 * chain around them. Read only: every figure is his own positions' or the chain's; nothing here suggests a strike.
 */
import type { ChainRow } from "../optionChain/types";
import { formatNumber } from "../ui/format";
import type { BasketLeg } from "./basket";
import type { CockpitUnit } from "./cockpit";
import type { PosLeg } from "./positionInsights";

export type DeskPanel = "glance" | "chart" | "chain" | "basket";
export const PANELS: { key: DeskPanel; label: string }[] = [
  { key: "glance", label: "Summary" }, { key: "chart", label: "Chart" }, { key: "chain", label: "Chain" }, { key: "basket", label: "Basket" },
];

export interface DeskUnderlying { underlying: string; spotSymbol: string; spotExchange: string; legs: number; expiry: string }

/** The underlyings he holds F&O on, most legs first (then by name); each with its nearest expiry held. */
export function deskUnderlyings(units: readonly CockpitUnit[]): DeskUnderlying[] {
  const by = new Map<string, DeskUnderlying>();
  for (const l of units.flatMap((u) => u.legs)) {
    const d = by.get(l.underlying);
    if (!d) by.set(l.underlying, { underlying: l.underlying, spotSymbol: l.spotSymbol, spotExchange: l.spotExchange, legs: 1, expiry: l.expiry });
    else { d.legs += 1; if (l.expiry < d.expiry) d.expiry = l.expiry; }
  }
  return [...by.values()].sort((a, b) => b.legs - a.legs || a.underlying.localeCompare(b.underlying));
}

const sideWord = (l: Pick<PosLeg, "side">) => (l.side === "SELL" ? "short" : "long");

/** His option legs on this underlying and expiry, as the chain marks them ("24900 CE short"). */
export function heldOn(units: readonly CockpitUnit[], underlying: string, expiry: string): { strike: number; right: "CE" | "PE"; label: string }[] {
  return units.flatMap((u) => u.legs)
    .filter((l): l is PosLeg & { right: "CE" | "PE" } => l.underlying === underlying && l.expiry === expiry && l.right !== "FUT")
    .map((l) => ({ strike: l.strike, right: l.right, label: `${formatNumber(l.strike, 0)} ${l.right} ${sideWord(l)}` }));
}

/** The chain rows from `extra` strikes below his lowest held strike to `extra` above his highest (none held: around the
 * ATM strike). */
export function chainAround(rows: readonly ChainRow[], held: readonly number[], atm: number | null, extra = 2): ChainRow[] {
  const sorted = [...rows].sort((a, b) => a.strike - b.strike);
  if (!sorted.length) return [];
  const anchors = held.length ? held : atm !== null ? [atm] : [];
  if (!anchors.length) return [];
  const idx = (k: number) => {
    let best = 0;
    sorted.forEach((r, i) => { if (Math.abs(r.strike - k) < Math.abs(sorted[best].strike - k)) best = i; });
    return best;
  };
  const lo = Math.max(0, Math.min(...anchors.map(idx)) - extra);
  const hi = Math.min(sorted.length - 1, Math.max(...anchors.map(idx)) + extra);
  return sorted.slice(lo, hi + 1);
}

/** His basket's legs on this underlying (the basket the chain builds; kept for this browser tab). */
export const basketOn = (legs: readonly BasketLeg[], underlying: string) => legs.filter((l) => l.underlying === underlying);

export const DESK_NOTE = "Read only. The chart, the chain and the basket open on their own pages to work on; nothing here places an order.";
