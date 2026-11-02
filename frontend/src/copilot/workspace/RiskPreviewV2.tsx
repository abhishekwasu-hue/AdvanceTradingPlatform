import { useEffect, useMemo, useState } from "react";
import { request } from "../../api/client";
import { cx } from "../../components/primitives/cx";
import { builderApi, wireLegs } from "../../optionsBuilder/api";
import { rangePctFor, type Evaluation, type Leg } from "../../optionsBuilder/model";
import { insightsApi } from "../../fno/insights";
import type { PositionGreeks } from "../../types";
import { riskRows, type BookGreeks, type BookRead } from "./riskPreview";

/**
 * M76 MP-4 (owner order M76 W-3; flag `copilot_workspace`): the risk preview under the trade plan's numbers - max loss /
 * profit in rupees and points, break-evens and their distance from spot, his PAPER balance after the margin estimate,
 * his book's (and this underlying's) Greeks after the trade, and the IV row when he saved his IV rise. Figures only:
 * no warning, no switch, never a hold on Place (the ticket's check card is the one card of warnings). Read-only - it
 * calls no order route. The rules and wording live in `riskPreview.ts` (tested in riskPreview.test.tsx).
 */
type Greeks = PositionGreeks & { underlying_net?: (BookGreeks & { legs: number }) | null };

/** His own open option legs (tenant AND user) and the sums for this one underlying. */
export const greeksPath = (underlying: string) => `/positions/greeks?${new URLSearchParams({ mine: "true", underlying }).toString()}`;

/** The builder's request for the IV row: the same legs and spot, IV raised by his points (absolute: 5 points = 0.05). */
export function ivRequest(legs: Leg[], spot: number, asOf: string, ivPoints: number) {
  return { legs: wireLegs(legs), spot, as_of: asOf, days_forward: 0, iv_shift: ivPoints / 100, range_pct: rangePctFor(spot, legs), points: 241 };
}

export const riskPreviewApi = {
  greeks: (underlying: string) => request<Greeks>(greeksPath(underlying)),
  balance: () => insightsApi.riskBudget().then((b) => (typeof b.paper_balance?.amount === "number" ? b.paper_balance.amount : null)),
  evaluate: builderApi.evaluate,
};
export type RiskPreviewApi = typeof riskPreviewApi;

export interface RiskPreviewProps {
  underlying: string; evaluation: Evaluation; legs: Leg[]; spot: number; asOf: string;
  ivPoints: number | null; api?: RiskPreviewApi;
}

/** "a · b · c" with each part kept whole, so a narrow phone wraps between figures, never inside one. */
function Parts({ text }: { text: string }) {
  const parts = text.split(" · ");
  return <>{parts.map((p, i) => <span key={i}>{i > 0 ? " · " : ""}<span className="whitespace-nowrap">{p}</span></span>)}</>;
}

export function RiskPreviewV2({ underlying, evaluation, legs, spot, asOf, ivPoints, api = riskPreviewApi }: RiskPreviewProps) {
  const [balance, setBalance] = useState<number | null>(null);
  const [book, setBook] = useState<BookRead | null>(null);
  const [ivEval, setIvEval] = useState<Evaluation | null>(null);

  useEffect(() => {
    let live = true;
    api.balance().then((b) => { if (live) setBalance(b); }).catch(() => { if (live) setBalance(null); });
    return () => { live = false; };
  }, [api]);

  useEffect(() => {
    let live = true;
    setBook(null);
    api.greeks(underlying)
      .then((g) => { if (live) setBook({ net: g.net, underlying: g.underlying_net ?? null, problem: null }); })
      .catch((e: unknown) => { if (live) setBook({ net: null, underlying: null, problem: e instanceof Error ? e.message : String(e) }); });
    return () => { live = false; };
  }, [api, underlying, legs]);

  // The IV row: the same engine, the same legs, IV raised by his own points.
  useEffect(() => {
    let live = true;
    setIvEval(null);
    if (ivPoints == null || !legs.length) return undefined;
    api.evaluate(ivRequest(legs, spot, asOf, ivPoints))
      .then((ev) => { if (live) setIvEval(ev); })
      .catch(() => { if (live) setIvEval(null); });
    return () => { live = false; };
  }, [api, legs, spot, asOf, ivPoints]);

  const rows = useMemo(() => riskRows({ evaluation, legs, spot, balance, book, ivPoints, ivEvaluation: ivEval }),
    [evaluation, legs, spot, balance, book, ivPoints, ivEval]);

  return (
    <section aria-label="Risk preview" className="min-w-0 rounded-panel border border-subtle p-space-3" data-plan="risk-preview">
      <h4 className="text-t13 font-semibold text-fg">Risk preview</h4>
      <dl className="mt-space-1 divide-y divide-subtle text-t13">
        {rows.map((r) => (
          <div key={r.key} className="py-space-1" data-risk-row={r.key}>
            <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-space-3">
              <dt className="text-fg-muted">{r.label}</dt>
              <dd className={cx("ml-auto text-right font-tabular", r.tone === "up" ? "text-up" : r.tone === "down" ? "text-down" : "text-fg")}>
                <Parts text={r.value} />
              </dd>
            </div>
            {r.sub && <p className="mt-space-1 text-right text-t12 text-fg-muted font-tabular"><Parts text={r.sub} /></p>}
          </div>
        ))}
      </dl>
    </section>
  );
}

export default RiskPreviewV2;
