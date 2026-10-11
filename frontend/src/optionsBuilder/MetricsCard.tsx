/**
 * P1-c2 / Z5: the numbers beside the payoff (spec §3.3) - net credit / debit, max profit and loss ("Unlimited" when the
 * structure has no cap), reward to risk, PoP, margin, theta per day, breakevens, cost, the probability-weighted P&L,
 * the expected move and net Greeks - and the risk gauge. Every number is the engine's (`evaluate`); this card only
 * formats it. Estimates are labelled so (the margin says how it was made, or why only the broker can say).
 */
import { cx } from "../components/primitives";
import { money, type Evaluation } from "./model";

function Row({ label, value, tone, hint, sub, testId }: {
  label: string; value: string; tone?: "up" | "down" | "warn"; hint?: string; sub?: string; testId?: string;
}) {
  return (
    <div className="py-1.5" title={hint} data-testid={testId}>
      <div className="flex items-baseline justify-between gap-3">
        <dt className="text-xs text-fg-muted">{label}</dt>
        <dd className={cx("text-right font-mono text-sm tabular-nums", tone === "up" ? "text-up" : tone === "down" ? "text-down" : tone === "warn" ? "text-warn" : "text-fg")}>{value}</dd>
      </div>
      {sub && <p className="mt-0.5 text-[11px] leading-snug text-fg-muted">{sub}</p>}
    </div>
  );
}

/** The risk gauge: how much of the range between the worst and the best expiry P&L is loss. An arc fills with the
 * loss share; a labelled reading beside it, so the colour is never the only cue. */
function RiskGauge({ maxLoss, maxProfit }: { maxLoss: number; maxProfit: number }) {
  const loss = Math.abs(maxLoss);
  const share = loss / (loss + Math.max(maxProfit, 0) || 1);
  const r = 40;
  const arc = Math.PI * r;
  return (
    <div className="mt-3 flex items-center gap-3" role="img" aria-label={`Risk gauge: risk ${money(-loss)} for a reward of ${money(maxProfit)}`} data-testid="risk-gauge">
      <svg viewBox="0 0 100 56" className="h-14 w-24 shrink-0" aria-hidden>
        <path d="M10,50 A40,40 0 0 1 90,50" className="fill-none stroke-up/60" strokeWidth={9} />
        <path d="M10,50 A40,40 0 0 1 90,50" className="fill-none stroke-down" strokeWidth={9} strokeDasharray={`${arc * share} ${arc}`} />
      </svg>
      <div className="text-[11px] leading-snug text-fg-muted">
        <div><span className="font-semibold text-down">Risk</span> {Math.round(share * 100)}% of the range</div>
        <div>{money(-loss)} to {money(maxProfit)}</div>
      </div>
    </div>
  );
}

export function MetricsCard({ evaluation: e, stale = false }: { evaluation: Evaluation | null; stale?: boolean }) {
  if (!e) {
    return (
      <aside aria-label="Metrics" className="rounded-xl border border-border bg-surface-1 p-4 text-sm text-fg-muted">
        The numbers appear once the legs are priced.
      </aside>
    );
  }
  const net = e.net_premium;
  const ext = e.extremes;
  const rr = e.reward_to_risk;
  const g = e.summary.greeks;
  const cost = e.costs;
  return (
    <aside aria-label="Metrics" aria-busy={stale} className={cx("rounded-xl border border-border bg-surface-1 p-4", stale && "opacity-60")} data-testid="metrics">
      {stale && <p className="mb-2 text-[11px] text-fg-muted" data-testid="metrics-stale">Updating for your last change…</p>}
      <h2 className="text-sm font-semibold text-fg">The strategy</h2>
      <dl className="mt-2 divide-y divide-border/60">
        <Row label={net >= 0 ? "Net credit" : "Net debit"} value={money(Math.abs(net))} tone={net >= 0 ? "up" : undefined} testId="metric-net" />
        {ext ? (
          <>
            <Row label="Max profit" value={ext.unbounded_profit ? "Unlimited" : money(ext.max_profit)} tone="up" />
            <Row label="Max loss" value={ext.unbounded_loss ? "Unlimited" : money(ext.max_loss)} tone={ext.unbounded_loss ? "warn" : "down"}
                 hint={ext.unbounded_loss ? "No cap: the loss grows with the price - size it as undefined risk" : undefined} testId="metric-max-loss" />
            <Row label="Reward : risk" value={rr != null ? `${rr.toFixed(2)} : 1` : ext.unbounded_profit && !ext.unbounded_loss ? "Unlimited" : "–"} />
          </>
        ) : (
          <Row label="Max profit / loss" value="see the curve" hint="Legs expire on different dates: there is no single expiry payoff" />
        )}
        <Row label="Probability of profit" value={`${(e.summary.pop * 100).toFixed(1)}%`} />
        <Row label="Margin (estimate)" value={e.margin.value != null ? money(e.margin.value) : "From the broker"} sub={e.margin.basis}
             hint={e.margin.note} testId="metric-margin" />
        <Row label="Theta / day" value={money(g.theta)} tone={g.theta >= 0 ? "up" : "down"} />
        <Row label="Breakevens" value={e.breakevens?.length ? e.breakevens.map((b) => Math.round(b).toLocaleString("en-IN")).join(" · ") : "–"} />
        <Row label="Cost to open (estimate)" value={money(cost.to_open.total)} testId="metric-cost"
             sub={`${cost.orders} orders at ${money(cost.brokerage_per_order)} each, STT ${money(cost.to_open.stt)}, other charges ${money(cost.to_open.other)}. Open and close: ${money(cost.round_trip)}.`}
             hint={cost.note} />
        <Row label="Probability-weighted P&L" value={money(e.summary.expected_pnl)} tone={e.summary.expected_pnl >= 0 ? "up" : "down"} />
        <Row label="Expected move (1 sd)" value={`±${Math.round(e.summary.expected_move).toLocaleString("en-IN")}`} />
      </dl>
      <dl className="mt-2 grid grid-cols-4 gap-2 rounded-lg bg-surface-2 px-2 py-2 text-center" aria-label="Net Greeks">
        {([["Delta", g.delta.toFixed(1)], ["Gamma", g.gamma.toFixed(3)], ["Theta", g.theta.toFixed(0)], ["Vega", g.vega.toFixed(0)]] as const).map(([k, v]) => (
          <div key={k} className="min-w-0"><dt className="text-[10px] text-fg-muted">{k}</dt><dd className="truncate font-mono text-xs tabular-nums text-fg">{v}</dd></div>
        ))}
      </dl>
      {ext && !ext.unbounded_loss && ext.max_loss != null && ext.max_loss < 0 && ext.max_profit != null && (
        <RiskGauge maxLoss={ext.max_loss} maxProfit={ext.max_profit} />
      )}
      {ext?.unbounded_loss && (
        <p className="mt-3 rounded-md border border-warn/40 bg-warn/10 px-2 py-1.5 text-[11px] text-warn" data-testid="risk-undefined">
          Undefined risk: the loss has no cap. Add a hedge leg to cap it.
        </p>
      )}
      <p className="mt-3 text-[11px] leading-snug text-fg-muted">
        {e.summary.method === "" ? "" : `${e.summary.method[0].toUpperCase()}${e.summary.method.slice(1)}. `}{e.disclaimer}
      </p>
    </aside>
  );
}
