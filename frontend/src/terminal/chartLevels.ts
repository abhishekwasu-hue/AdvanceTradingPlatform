import type { LevelLine } from "../chart/chartCore";
import type { BasketLeg } from "../fno/basket";
import type { CombinedExit } from "../fno/combinedExit";

/**
 * TERM-2 step 2: what the Terminal draws on its chart for the ONE active symbol + expiry - the user's own legs (the
 * basket's and the strikes he holds), the basket's breakevens (the server's figures) and his own spot-based stop /
 * target of a combined exit. Lines only, from his own data: nothing is suggested. Pure.
 */
export function terminalLevels({ underlying, expiry, basket, held, breakevens, combined, spotSymbol }: {
  underlying: string;
  expiry: string | null;
  basket: readonly BasketLeg[];
  /** "strike|CE" / "strike|PE" he holds on this underlying and expiry (the chain's "P" marks). */
  held: ReadonlySet<string>;
  breakevens: readonly number[] | null;
  combined: readonly CombinedExit[];
  spotSymbol: string;
}): LevelLine[] {
  const out: LevelLine[] = [];
  const seen = new Set<string>();
  const add = (price: number, title: string, tone: LevelLine["tone"]) => {
    const key = `${price}|${title}`;
    if (!Number.isFinite(price) || price <= 0 || seen.has(key)) return;
    seen.add(key);
    out.push({ price, title, tone });
  };
  for (const l of basket) {
    if (l.underlying !== underlying || l.right === "FUT" || (expiry && l.expiry !== expiry)) continue;
    add(l.strike, `${l.side === "SELL" ? "Sell" : "Buy"} ${l.strike} ${l.right}`, l.side === "SELL" ? "down" : "up");
  }
  for (const k of held) {
    const [strike, right] = k.split("|");
    add(Number(strike), `Held ${strike} ${right}`, "neutral");
  }
  for (const b of breakevens ?? []) add(Math.round(b * 100) / 100, "Breakeven", "neutral");
  for (const c of combined) {
    if (c.measure !== "SPOT" || c.status !== "ARMED" || (c.spot_symbol ?? "").toUpperCase() !== spotSymbol.toUpperCase()) continue;
    if (c.sl_value !== null) add(c.sl_value, "Your stop", "down");
    if (c.target_value !== null) add(c.target_value, "Your target", "up");
  }
  return out;
}
