import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import RiskLimitsCard from "../components/RiskLimitsCard";
import { Card } from "../components/ui";
import type { GuardianStatus, MarketEvent, RiskCeilings, RiskConfig } from "../types";

import { PageHeader } from "../components/primitives";
const FIELDS: { key: keyof RiskConfig; label: string; step?: string; ceiling?: string }[] = [
  { key: "capital", label: "Capital (₹)" },
  { key: "risk_per_trade_pct", label: "Risk per trade (%)", step: "0.1", ceiling: "risk_per_trade_pct" },
  { key: "max_daily_loss_pct", label: "Max daily loss (%)", step: "0.1", ceiling: "max_daily_loss_pct" },
  { key: "max_trades_per_day", label: "Max trades / day" },
  { key: "max_open_positions", label: "Max open positions" },
  { key: "max_consecutive_losses", label: "Max consecutive losses" },
  { key: "min_risk_reward", label: "Min risk/reward", step: "0.1" },
  { key: "lot_size", label: "Lot size" },
];

// Phase V1: the Risk Guardian's rules, enforced by the engine on every entry.
const GUARDIAN_FIELDS: { key: keyof RiskConfig; label: string; step?: string; ceiling?: string; help: string }[] = [
  { key: "max_portfolio_risk_pct", label: "Max portfolio risk (% of capital)", step: "0.5", ceiling: "max_portfolio_risk_pct", help: "Loss if every open stop hits plus the new trade's max loss; index positions share one bucket (R4)." },
  { key: "stop_cooldown_minutes", label: "Cool-down after a stop-out (min)", help: "No re-entry in the same underlying this long after a stop (R10)." },
  { key: "dd_level_1_pct", label: "Drawdown: halve size at (%)", step: "0.5", help: "This far below the equity peak the risk per trade is halved (P2)." },
  { key: "dd_level_2_pct", label: "Drawdown: pause entries at (%)", step: "0.5", ceiling: "dd_level_2_pct", help: "This far below the peak new entries pause until equity recovers (P3)." },
  { key: "event_size_cut_pct", label: "Event-day size cut (%)", step: "5", help: "Default cut for a SIZE_CUT market event (M8)." },
];

const EVENT_KINDS = ["BUDGET", "RBI_POLICY", "EXPIRY", "RESULTS", "FED", "ELECTION", "OTHER"];

export default function RiskManagementPage() {
  const { user, loading: authLoading } = useAuth();
  const [config, setConfig] = useState<RiskConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ceilings, setCeilings] = useState<RiskCeilings | null>(null);
  const [guardian, setGuardian] = useState<GuardianStatus | null>(null);
  const [events, setEvents] = useState<MarketEvent[]>([]);
  const [evDate, setEvDate] = useState(new Date().toISOString().slice(0, 10));
  const [evUnderlying, setEvUnderlying] = useState("");
  const [evKind, setEvKind] = useState("OTHER");
  const [evAction, setEvAction] = useState<"BLOCK" | "SIZE_CUT">("SIZE_CUT");
  const [evCut, setEvCut] = useState("");
  const [evStart, setEvStart] = useState("");
  const [evEnd, setEvEnd] = useState("");
  const [evDesc, setEvDesc] = useState("");

  function refreshGuardian() {
    api.guardianStatus().then(setGuardian).catch(() => {});
    api.listMarketEvents().then(setEvents).catch(() => {});
  }

  useEffect(() => {
    if (!user) return;
    api.getRiskSettings().then(setConfig).catch((e) => setError(String(e)));
    api.riskCeilings().then(setCeilings).catch(() => {});
    refreshGuardian();
  }, [user]);

  async function addEvent() {
    setError(null);
    try {
      await api.createMarketEvent({
        event_date: evDate, underlying: evUnderlying || null, kind: evKind, action: evAction,
        size_cut_pct: evAction === "SIZE_CUT" && evCut ? Number(evCut) : null,
        start_time: evStart || null, end_time: evEnd || null, description: evDesc,
        global_event: user?.role === "SUPER_ADMIN" && evUnderlying.toUpperCase() === "*" ? true : false,
      });
      setEvDesc("");
      refreshGuardian();
    } catch (e) {
      setError(String(e));
    }
  }

  async function removeEvent(id: number) {
    try {
      await api.deleteMarketEvent(id);
      refreshGuardian();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleSave() {
    if (!config) return;
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      const saved = await api.updateRiskSettings(config);
      setConfig(saved);
      setMessage("Saved. Every paper and live entry now uses these limits and guardian rules.");
      refreshGuardian();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <PageHeader title="Risk Management" />
        <Card>
          <p className="text-sm text-fg-muted">
            Log in from the Account tab to configure your own risk limits. Every paper (and,
            once wired, live) order passes through the Risk Engine using these settings before
            it can execute.
          </p>
        </Card>
      </div>
    );
  }

  if (!config) {
    return <div className="text-sm text-fg-muted">Loading…</div>;
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Risk Management" description="Position sizing and daily-loss/trade-count/consecutive-loss guards the Risk Engine checks before every paper-execute order. Anonymous calls always use the platform default; these apply only to your own logged-in orders." />

      <Card>
        <div className="grid sm:grid-cols-2 gap-4">
          {FIELDS.map((f) => (
            <div key={f.key}>
              <label className="block text-xs text-fg-muted mb-1">{f.label}{f.ceiling && ceilings ? <span className="text-[10px] text-fg-muted"> · ceiling {ceilings[f.ceiling]}</span> : null}</label>
              <input
                type="number"
                step={f.step ?? "1"}
                className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
                value={config[f.key]}
                onChange={(e) => setConfig({ ...config, [f.key]: Number(e.target.value) })}
              />
            </div>
          ))}
        </div>

        <div className="mt-4 text-xs font-bold uppercase tracking-wide text-fg-muted">Risk Guardian rules</div>
        <p className="text-[11px] text-fg-muted mb-2">Enforced by the engine on every entry, paper or live, whatever built the strategy. Values cannot exceed the platform ceilings.</p>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {GUARDIAN_FIELDS.map((f) => (
            <div key={f.key}>
              <label className="block text-xs text-fg-muted mb-1" title={f.help}>{f.label}{f.ceiling && ceilings ? <span className="text-[10px] text-fg-muted"> · ceiling {ceilings[f.ceiling]}</span> : null}</label>
              <input
                type="number"
                step={f.step ?? "1"}
                min={0}
                className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
                value={config[f.key]}
                onChange={(e) => setConfig({ ...config, [f.key]: Number(e.target.value) })}
              />
              <div className="text-[10px] text-fg-muted mt-0.5">{f.help}</div>
            </div>
          ))}
        </div>

        {error && <div className="mt-3 text-sm text-down">{error}</div>}
        {message && <div className="mt-3 text-sm text-up">{message}</div>}

        <button
          onClick={handleSave}
          disabled={saving}
          className="mt-4 rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save risk settings"}
        </button>
      </Card>

      {guardian && (
        <Card title="Risk Guardian status">
          <div className="grid md:grid-cols-2 gap-4 text-xs">
            {(["PAPER", "LIVE"] as const).map((mode) => {
              const m = guardian.modes[mode];
              const colour = m.state === "paused" ? "text-down" : m.state === "reduced" ? "text-warn" : "text-up";
              return (
                <div key={mode} className="rounded-lg border border-border bg-surface-2/40 p-3 space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-sm">{mode}</span>
                    <span className={`font-bold uppercase ${colour}`}>{m.state}{m.state !== "normal" ? ` · size x${m.size_multiplier}` : ""}</span>
                  </div>
                  <div className="text-fg-muted">Equity {m.equity.toLocaleString()} · peak {m.peak.toLocaleString()} · drawdown <span className={m.drawdown_pct >= guardian.settings.dd_level_1_pct ? "text-warn" : "text-fg"}>{m.drawdown_pct.toFixed(1)}%</span> over {m.closed_trades} closed trade(s)</div>
                  <div className="text-fg-muted">Open risk at the stops {m.open_risk_total.toLocaleString()} ({m.open_risk_pct}% of capital, cap {m.portfolio_cap.toLocaleString()})</div>
                  {Object.keys(m.open_risk_by_bucket).length > 0 && (
                    <div className="text-fg-muted">By bucket: {Object.entries(m.open_risk_by_bucket).map(([b, v]) => `${b} ${v.toLocaleString()}`).join(" · ")}</div>
                  )}
                  {m.cooldowns.length > 0 && (
                    <div className="text-warn">Cool-down: {m.cooldowns.map((c) => `${c.underlying} (${c.minutes_left} min left)`).join(", ")}</div>
                  )}
                </div>
              );
            })}
          </div>
          <div className="mt-3 text-[11px] text-fg-muted">
            The drawdown ladder halves the risk per trade at {guardian.settings.dd_level_1_pct}% below the equity peak and pauses new entries at {guardian.settings.dd_level_2_pct}%; the multiplier never rises above 1 after a winning streak. Exits are never blocked.
          </div>
        </Card>
      )}

      <Card title="Market events (blackouts and size cuts)">
        <p className="text-xs text-fg-muted mb-2">Budget, RBI policy, expiry, results: on the day (and inside the time window, if given) a BLOCK event refuses new entries in the named underlying, a SIZE_CUT event shrinks the risk per trade. Leave the underlying empty for every symbol, or write INDEX for NIFTY/BANKNIFTY/SENSEX together. Global events are kept by the platform operator.</p>
        <div className="grid sm:grid-cols-3 lg:grid-cols-8 gap-2 text-xs">
          <input type="date" className="rounded bg-surface-2 border border-border px-2 py-1" value={evDate} onChange={(e) => setEvDate(e.target.value)} />
          <input className="rounded bg-surface-2 border border-border px-2 py-1" placeholder="underlying / INDEX / empty" value={evUnderlying} onChange={(e) => setEvUnderlying(e.target.value)} />
          <select className="rounded bg-surface-2 border border-border px-2 py-1" value={evKind} onChange={(e) => setEvKind(e.target.value)}>
            {EVENT_KINDS.map((k) => <option key={k} value={k}>{k.replace(/_/g, " ")}</option>)}
          </select>
          <select className="rounded bg-surface-2 border border-border px-2 py-1" value={evAction} onChange={(e) => setEvAction(e.target.value as "BLOCK" | "SIZE_CUT")}>
            <option value="SIZE_CUT">Size cut</option>
            <option value="BLOCK">Block entries</option>
          </select>
          <input type="number" min={0} max={100} disabled={evAction !== "SIZE_CUT"} className="rounded bg-surface-2 border border-border px-2 py-1 disabled:opacity-40" placeholder={`cut % (${config.event_size_cut_pct})`} value={evCut} onChange={(e) => setEvCut(e.target.value)} />
          <input className="rounded bg-surface-2 border border-border px-2 py-1" placeholder="from HH:MM" value={evStart} onChange={(e) => setEvStart(e.target.value)} />
          <input className="rounded bg-surface-2 border border-border px-2 py-1" placeholder="to HH:MM" value={evEnd} onChange={(e) => setEvEnd(e.target.value)} />
          <button onClick={addEvent} className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-3 py-1">Add</button>
          <input className="rounded bg-surface-2 border border-border px-2 py-1 sm:col-span-3 lg:col-span-8" placeholder="description (optional)" value={evDesc} onChange={(e) => setEvDesc(e.target.value)} />
        </div>
        {events.length === 0 ? (
          <div className="mt-3 text-xs text-fg-muted">No events in the next 30 days.</div>
        ) : (
          <table className="w-full text-xs mt-3">
            <thead className="text-fg-muted uppercase text-[10px] tracking-wide"><tr className="text-left"><th className="py-1 pr-3">Date</th><th className="py-1 pr-3">Scope</th><th className="py-1 pr-3">Kind</th><th className="py-1 pr-3">Action</th><th className="py-1 pr-3">Window</th><th className="py-1 pr-3">Description</th><th className="py-1 text-right"></th></tr></thead>
            <tbody>
              {events.map((ev) => (
                <tr key={ev.id} className="border-t border-border">
                  <td className="py-1.5 pr-3 font-mono">{ev.event_date}</td>
                  <td className="py-1.5 pr-3">{ev.underlying ?? "all"}{ev.global ? <span className="ml-1 text-[10px] text-fg">global</span> : null}</td>
                  <td className="py-1.5 pr-3">{ev.kind.replace(/_/g, " ")}</td>
                  <td className={`py-1.5 pr-3 font-bold ${ev.action === "BLOCK" ? "text-down" : "text-warn"}`}>{ev.action === "BLOCK" ? "block" : `cut ${ev.size_cut_pct ?? config.event_size_cut_pct}%`}</td>
                  <td className="py-1.5 pr-3 text-fg-muted">{ev.start_time || ev.end_time ? `${ev.start_time ?? "00:00"}-${ev.end_time ?? "23:59"}` : "all day"}</td>
                  <td className="py-1.5 pr-3 text-fg-muted">{ev.description}</td>
                  <td className="py-1.5 text-right">{(!ev.global || user?.role === "SUPER_ADMIN") && <button onClick={() => removeEvent(ev.id)} className="text-down hover:underline">remove</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <RiskLimitsCard canEdit={user?.role === "OWNER" || user?.role === "SUPER_ADMIN"} />
    </div>
  );
}
