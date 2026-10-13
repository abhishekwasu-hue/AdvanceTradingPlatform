import { money } from "../components/paperTicket";
import type { AiAction } from "../types";
import { breachRows } from "./riskCheck";

/**
 * Z5 / P1-d (flag risk_by_default): on a waiting entry proposal, what your own risk by default makes of it - the size
 * your cap allows at the proposal price, the risk at the stop, the cost line and anything that would refuse it now
 * (daily loss lockout, entries per day, India VIX gate) - and, when the proposal carries option legs, each short
 * strike's breach chance (model estimate). Every number comes from the server.
 */
export default function RiskCheckPanel({ a }: { a: Pick<AiAction, "risk_check" | "strike_breach" | "portfolio_impact"> }) {
  const rc = a.risk_check;
  const breach = breachRows(a.strike_breach);
  const impact = a.portfolio_impact?.line;
  if (!rc && breach.length === 0 && !impact) return null;
  return (
    <div className="space-y-1 rounded-lg border border-border bg-surface-2 p-2 text-xs" data-testid="proposal-risk-check">
      {rc && (
        <>
          <p className="text-fg">
            Your cap allows {rc.sizing.suggested_quantity} · risk at the stop {money(rc.sizing.risk_amount)} of {money(rc.sizing.cap)} ({rc.sizing.binding})
          </p>
          {rc.costs && <p className="text-fg-muted">Costs {money(rc.costs.total)} · break-even move {rc.costs.break_even_move ?? "-"} · reward:risk after costs {rc.costs.net_reward_risk ?? "-"}</p>}
          {[...rc.errors, ...rc.refusals].map((r) => <p key={r} className="text-down">{r}</p>)}
        </>
      )}
      {/* N1 F3 (flag portfolio_risk): the owner's own book after this entry - numbers only, never advice. */}
      {impact && <p className="text-fg-muted" data-testid="portfolio-impact">{impact}</p>}
      {breach.map((row) => <p key={row.key} className="text-fg-muted" data-testid="strike-breach-row">{row.text}</p>)}
      {breach.length > 0 && a.strike_breach && <p className="text-[11px] text-fg-muted">{a.strike_breach.note}</p>}
    </div>
  );
}
