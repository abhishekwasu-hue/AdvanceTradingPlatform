/**
 * Z5 / P1-c: "Review basket" - the strategy as the engine would place it: hedge legs first, each with its
 * quantity (lots x lot size) and price. In this release placing is not available: the PAPER basket comes with the
 * plan-before-trade ticket. REV-1 M2: the dialog is review only and says so - no place button that can never be
 * pressed; a place button shows only if the server ever answers `placeable`. There is no LIVE path from the builder.
 */
import { useEffect, useState } from "react";
import { Button, Dialog } from "../components/primitives";
import { builderApi } from "./api";
import { basketUnderlying, contractLabel, money, type Basket, type Leg } from "./model";

export function BasketDialog({ open, onOpenChange, underlying, legs, errorText }: {
  open: boolean; onOpenChange: (open: boolean) => void; underlying: string; legs: Leg[]; errorText: (e: unknown) => string;
}) {
  const [basket, setBasket] = useState<Basket | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const name = basketUnderlying(underlying);

  useEffect(() => {
    if (!open) return undefined;
    setBasket(null);
    setProblem(null);
    if (!name) { setProblem("Name the underlying (one word, such as NIFTY) to build the basket."); return undefined; }
    let live = true;
    builderApi.basket(name, legs).then((b) => { if (live) setBasket(b); }).catch((e: unknown) => { if (live) setProblem(errorText(e)); });
    return () => { live = false; };
  }, [open, name, legs, errorText]);

  const n = basket?.orders.length ?? legs.length;
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Order basket (review only)"
            description="The legs in the order they would be placed: hedges first, so the margin sees the protection before the short. Nothing is placed from here."
            footer={(
              <div className="flex w-full flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-end">
                {basket && !basket.placeable && <p className="text-xs text-fg-muted sm:mr-auto" data-testid="basket-why-not">{basket.why_not}</p>}
                {basket?.placeable
                  ? <Button variant="primary" data-testid="basket-place">Place {n} legs in PAPER</Button>
                  : <Button onClick={() => onOpenChange(false)} data-testid="basket-close">Close</Button>}
              </div>
            )}>
      {problem && <p role="alert" className="text-sm text-warn">{problem}</p>}
      {!problem && !basket && <p className="text-sm text-fg-muted">Building the basket…</p>}
      {basket && (
        <div className="space-y-3">
          <ol className="divide-y divide-border/60 rounded-lg border border-border" data-testid="basket-orders">
            {basket.orders.map((o) => (
              <li key={o.seq} className="flex items-center justify-between gap-3 px-3 py-2 text-xs" data-testid="basket-order">
                <span className="font-mono text-fg-muted">{o.seq}</span>
                <span className={o.side === "BUY" ? "flex-1 font-mono font-semibold text-up" : "flex-1 font-mono font-semibold text-down"}>{contractLabel(o)}</span>
                <span className="font-mono tabular-nums text-fg">{o.quantity.toLocaleString("en-IN")} qty</span>
                <span className="w-20 text-right font-mono tabular-nums text-fg-muted">@ {o.price.toFixed(2)}</span>
              </li>
            ))}
          </ol>
          <p className="text-sm text-fg">
            {basket.net_premium >= 0 ? "Net credit" : "Net debit"} <span className="font-mono tabular-nums">{money(Math.abs(basket.net_premium))}</span>
          </p>
        </div>
      )}
    </Dialog>
  );
}
