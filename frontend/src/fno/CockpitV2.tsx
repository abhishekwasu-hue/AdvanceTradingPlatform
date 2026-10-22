import { Bell, CalendarClock, Gauge, Layers } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { AlertRule } from "../alerts/rules";
import { CardV2, toneClass } from "../dashboard/CardV2";
import { signTone } from "../dashboard/model";
import { TileV2, TilesV2 } from "../trading/PartsV2";
import type { PositionGreeks } from "../types";
import { EMPTY, formatINR, formatNumber, formatSignedINR } from "../ui/format";
import { RiskLineV2, useRiskBudget } from "./InsightsV2";
import {
  COCKPIT_NOTE, alertsSet, cockpitRiskLine, summarise, todayIST, upcomingExpiries, type CockpitSummary, type CockpitUnit, type ExpiryLine,
} from "./cockpit";
import type { RiskLine } from "./insights";

/**
 * F&O workspace 8b-i: the F&O cockpit on the positions page (pure parts in cockpit.ts) - the user's open PAPER F&O
 * positions on one read-only card: the lines he holds with their P&L, the premium kept so far, the Greeks added up, the
 * most they can lose together at expiry beside his own limits, the expiries coming up and the alerts he set. It reads
 * (the Greeks once when it opens, his risk settings once, his alert rules once) and never writes: no control on it
 * trades, and each position keeps its own Exit in the list below it (ADR-0004).
 */
export interface CockpitApi {
  greeks: () => Promise<PositionGreeks>;
  alertRules: () => Promise<AlertRule[]>;
}
const defaultApi: CockpitApi = { greeks: () => api.positionGreeks(), alertRules: () => api.listAlertRules() };

export default function CockpitV2({ units, alertsOn, cockpitApi = defaultApi }: { units: readonly CockpitUnit[]; alertsOn: boolean; cockpitApi?: CockpitApi }) {
  const on = units.length > 0;
  const hasOptions = units.some((u) => u.legs.some((l) => l.right !== "FUT"));
  const [greeks, setGreeks] = useState<PositionGreeks | null>(null);
  const [rules, setRules] = useState<AlertRule[] | null>(null);
  const budget = useRiskBudget(on);
  useEffect(() => {
    if (!on || !hasOptions) return undefined;
    let alive = true;
    cockpitApi.greeks().then((g) => { if (alive) setGreeks(g && g.net ? g : null); }).catch(() => { if (alive) setGreeks(null); });
    return () => { alive = false; };
  }, [on, hasOptions, cockpitApi]);
  useEffect(() => {
    if (!on || !alertsOn) return undefined;
    let alive = true;
    cockpitApi.alertRules().then((r) => { if (alive) setRules(Array.isArray(r) ? r : null); }).catch(() => { if (alive) setRules(null); });
    return () => { alive = false; };
  }, [on, alertsOn, cockpitApi]);
  const summary = useMemo(() => summarise(units), [units]);
  const expiries = useMemo(() => upcomingExpiries(units, todayIST()), [units]);
  const alerts = useMemo(() => alertsSet(rules, units), [rules, units]);
  if (!on) return null;
  return (
    <CockpitView units={units} summary={summary} greeks={greeks} line={cockpitRiskLine(summary, budget)} expiries={expiries}
      alerts={alertsOn ? alerts : null} />
  );
}

/** The card for given figures (no reads of its own), so the tests render every state directly. */
export function CockpitView({ units, summary, greeks, line, expiries, alerts }: {
  units: readonly CockpitUnit[]; summary: CockpitSummary; greeks: PositionGreeks | null; line: RiskLine | null; expiries: readonly ExpiryLine[];
  alerts: readonly AlertRule[] | null;
}) {
  const p = summary.premium;
  return (
    <CardV2 title={<span className="inline-flex items-center gap-space-2"><Gauge size={18} aria-hidden className="text-brand" />F&amp;O at a glance</span>}
      count={summary.positions} testId="fno-cockpit">
      <div className="space-y-space-4" data-cockpit>
        <TilesV2>
          <TileV2 testId="cockpit-pnl" label="Open P&L" value={formatSignedINR(summary.pnl)} tone={signTone(summary.pnl)}
            sub={`${summary.lines} ${summary.lines === 1 ? "line" : "lines"} · ${summary.positions} ${summary.positions === 1 ? "leg" : "legs"}`} />
          <TileV2 testId="cockpit-kept" label="Premium kept so far" value={p ? (p.kept === null ? EMPTY : formatSignedINR(p.kept)) : EMPTY}
            sub={p ? `${p.pct === null ? "waiting for live prices" : `${formatNumber(p.pct, 1)}%`} of ${formatINR(p.received)} received` : "no premium received"} />
          <TileV2 testId="cockpit-theta" label="Theta a day" value={greeks ? formatSignedINR(greeks.net.net_theta) : EMPTY}
            sub={greeks ? "time decay, all options together" : undefined} />
          <TileV2 testId="cockpit-worst" label="Most it can lose" value={summary.unbounded ? "No fixed limit" : formatINR(summary.worst)}
            sub="at expiry, all lines together" />
        </TilesV2>

        <RiskLineV2 line={line} />

        {greeks && (
          <dl className="grid grid-cols-2 gap-x-space-4 gap-y-space-1 text-t13 sm:grid-cols-4" data-cockpit="greeks">
            <Figure label="Net delta" value={formatNumber(greeks.net.net_delta, 2)} />
            <Figure label="Net gamma" value={formatNumber(greeks.net.net_gamma, 4)} />
            <Figure label="Net theta" value={formatNumber(greeks.net.net_theta, 2)} />
            <Figure label="Net vega" value={formatNumber(greeks.net.net_vega, 2)} />
          </dl>
        )}

        <div className="grid gap-space-4 md:grid-cols-3">
          <section aria-label="What you hold" data-cockpit="lines">
            <h3 className="mb-space-2 flex items-center gap-space-2 text-t14 font-semibold text-fg"><Layers size={16} aria-hidden className="text-fg-muted" />What you hold</h3>
            <ul className="divide-y divide-subtle text-t13">
              {units.map((u) => (
                <li key={u.key} className="flex items-baseline justify-between gap-space-2 py-space-1">
                  <span className="min-w-0 truncate text-fg">{u.title}</span>
                  <span className={`shrink-0 font-tabular font-semibold ${toneClass(signTone(u.pnl))}`}>{u.pnl === null ? EMPTY : formatSignedINR(u.pnl)}</span>
                </li>
              ))}
            </ul>
          </section>
          <section aria-label="Coming expiries" data-cockpit="expiries">
            <h3 className="mb-space-2 flex items-center gap-space-2 text-t14 font-semibold text-fg"><CalendarClock size={16} aria-hidden className="text-fg-muted" />Coming expiries</h3>
            <ul className="divide-y divide-subtle text-t13">
              {expiries.map((e) => (
                <li key={e.expiry} className="py-space-1" data-expiry={e.expiry}>
                  <span className={e.daysLeft <= 1 ? "font-semibold text-warn" : "text-fg"}>{e.when}</span>
                  <span className="block text-t12 text-fg-muted">{e.underlyings.join(", ")} · {e.legs} {e.legs === 1 ? "leg" : "legs"}</span>
                </li>
              ))}
            </ul>
          </section>
          {alerts && (
            <section aria-label="Your alerts" data-cockpit="alerts">
              <h3 className="mb-space-2 flex items-center gap-space-2 text-t14 font-semibold text-fg"><Bell size={16} aria-hidden className="text-fg-muted" />Your alerts</h3>
              {alerts.length === 0 ? (
                <p className="text-t13 text-fg-muted">No alert set on these yet. "Tell me when" on a position sets one.</p>
              ) : (
                <ul className="divide-y divide-subtle text-t13">
                  {alerts.map((a) => <li key={a.id} className="py-space-1 text-fg" data-alert={a.id}>{a.name}</li>)}
                </ul>
              )}
            </section>
          )}
        </div>
        <p className="text-t12 text-fg-muted">{COCKPIT_NOTE}</p>
      </div>
    </CardV2>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-space-2 sm:block">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="font-tabular font-semibold text-fg">{value}</dd>
    </div>
  );
}
