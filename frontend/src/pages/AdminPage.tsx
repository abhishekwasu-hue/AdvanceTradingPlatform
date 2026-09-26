import { Bot, Building2, OctagonX, ScrollText, ShieldEllipsis, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import ExportCard from "../components/ExportCard";
import { Card, StatTile } from "../components/ui";
import type { AdminOverview, AdminPlan, AdminTenantDetail, AdminTenantSummary, PlatformAuditLog } from "../types";

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
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isAdmin = user?.role === "SUPER_ADMIN";

  function refresh() {
    if (!isAdmin) return;
    api.adminOverview().then(setOverview).catch((e) => setError(String(e)));
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
        <h1 className="text-xl font-extrabold text-rose-400">Admin Console</h1>
        <Card><p className="text-sm text-muted">Platform administrators only.</p></Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-rose-400">Admin Console</h1>
        <p className="text-sm font-semibold text-rose-400/60">
          Every organisation on the platform: plans, suspension, what the worker is running, the
          platform-wide audit trail, and the global kill switch. Every change here lands on the
          affected tenant's own audit trail and notifies them.
        </p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <StatTile icon={Building2} label="Tenants" value={overview?.tenants_total ?? "…"} accentClass="text-rose-400" />
        <StatTile icon={Users} label="Active users" value={overview?.users_total ?? "…"} accentClass="text-orange-400" />
        <StatTile icon={ShieldEllipsis} label="Active / LIVE deployments" value={overview ? `${overview.active_deployments} / ${overview.live_deployments}` : "…"} accentClass="text-sky-400" />
        <StatTile icon={ScrollText} label="Open positions (LIVE)" value={overview ? `${overview.open_positions} (${overview.open_live_positions})` : "…"} accentClass="text-violet-400" />
        <StatTile icon={Bot} label="Worker" value={overview === null ? "…" : overview.worker_running ? "Running" : "Down"} tone={overview === null ? "default" : overview.worker_running ? "up" : "down"} />
      </div>

      {error && <div className="text-sm text-danger">{error}</div>}
      {message && <div className="text-sm text-accent">{message}</div>}

      <Card title="Global kill switch">
        {overview?.global_kill_switch_engaged ? (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="flex items-center gap-1.5 text-danger font-bold"><OctagonX size={16} /> ENGAGED</span>
            <span className="text-muted">{overview.global_kill_switch_reason}</span>
            <button disabled={busy} onClick={() => act("Global kill switch disengaged.", api.disengageGlobalKillSwitch)} className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1 text-xs">Disengage</button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted">Blocks every new order on every tenant until disengaged. Open positions keep being monitored.</span>
            <input className="rounded bg-panel2 border border-border px-2 py-1 text-xs w-56" placeholder="reason" value={killReason} onChange={(e) => setKillReason(e.target.value)} />
            <button disabled={busy || !killReason} onClick={() => act("Global kill switch engaged.", () => api.engageGlobalKillSwitch(killReason))} className="rounded bg-danger hover:bg-red-700 text-white font-semibold px-3 py-1 text-xs disabled:opacity-50">Engage</button>
          </div>
        )}
      </Card>

      <Card title={`Tenants (${tenants.length})`}>
        <input className="w-full sm:w-80 rounded bg-panel2 border border-border px-2 py-1.5 text-sm mb-3" placeholder="search by name or member email" value={query} onChange={(e) => setQuery(e.target.value)} />
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-muted uppercase text-[10px] tracking-wide">
              <tr className="text-left"><th className="py-1 pr-3">#</th><th className="py-1 pr-3">Organisation</th><th className="py-1 pr-3">Owners</th><th className="py-1 pr-3">Plan</th><th className="py-1 pr-3">Status</th><th className="py-1 pr-3">Members</th><th className="py-1 pr-3">Deployments (LIVE)</th><th className="py-1 pr-3">Open</th><th /></tr>
            </thead>
            <tbody>
              {tenants.map((t) => (
                <tr key={t.id} className={`border-t border-border ${selected?.id === t.id ? "bg-panel2/60" : ""}`}>
                  <td className="py-1.5 pr-3 text-muted">{t.id}</td>
                  <td className="py-1.5 pr-3 text-slate-200 font-medium">{t.name}</td>
                  <td className="py-1.5 pr-3 text-muted">{t.owners.join(", ") || "-"}</td>
                  <td className="py-1.5 pr-3">
                    <select className="rounded bg-panel2 border border-border px-1 py-0.5 text-xs" value={t.plan} disabled={busy} onChange={(e) => act(`${t.name}: plan set to ${e.target.value}.`, () => api.adminUpdateTenant(t.id, { plan: e.target.value }))}>
                      {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                  </td>
                  <td className="py-1.5 pr-3">
                    <select className={`rounded bg-panel2 border px-1 py-0.5 text-xs ${t.status === "active" ? "border-accent/40 text-accent" : "border-danger/40 text-danger"}`} value={t.status} disabled={busy} onChange={(e) => {
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
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1">Usage / limits</div>
              {Object.entries(selected.usage).map(([k, v]) => (
                <div key={k} className="flex justify-between border-t border-border py-1"><span className="text-muted">{k.replace("_", " ")}</span><span className="font-tabular text-slate-200">{v} / {String(selected.limits[k] ?? "-")}</span></div>
              ))}
              <div className="flex justify-between border-t border-border py-1"><span className="text-muted">live trading</span><span className="text-slate-200">{selected.limits.live_trading ? "yes" : "no"}</span></div>
              <div className="flex justify-between border-t border-border py-1"><span className="text-muted">tenant kill switch</span><span className={selected.tenant_kill_switch_engaged ? "text-danger" : "text-slate-200"}>{selected.tenant_kill_switch_engaged ? "engaged" : "off"}</span></div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1">Users</div>
              {selected.users.map((u) => (
                <div key={u.id} className="flex justify-between border-t border-border py-1"><span className={u.is_active ? "text-slate-200" : "text-muted line-through"}>{u.email}</span><span className="text-muted">{u.role}</span></div>
              ))}
              <div className="text-[10px] uppercase tracking-wider text-muted mt-3 mb-1">Brokers</div>
              {selected.brokers.length === 0 && <div className="text-muted">none stored</div>}
              {selected.brokers.map((b) => (
                <div key={b.broker_name} className="flex justify-between border-t border-border py-1"><span className="capitalize text-slate-200">{b.broker_name}</span><span className={b.token_status === "VALID" ? "text-accent" : "text-warn"}>{b.token_status}</span></div>
              ))}
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1">Deployments</div>
              {selected.deployments.length === 0 && <div className="text-muted">none</div>}
              {selected.deployments.map((d) => (
                <div key={d.id} className="border-t border-border py-1">
                  <div className="flex justify-between"><span className="text-slate-200">{d.strategy_id} · {d.symbol}</span><span className={d.mode === "LIVE" ? "text-rose-400" : "text-sky-400"}>{d.mode} · {d.status}</span></div>
                  {d.last_error && <div className="text-muted">{d.last_error}</div>}
                </div>
              ))}
            </div>
          </div>
          <button onClick={() => { setSelected(null); api.adminAuditLogs().then(setLogs).catch(() => {}); }} className="mt-3 text-xs text-muted hover:text-slate-200">close</button>
        </Card>
      )}

      <ExportCard scope="platform" tenantId={selected?.id} />

      <Card title={selected ? `Audit trail: tenant #${selected.id}` : "Platform audit trail (latest 200)"}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-muted uppercase text-[10px] tracking-wide"><tr className="text-left"><th className="py-1 pr-3">Time</th><th className="py-1 pr-3">Tenant</th><th className="py-1 pr-3">User</th><th className="py-1 pr-3">Event</th><th className="py-1 pr-3">Detail</th></tr></thead>
            <tbody>
              {logs.map((l) => (
                <tr key={l.id} className="border-t border-border">
                  <td className="py-1 pr-3 text-muted whitespace-nowrap">{new Date(l.created_at).toLocaleString()}</td>
                  <td className="py-1 pr-3 text-muted">{l.tenant_id ?? "-"}</td>
                  <td className="py-1 pr-3 text-slate-300">{l.user_email ?? "-"}</td>
                  <td className="py-1 pr-3 font-medium text-slate-200">{l.event}</td>
                  <td className="py-1 pr-3 text-muted">{l.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
