import { ShieldCheck, ShoppingBasket, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cx } from "../components/primitives/cx";
import { basketApi, type BasketApi, type BasketNumbers, type Product } from "../fno/api";
import type { BasketLeg } from "../fno/basket";
import { basketAsk } from "../fno/basketModel";
import { basketOn } from "../fno/desk";
import { moneyText } from "../fno/ticketModel";
import { EMPTY, formatNumber } from "../ui/format";
import { Details } from "../ui/Details";

/**
 * Owner fix T1-4 (24 Oct): the spread builder of the Option-seller layout - the sold leg and its hedge as ONE group
 * (TT-8), with the net credit, the max loss, the breakeven(s) and the margin with the hedge benefit, from the same
 * evaluation the basket uses (POST /api/paper/fno/basket/evaluate, backend paper/fno_basket.py). Nothing is placed here:
 * "Review and place" opens the existing basket confirm step, which places the hedge (buy) legs first, then the sells,
 * and stops with the INCOMPLETE alert ("Complete the leg" / "Exit the filled leg") when a later leg does not fill - a
 * short is never left uncovered without that alert. Exits close the sold leg first, then the hedge; never blocked.
 */
const EVAL_DELAY_MS = 300;

/** Pure: the legs in the order they are placed - every bought (hedge) leg first, then the sold ones. */
export function placementOrder<T extends { side: string }>(legs: readonly T[]): { leg: T; role: "hedge" | "sell" }[] {
  const buys = legs.filter((l) => l.side === "BUY").map((leg) => ({ leg, role: "hedge" as const }));
  const sells = legs.filter((l) => l.side !== "BUY").map((leg) => ({ leg, role: "sell" as const }));
  return [...buys, ...sells];
}

/** Pure: a sold option with no bought option of the same right (CE / PE) on this basket has no hedge. */
export function unhedgedSells(legs: readonly Pick<BasketLeg, "side" | "right" | "strike">[]): number[] {
  return legs.filter((l) => l.side === "SELL" && l.right !== "FUT" && !legs.some((h) => h.side === "BUY" && h.right === l.right)).map((l) => l.strike);
}

export function SpreadBuilderV2({ underlying, legs, spot, product = null, onOpen, api = basketApi }: {
  underlying: string; legs: readonly BasketLeg[]; spot: number | null; product?: Product | null; onOpen?: () => void;
  api?: Pick<BasketApi, "evaluate">;
}) {
  const mine = useMemo(() => basketOn(legs, underlying), [legs, underlying]);
  const [numbers, setNumbers] = useState<BasketNumbers | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const seq = useRef(0);
  useEffect(() => {
    const n = ++seq.current;
    if (!mine.length) { setNumbers(null); setProblem(null); return undefined; }
    const timer = window.setTimeout(() => {
      api.evaluate(basketAsk(mine.map(({ id: _id, ...rest }) => rest), spot, product)).then((out) => {
        if (n !== seq.current) return;
        if (out.ok) { setNumbers(out); setProblem(null); } else { setNumbers(null); setProblem(out.errors[0] ?? null); }
      }).catch(() => { if (n === seq.current) { setNumbers(null); setProblem("The numbers could not be worked out just now."); } });
    }, EVAL_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [mine, spot, product, api]);

  if (!mine.length) {
    return (
      <div className="space-y-space-2" data-panel-body="basket" data-spread-builder="empty">
        <p className="py-space-2 text-t13 text-fg-muted" data-empty="basket-empty">
          Build a spread on {underlying}: tap a price on the option chain and choose Add to basket - the leg you sell and the leg that
          protects it (bought further out). They are placed and shown as one group.
        </p>
        {onOpen && <OpenButton onOpen={onOpen} label="Open the basket" />}
      </div>
    );
  }
  const order = placementOrder(mine);
  const naked = unhedgedSells(mine);
  const ext = numbers?.extremes ?? null;
  const hedge = numbers?.margin.hedge ?? null;
  const credit = numbers ? numbers.net_premium : null;
  return (
    <div className="space-y-space-3" data-panel-body="basket" data-spread-builder="legs">
      <ol className="divide-y divide-subtle text-t13" data-basket-legs aria-label="The order the legs are placed in">
        {order.map(({ leg, role }, i) => (
          <li key={leg.id} className="flex items-baseline justify-between gap-space-2 py-space-1" data-leg-role={role} data-seq={i + 1}>
            <span className="min-w-0 truncate text-fg">
              <span className="font-tabular text-fg-muted">{i + 1}.</span>{" "}
              <span className="font-semibold">{role === "hedge" ? "Hedge" : "Sell"}</span>{" · "}
              <span className={leg.side === "SELL" ? "text-down" : "text-up"}>{leg.side === "SELL" ? "Sell" : "Buy"}</span>{" "}
              {leg.label ?? `${formatNumber(leg.strike, 0)} ${leg.right}`}
            </span>
            <span className="shrink-0 font-tabular text-fg-muted">{leg.lots} lot{leg.lots === 1 ? "" : "s"}</span>
          </li>
        ))}
      </ol>
      <dl className="grid grid-cols-2 gap-x-space-3 gap-y-space-2 text-t13" data-spread-numbers>
        <Fig label={credit === null ? "Net premium" : credit >= 0 ? "Net credit" : "Net debit"} testId="net">{credit === null ? EMPTY : moneyText(Math.abs(credit))}</Fig>
        <Fig label="Max loss" testId="max-loss">{!ext ? EMPTY : ext.unbounded_loss ? "Unlimited" : moneyText(ext.max_loss === null ? null : Math.abs(ext.max_loss))}</Fig>
        <Fig label={numbers?.breakevens && numbers.breakevens.length > 1 ? "Breakevens" : "Breakeven"} testId="breakeven">
          {numbers?.breakevens?.length ? numbers.breakevens.map((b) => formatNumber(Math.round(b), 0)).join(" · ") : EMPTY}
        </Fig>
        <Fig label="Margin with the hedge" testId="margin">{hedge?.with_hedge.value != null ? `${moneyText(hedge.with_hedge.value)} (estimate)` : EMPTY}</Fig>
      </dl>
      {hedge?.benefit != null && hedge.benefit > 0 && (
        <p className="flex items-start gap-space-2 text-t13 text-fg" data-spread="hedge-benefit">
          <ShieldCheck size={16} aria-hidden className="mt-0.5 shrink-0 text-up" />
          The hedge lowers the margin by {moneyText(hedge.benefit)} against the sold leg alone.
        </p>
      )}
      {naked.length > 0 && (
        <p role="alert" className="flex items-start gap-space-2 rounded-control bg-warn-tint px-space-3 py-space-2 text-t13 text-warn" data-spread="unhedged">
          <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0" />
          The sold {naked.map((k) => formatNumber(k, 0)).join(", ")} has no hedge: its loss has no limit. Add a bought leg of the same type to cap it.
        </p>
      )}
      {problem && <p className="text-t13 text-fg-muted" data-spread="problem">{problem}</p>}
      <Details id="terminal.spread" className="mt-0">
        <p data-spread="sequence">
          Placed one after another: the hedge (buy) first, then the sell. On exit the sold leg closes first, then the hedge.
          If a leg does not go through you are told at once, with "Complete the leg" or "Exit the filled leg".
        </p>
        <p data-spread="exits">
          Next, under &quot;Exits for this strategy&quot;, you can set an overall stop-loss and target for the whole spread (the spot level by default).
        </p>
      </Details>
      {onOpen && <OpenButton onOpen={onOpen} label="Review and place" />}
    </div>
  );
}

function Fig({ label, testId, children }: { label: string; testId: string; children: ReactNode }) {
  return (
    <div data-figure={testId}>
      <dt className="text-fg-muted">{label}</dt>
      <dd className="font-tabular text-t14 font-semibold text-fg">{children}</dd>
    </div>
  );
}

function OpenButton({ onOpen, label }: { onOpen: () => void; label: string }) {
  return (
    <button type="button" onClick={onOpen} data-basket-open
      className={cx("inline-flex min-h-11 items-center gap-space-2 rounded-control border border-border px-space-3 text-t14 font-semibold text-fg hover:bg-surface-2 lg:min-h-9")}>
      <ShoppingBasket size={16} aria-hidden />{label}
    </button>
  );
}
