import type { BasketLeg, NewLeg } from "../fno/basket";
import { itemKey } from "../chart/watchlistsV2";
import { isFnoPosition, positionItem, type ContractFacts } from "../fno/contracts";
import { heldPayoff, posLeg, type PayoffLeg, type PosLeg } from "../fno/positionInsights";
import type { TradeRecord } from "../types";

/**
 * M63 I-5 (the Terminal's Payoff panel): what the panel draws, pure. Read only - it draws his own legs and changes
 * nothing; the panel holds no order button.
 * - "Your positions": his open F&O PAPER positions on the Terminal's ONE underlying (the context bar's), each known
 *   contract at its entry price (fno/positionInsights.heldPayoff - the same exact payoff at expiry as "Compare a change").
 * - "Your basket": the basket's legs on that underlying (their lot sizes come from the basket's own engine read).
 * - One expiry for both, so there is one outcome at expiry: the context bar's expiry when any of his legs is on it, else
 *   the nearest expiry among his legs. Legs on another expiry are counted, never drawn.
 */

export interface PayoffScope {
  /** The one expiry drawn ("2026-11-03"), null when he has no legs on this underlying. */
  expiry: string | null;
  held: PosLeg[];
  basket: NewLeg[];
  /** His legs on this underlying left out because they expire on another day. */
  otherExpiry: number;
}

/** His open F&O PAPER positions on `underlying` as payoff legs (a contract not known yet, or expired, is left out). */
export function heldOn(positions: readonly TradeRecord[] | null | undefined, facts: ReadonlyMap<string, ContractFacts>, underlying: string): PosLeg[] {
  const out: PosLeg[] = [];
  for (const p of positions ?? []) {
    if (p.mode !== "PAPER" || !isFnoPosition(p)) continue;
    const leg = posLeg(p, facts.get(itemKey(positionItem(p))), null);
    if (leg && leg.underlying.toUpperCase() === underlying.toUpperCase()) out.push(leg);
  }
  return out;
}

export function payoffScope({ underlying, expiry, held, basket }: {
  underlying: string; expiry: string | null; held: readonly PosLeg[]; basket: readonly BasketLeg[];
}): PayoffScope {
  const mine = basket.filter((l) => l.underlying.toUpperCase() === underlying.toUpperCase()).map(({ id: _id, ...rest }) => rest as NewLeg);
  const expiries = [...new Set([...held.map((l) => l.expiry), ...mine.map((l) => l.expiry)])].sort();
  const pick = expiry && expiries.includes(expiry) ? expiry : expiries[0] ?? null;
  const onPick = <T extends { expiry: string }>(legs: readonly T[]) => legs.filter((l) => l.expiry === pick);
  const keptHeld = onPick(held);
  const keptBasket = onPick(mine);
  return { expiry: pick, held: keptHeld, basket: keptBasket, otherExpiry: held.length + mine.length - keptHeld.length - keptBasket.length };
}

/** The positions' side as the chart reads it. */
export const heldSide = (scope: PayoffScope): PayoffLeg[] => heldPayoff(scope.held);
