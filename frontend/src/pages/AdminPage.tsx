import { Bot, Building2, OctagonX, ScrollText, ShieldEllipsis, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import ExportCard from "../components/ExportCard";
import { Card, StatTile } from "../components/ui";
import GoLiveChecklist from "../components/GoLiveChecklist";
import HolidaysCard from "../components/HolidaysCard";
import type { AdminOverview, AdminPlan, AdminTenantDetail, AdminTenantSummary, EncryptionStatus, FeatureFlags, Incident, PlatformAuditLog, RiskCeilings, SystemStatus } from "../types";

import { PageHeader } from "../components/primitives";
const STATUSES = ["active", "suspended"];

export default function AdminPage() {
  const { user, loading: authLoading } = useAuth();
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [plans, setPlans] = useState<AdminPlan[]>([]);
  const [tenants, setTenants] = useState<AdminTenantSummary[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<AdminTenantDetail | null>(null);
  const [logs, setLogs] = useState<PlatformAuditLog[]>([]);
  const [killReason, setKillReason] = useState("");
  const [controls, setControls] = useState<SystemStatus | null>(null);
  const [maintMsg, setMaintMsg] = useState("");
  const [brokerList, setBrokerList] = useState("");
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [flags, setFlags] = useState<FeatureFlags | null>(null);
  const [flagTenants, setFlagTenants] = useState<Record<string, string>>({});
  const [encryption, setEncryption] = useState<EncryptionStatus | null>(null);
  const [ceilings, setCeilings] = useState<RiskCeilings | null>(null);
  const [ceilingDraft, setCeilingDraft] = useState<Record<string, string>>({});
  const [incTitle, setIncTitle] = useState("");
  const [incSeverity, setIncSeverity] = useState("WARNING");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isAdmin = user?.role === "SUPER_ADMIN";

  function refresh() {
    if (!isAdmin) return;
    api.adminOverview().then(setOverview).catch((e) => setError(String(e)));
    api.adminControls().then((c) => { setControls(c); setBrokerList(c.disabled_brokers.join(", ")); setMaintMsg(c.maintenance_message ?? ""); }).catch(() => {});
    api.adminIncidents().then(setIncidents).catch(() => {});
    api.adminFlags().then((f) => { setFlags(f); setFlagTenants(Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.tenants.join(", ")]))); }).catch(() => {});
    api.adminEncryptionStatus().then(setEncryption).catch(() => {});
    api.adminRiskCeilings().then((c) => { setCeilings(c); setCeilingDraft(Object.fromEntries(Object.entries(c).map(([k, v]) => [k, String(v)]))); }).catch(() => {});
    api.adminPlans().then(setPlans).catch(() => {});
    api.adminTenants(query).then(setTenants).catch((e) => setError(String(e)));
    api.adminAuditLogs(selected?.id).then(setLogs).catch(() => {});
    if (selected) api.adminTenant(selected.id).then(setSelected).catch(() => {});
  }

  useEffect(refresh, [user, query]); // eslint-disable-line react-hooks/exhaustive-deps

  async function act(label: string, fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await fn();
      setMessage(label);
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (authLoading) return null;
  if (!isAdmin) {
    return (
      <div className="space-y-4">
        <PageHeader title="Admin Console" />
        <Card><p className="text-sm text-fg-muted">Platform administrators only.</p></Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Admin Console" description="Every organisation on the platform: plans, suspension, what the worker is running, the platform-wide audit trail, and the global kill switch. Every change here lands on the affected tenant's own audit trail and notifies them." />

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <StatTile icon={Building2} label="Tenants" value={overview?.tenants_total ?? "…"} accentClass="text-down" />
        <StatTile icon={Users} label="Active users" value={overview?.users_total ?? "…"} accentClass="text-fg" />
        <StatTile icon={ShieldEllipsis} label="Active / LIVE deployments" value={overview ? `${overview.active_deployments} / ${overview.live_deployments}` : "…"} accentClass="text-fg" />
        <StatTile icon={ScrollText} label="Open positions (LIVE)" value={overview ? `${overview.open_positions} (${overview.open_live_positions})` : "…"} accentClass="text-fg" />
        <StatTile icon={Bot} label="Worker" value={overview === null ? "…" : overview.worker_running ? "Running" : "Down"} tone={overview === null ? "default" : overview.worker_running ? "up" : "down"} />
      </div>

      {error && <div className="text-sm text-down">{error}</div>}
      {message && <div className="text-sm text-up">{message}</div>}

      <GoLiveChecklist kind="platform" />

      <HolidaysCard />

      <Card title="Global kill switch">
        {overview?.global_kill_switch_engaged ? (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="flex items-center gap-1.5 text-down font-bold"><OctagonX size={16} /> ENGAGED</span>
            <span className="text-fg-muted">{overview.global_kill_switch_reason}</span>
            <button disabled={busy} onClick={() => act("Global kill switch disengaged.", api.disengageGlobalKillSwitch)} className="rounded border border-border hover:bg-surface-2 text-fg px-3 py-1 text-xs">Disengage</button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-fg-muted">Blocks every new order on every tenant until disengaged. Open positions keep being monitored.</span>
            <input className="rounded bg-surface-2 border border-border px-2 py-1 text-xs w-56" placeholder="reason" value={killReason} onChange={(e) => setKillReason(e.target.value)} />
            <button disabled={busy || !killReason} onClick={() => act("Global kill switch engaged.", () => api.engageGlobalKillSwitch(killReason))} className="rounded bg-down text-surface hover:brightness-110 font-semibold px-3 py-1 text-xs disabled:opacity-50">Engage</button>
          </div>
        )}
      </Card>

      <Card title="Platform controls">
        <div className="grid lg:grid-cols-2 gap-4 text-xs">
          <div className="rounded-lg border border-border bg-surface-2/40 p-3 space-y-2">
            <div className="font-bold text-sm">Maintenance mode {controls?.maintenance_mode ? <span className="text-warn">ON</span> : <span className="text-fg-muted">off</span>}</div>
            <p className="text-fg-muted">Planned pause: no new entries on any tenant (paper or live); exits, monitoring and the API keep running; every user sees the message. The kill switch above is the unplanned emergency stop.</p>
            <input className="w-full rounded bg-surface-2 border border-border px-2 py-1" placeholder="Message shown to users" value={maintMsg} onChange={(e) => setMaintMsg(e.target.value)} />
            <div className="flex gap-2">
              {controls?.maintenance_mode
                ? <button disabled={busy} onClick={() => act("Maintenance mode off.", () => api.adminSetMaintenance(false).then(setControls))} className="rounded border border-border hover:bg-surface-2 text-fg px-3 py-1">Turn off</button>
                : <button disabled={busy} onClick={() => act("Maintenance mode on.", () => api.adminSetMaintenance(true, maintMsg).then(setControls))} className="rounded bg-warn text-surface hover:brightness-110 font-semibold px-3 py-1">Turn on</button>}
            </div>
          </div>
          <div className="rounded-lg border border-border bg-surface-2/40 p-3 space-y-2">
            <div className="font-bold text-sm">Disabled brokers {controls && controls.disabled_brokers.length > 0 && <span className="text-down">{controls.disabled_brokers.join(", ")}</span>}</div>
            <p className="text-fg-muted">LIVE entries through a listed broker are refused platform-wide (its API is degraded, or credentials are being rotated). Exits still go through. Comma-separated names: upstox, zerodha, shoonya.</p>
            <input className="w-full rounded bg-surface-2 border border-border px-2 py-1" placeholder="upstox, zerodha" value={brokerList} onChange={(e) => setBrokerList(e.target.value)} />
            <button disabled={busy} onClick={() => act("Disabled brokers updated.", () => api.adminSetDisabledBrokers(brokerList.split(",").map((b) => b.trim()).filter(Boolean)).then(setControls))} className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-3 py-1">Save</button>
          </div>
        </div>
      </Card>

      <Card title="Risk ceilings (Risk Guardian)">
        <p className="text-xs text-fg-muted mb-3">Hard limits every tenant's risk settings stay under: a tenant cannot save a value above a ceiling, and the engine clamps older settings at runtime. Risk per trade 2% is the spec's hard ceiling; the minimum cool-down forces every tenant to wait at least this long after a stop-out.</p>
        <div className="grid sm:grid-cols-3 lg:grid-cols-5 gap-3 text-xs">
          {ceilings && Object.keys(ceilings).map((key) => (
            <div key={key}>
              <label className="block text-[10px] text-fg-muted mb-0.5 font-mono">{key}</label>
              <input type="number" step="0.1" min={0} className="w-full rounded bg-surface-2 border border-border px-2 py-1" value={ceilingDraft[key] ?? ""} onChange={(e) => setCeilingDraft({ ...ceilingDraft, [key]: e.target.value })} />
            </div>
          ))}
        </div>
        <button disabled={busy || !ceilings} onClick={() => act("Risk ceilings saved.", () => api.adminSetRiskCeilings(Object.fromEntries(Object.entries(ceilingDraft).map(([k, v]) => [k, Number(v)]))).then(setCeilings))} className="mt-3 rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-3 py-1 text-xs disabled:opacity-50">Save ceilings</button>
      </Card>

      <Card title="Feature flags">
        <p className="text-xs text-fg-muted mb-3">Kill flags: every feature is on unless turned off here. Turning one off with a tenant allow-list keeps it on for those tenants only (staged rollouts, beta access). Exits and PAPER trading are never behind a flag.</p>
        <table className="w-full text-xs">
          <thead className="text-fg-muted uppercase text-[10px] tracking-wide"><tr className="text-left"><th className="py-1 pr-3">Flag</th><th className="py-1 pr-3">State</th><th className="py-1 pr-3">Allow-listed tenant ids</th><th className="py-1 text-right">Actions</th></tr></thead>
          <tbody>
            {flags && Object.entries(flags).map(([name, f]) => (
              <tr key={name} className="border-t border-border">
                <td className="py-1.5 pr-3"><div className="font-mono text-fg">{name}</div><div className="text-[10px] text-fg-muted">{f.description}</div></td>
                <td className={`py-1.5 pr-3 font-bold ${f.on ? "text-up" : "text-down"}`}>{f.on ? "ON" : `OFF${f.tenants.length ? ` (${f.tenants.length} allowed)` : ""}`}</td>
                <td className="py-1.5 pr-3"><input className="w-full rounded bg-surface-2 border border-border px-2 py-1" placeholder="e.g. 3, 17" value={flagTenants[name] ?? ""} onChange={(e) => setFlagTenants({ ...flagTenants, [name]: e.target.value })} /></td>
                <td className="py-1.5 text-right whitespace-nowrap">
                  {f.on
                    ? <button disabled={busy} onClick={() => act(`${name} turned off.`, () => api.adminSetFlag(name, false, (flagTenants[name] ?? "").split(",").map((t) => parseInt(t.trim(), 10)).filter((n) => !Number.isNaN(n))).then(setFlags))} className="rounded border border-down/40 text-down hover:bg-down/10 px-2 py-0.5">Turn off</button>
                    : <>
                        <button disabled={busy} onClick={() => act(`${name} allow-list saved.`, () => api.adminSetFlag(name, false, (flagTenants[name] ?? "").split(",").map((t) => parseInt(t.trim(), 10)).filter((n) => !Number.isNaN(n))).then(setFlags))} className="rounded border border-border hover:bg-surface-2 text-fg px-2 py-0.5 mr-1">Save list</button>
                        <button disabled={busy} onClick={() => act(`${name} turned on.`, () => api.adminSetFlag(name, true).then(setFlags))} className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-2 py-0.5">Turn on</button>
                      </>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {encryption && (
          <div className="mt-3 text-[11px] text-fg-muted">
            Secrets at rest: {encryption.tenant_keys} tenant data keys, {encryption.secrets_total - encryption.secrets_legacy}/{encryption.secrets_total} secrets under tenant keys
            {encryption.secrets_legacy > 0 && <span className="text-warn"> - {encryption.secrets_legacy} still under the master key (run scripts/reencrypt_secrets.py reencrypt)</span>}.
          </div>
        )}
      </Card>

      <Card title={`Incidents (${incidents.filter((i) => i.status !== "RESOLVED").length} open)`}>
        <div className="flex flex-wrap gap-2 text-xs mb-3">
          <input className="rounded bg-surface-2 border border-border px-2 py-1 w-72" placeholder="Title" value={incTitle} onChange={(e) => setIncTitle(e.target.value)} />
          <select className="rounded bg-surface-2 border border-border px-1 py-1" value={incSeverity} onChange={(e) => setIncSeverity(e.target.value)}>
            {["WARNING", "CRITICAL", "EMERGENCY"].map((s) => <option key={s}>{s}</option>)}
          </select>
          <button disabled={busy || incTitle.length < 3} onClick={() => act("Incident opened.", () => api.adminCreateIncident({ title: incTitle, severity: incSeverity }).then(() => { setIncTitle(""); return api.adminIncidents().then(setIncidents); }))} className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-3 py-1">Open incident</button>
          <span className="text-fg-muted">The global kill switch opens one automatically. Resolve with a root cause; downtime is measured from the start.</span>
        </div>
        {incidents.length === 0 ? <div className="text-xs text-fg-muted">No incidents recorded.</div> : (
          <table className="w-full text-xs"><tbody>
            {incidents.slice(0, 15).map((i) => (
              <tr key={i.id} className="border-t border-border/60">
                <td className="py-1 text-fg-muted">#{i.id} {i.started_at ? new Date(i.started_at).toLocaleString() : ""}</td>
                <td className={`py-1 font-bold ${i.severity === "EMERGENCY" ? "text-down" : i.severity === "CRITICAL" ? "text-warn" : "text-fg-muted"}`}>{i.severity}</td>
                <td className="py-1">{i.title} <span className="text-fg-muted">({i.source})</span>{i.root_cause && <div className="text-fg-muted">cause: {i.root_cause}</div>}</td>
                <td className="py-1">{i.status}{i.downtime_minutes != null && <span className="text-fg-muted"> · {i.downtime_minutes} min</span>}</td>
                <td className="py-1 text-right">
                  {i.status !== "RESOLVED" && (
                    <button disabled={busy} onClick={() => { const cause = window.prompt("Root cause") ?? ""; const actions = window.prompt("Actions taken") ?? ""; void act("Incident resolved.", () => api.adminUpdateIncident(i.id, { status: "RESOLVED", root_cause: cause, actions_taken: actions }).then(() => api.adminIncidents().then(setIncidents))); }} className="text-brand hover:underline">Resolve</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody></table>
        )}
      </Card>

      <Card title={`Tenants (${tenants.length})`}>
        <input className="w-full sm:w-80 rounded bg-surface-2 border border-border px-2 py-1.5 text-sm mb-3" placeholder="search by name or member email" value={query} onChange={(e) => setQuery(e.target.value)} />
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-fg-muted uppercase text-[10px] tracking-wide">
              <tr className="text-left"><th className="py-1 pr-3">#</th><th className="py-1 pr-3">Organisation</th><th className="py-1 pr-3">Owners</th><th className="py-1 pr-3">Plan</th><th className="py-1 pr-3">Status</th><th className="py-1 pr-3">Members</th><th className="py-1 pr-3">Deployments (LIVE)</th><th className="py-1 pr-3">Open</th><th /></tr>
            </thead>
            <tbody>
              {tenants.map((t) => (
                <tr key={t.id} className={`border-t border-border ${selected?.id === t.id ? "bg-surface-2/60" : ""}`}>
                  <td className="py-1.5 pr-3 text-fg-muted">{t.id}</td>
                  <td className="py-1.5 pr-3 text-fg font-medium">{t.name}</td>
                  <td className="py-1.5 pr-3 text-fg-muted">{t.owners.join(", ") || "-"}</td>
                  <td className="py-1.5 pr-3">
                    <select className="rounded bg-surface-2 border border-border px-1 py-0.5 text-xs" value={t.plan} disabled={busy} onChange={(e) => act(`${t.name}: plan set to ${e.target.value}.`, () => api.adminUpdateTenant(t.id, { plan: e.target.value }))}>
                      {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                  </td>
                  <td className="py-1.5 pr-3">
                    <select className={`rounded bg-surface-2 border px-1 py-0.5 text-xs ${t.status === "active" ? "border-up/40 text-up" : "border-down/40 text-down"}`} value={t.status} disabled={busy} onChange={(e) => {
                      const reason = e.target.value === "suspended" ? window.prompt("Reason for suspension (sent to the tenant):") ?? "" : "";
                      if (e.target.value === "suspended" && !reason) return;
                      void act(`${t.name}: status set to ${e.target.value}.`, () => api.adminUpdateTenant(t.id, { status: e.target.value, reason }));
                    }}>
                      {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </td>
                  <td className="py-1.5 pr-3 font-tabular">{t.members}</td>
                  <td className="py-1.5 pr-3 font-tabular">{t.active_deployments} ({t.live_deployments})</td>
                  <td className="py-1.5 pr-3 font-tabular">{t.open_positions}</td>
                  <td className="py-1.5 text-right"><button onClick={() => api.adminTenant(t.id).then((d) => { setSelected(d); api.adminAuditLogs(d.id).then(setLogs).catch(() => {}); }).catch((e) => setError(String(e)))} className="text-brand hover:underline">details</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {selected && (
        <Card title={`Tenant #${selected.id}: ${selected.name}`}>
          <div className="grid md:grid-cols-3 gap-4 text-xs">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-fg-muted mb-1">Usage / limits</div>
              {Object.entries(selected.usage).map(([k, v]) => (
                <div key={k} className="flex justify-between border-t border-border py-1"><span className="text-fg-muted">{k.replace("_", " ")}</span><span className="font-tabular text-fg">{v} / {String(selected.limits[k] ?? "-")}</span></div>
              ))}
              <div className="flex justify-between border-t border-border py-1"><span className="text-fg-muted">live trading</span><span className="text-fg">{selected.limits.live_trading ? "yes" : "no"}</span></div>
              <div className="flex justify-between border-t border-border py-1"><span className="text-fg-muted">tenant kill switch</span><span className={selected.tenant_kill_switch_engaged ? "text-down" : "text-fg"}>{selected.tenant_kill_switch_engaged ? "engaged" : "off"}</span></div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-fg-muted mb-1">Users</div>
              {selected.users.map((u) => (
                <div key={u.id} className="flex justify-between border-t border-border py-1"><span className={u.is_active ? "text-fg" : "text-fg-muted line-through"}>{u.email}</span><span className="text-fg-muted">{u.role}</span></div>
              ))}
              <div className="text-[10px] uppercase tracking-wider text-fg-muted mt-3 mb-1">Brokers</div>
              {selected.brokers.length === 0 && <div className="text-fg-muted">none stored</div>}
              {selected.brokers.map((b) => (
                <div key={b.broker_name} className="flex justify-between border-t border-border py-1"><span className="capitalize text-fg">{b.broker_name}</span><span className={b.token_status === "VALID" ? "text-up" : "text-warn"}>{b.token_status}</span></div>
              ))}
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-fg-muted mb-1">Deployments</div>
              {selected.deployments.length === 0 && <div className="text-fg-muted">none</div>}
              {selected.deployments.map((d) => (
                <div key={d.id} className="border-t border-border py-1">
                  <div className="flex justify-between"><span className="text-fg">{d.strategy_id} · {d.symbol}</span><span className={d.mode === "LIVE" ? "text-down" : "text-fg"}>{d.mode} · {d.status}</span></div>
                  {d.last_error && <div className="text-fg-muted">{d.last_error}</div>}
                </div>
              ))}
            </div>
          </div>
          <button onClick={() => { setSelected(null); api.adminAuditLogs().then(setLogs).catch(() => {}); }} className="mt-3 text-xs text-fg-muted hover:text-fg">close</button>
        </Card>
      )}

      <ExportCard scope="platform" tenantId={selected?.id} />

      <Card title={selected ? `Audit trail: tenant #${selected.id}` : "Platform audit trail (latest 200)"}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-fg-muted uppercase text-[10px] tracking-wide"><tr className="text-left"><th className="py-1 pr-3">Time</th><th className="py-1 pr-3">Tenant</th><th className="py-1 pr-3">User</th><th className="py-1 pr-3">Event</th><th className="py-1 pr-3">Detail</th></tr></thead>
            <tbody>
              {logs.map((l) => (
                <tr key={l.id} className="border-t border-border">
                  <td className="py-1 pr-3 text-fg-muted whitespace-nowrap">{new Date(l.created_at).toLocaleString()}</td>
                  <td className="py-1 pr-3 text-fg-muted">{l.tenant_id ?? "-"}</td>
                  <td className="py-1 pr-3 text-fg-muted">{l.user_email ?? "-"}</td>
                  <td className="py-1 pr-3 font-medium text-fg">{l.event}</td>
                  <td className="py-1 pr-3 text-fg-muted">{l.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
