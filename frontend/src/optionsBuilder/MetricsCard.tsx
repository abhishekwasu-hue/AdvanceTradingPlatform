/**
 * P1-c2: the numbers beside the payoff - net credit / debit, max profit and loss ("unlimited" when the structure has
 * no cap), reward to risk, PoP, probability-weighted P&L, the expected move, breakevens and net Greeks - each a model
 * estimate, labelled so, with how it was computed. A risk gauge shows max loss against the premium at stake.
 */
import { cx } from "../components/primitives";
import { money, netPremium, rewardToRisk, type Evaluation, type Leg } from "./model";

function Row({ label, value, tone, hint }: { label: string; value: string; tone?: "up" | "down" | "warn"; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5" title={hint}>
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className={cx("font-mono text-sm tabular-nums", tone === "up" ? "text-up" : tone === "down" ? "text-down" : tone === "warn" ? "text-warn" : "text-fg")}>{value}</dd>
    </div>
  );
}

export function MetricsCard({ evaluation: e, legs }: { evaluation: Evaluation | null; legs: Leg[] }) {
  if (!e) {
    return (
      <aside aria-label="Metrics" className="rounded-xl border border-border bg-surface-1 p-4 text-sm text-fg-muted">
        The numbers appear once the legs are priced.
      </aside>
    );
  }
  const net = netPremium(legs);
  const ext = e.extremes;
  const rr = rewardToRisk(ext);
  const g = e.summary.greeks;
  return (
    <aside aria-label="Metrics" className="rounded-xl border border-border bg-surface-1 p-4" data-testid="metrics">
      <h2 className="text-sm font-semibold text-fg">The strategy</h2>
      <dl className="mt-2 divide-y divide-border/60">
        <Row label={net >= 0 ? "Net credit" : "Net debit"} value={money(Math.abs(net))} tone={net >= 0 ? "up" : undefined} />
        {ext ? (
          <>
            <Row label="Max profit" value={ext.unbounded_profit ? "Unlimited" : money(ext.max_profit)} tone="up" />
            <Row label="Max loss" value={ext.unbounded_loss ? "Unlimited" : money(ext.max_loss)} tone={ext.unbounded_loss ? "warn" : "down"}
                 hint={ext.unbounded_loss ? "No cap: the loss grows with the price - size it as undefined risk" : undefined} />
            <Row label="Reward : risk" value={rr == null ? "–" : `${rr.toFixed(2)} : 1`} />
          </>
        ) : (
          <Row label="Max profit / loss" value="see the curve" hint="Legs expire on different dates: there is no single expiry payoff" />
        )}
        <Row label="Probability of profit" value={`${(e.summary.pop * 100).toFixed(1)}%`} />
        <Row label="Probability-weighted P&L" value={money(e.summary.expected_pnl)} tone={e.summary.expected_pnl >= 0 ? "up" : "down"} />
        <Row label="Expected move (1 sd)" value={`±${Math.round(e.summary.expected_move).toLocaleString("en-IN")}`} />
        <Row label="Breakevens" value={e.breakevens?.length ? e.breakevens.map((b) => Math.round(b).toLocaleString("en-IN")).join(" · ") : "–"} />
        <Row label="Theta / day" value={money(g.theta)} tone={g.theta >= 0 ? "up" : "down"} />
        <Row label="Delta · Vega" value={`${g.delta.toFixed(1)} · ${g.vega.toFixed(0)}`} />
      </dl>
      {ext && !ext.unbounded_loss && ext.max_loss != null && ext.max_loss < 0 && (
        <div className="mt-3" aria-label="Risk gauge">
          <div className="flex justify-between text-[11px] text-fg-muted"><span>Max loss</span><span>Max profit</span></div>
          <div className="mt-1 flex h-2 overflow-hidden rounded-full bg-surface-2">
            {ext.max_profit != null && (
              <>
                <div className="bg-down/70" style={{ width: `${(100 * Math.abs(ext.max_loss)) / (Math.abs(ext.max_loss) + Math.max(ext.max_profit, 0) || 1)}%` }} />
                <div className="flex-1 bg-up/70" />
              </>
            )}
          </div>
        </div>
      )}
      <p className="mt-3 text-[11px] leading-snug text-fg-muted">
        {e.summary.method === "" ? "" : `${e.summary.method[0].toUpperCase()}${e.summary.method.slice(1)}. `}{e.disclaimer}
      </p>
    </aside>
  );
}
