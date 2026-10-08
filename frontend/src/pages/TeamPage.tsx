import { Copy, Link2, ShieldCheck, UserMinus, UserPlus, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, StatTile } from "../components/ui";
import { ROLE_LABELS, ROUTING_POLICIES, type MemberScopes, type ScopeCatalogueEntry, type TeamInvite, type TeamMember, type TenantInfo, type TenantRole } from "../types";

import { PageHeader } from "../components/primitives";
const INVITABLE: TenantRole[] = ["USER", "STRATEGY_CREATOR", "VIEWER"];
const ASSIGNABLE: TenantRole[] = ["OWNER", "USER", "STRATEGY_CREATOR", "VIEWER"];

function RoleBadge({ role }: { role: TenantRole }) {
  const cls =
    role === "OWNER" ? "border-warn/40 text-warn bg-warn/10"
      : role === "SUPER_ADMIN" ? "border-down/40 text-down bg-down/10"
        : role === "VIEWER" || role === "SUPPORT" ? "border-border text-fg-muted bg-surface-2"
          : "border-border text-fg bg-surface-2";
  return <span className={`inline-block rounded-md border px-2 py-0.5 text-[11px] font-bold ${cls}`}>{ROLE_LABELS[role] ?? role}</span>;
}

export default function TeamPage() {
  const { user, loading: authLoading } = useAuth();
  const [tenant, setTenant] = useState<TenantInfo | null>(null);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [invites, setInvites] = useState<TeamInvite[]>([]);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<TenantRole>("USER");
  const [lastInviteUrl, setLastInviteUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [tenantName, setTenantName] = useState("");
  const [algoId, setAlgoId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [catalogue, setCatalogue] = useState<ScopeCatalogueEntry[]>([]);
  const [scopeEdit, setScopeEdit] = useState<MemberScopes | null>(null);

  const isOwner = user?.role === "OWNER" || user?.role === "SUPER_ADMIN";

  function refresh() {
    if (!user) return;
    api.getTenant().then((t) => { setTenant(t); setTenantName(t.name); setAlgoId(t.algo_id ?? ""); }).catch((e) => setError(String(e)));
    api.listMembers().then(setMembers).catch((e) => setError(String(e)));
    if (isOwner) api.listInvites().then(setInvites).catch(() => setInvites([]));
    if (isOwner && catalogue.length === 0) api.scopeCatalogue().then(setCatalogue).catch(() => {});
  }

  function toggleScope(scope: string) {
    if (!scopeEdit) return;
    const roleHas = catalogue.find((c) => c.scope === scope)?.roles.includes(scopeEdit.role) ?? false;
    const deny = new Set(scopeEdit.overrides.deny);
    const grant = new Set(scopeEdit.overrides.grant);
    const effective = scopeEdit.scopes.includes(scope);
    if (effective) {
      // turning off: a role-held scope gets denied, a granted one loses its grant
      if (roleHas) deny.add(scope); else grant.delete(scope);
    } else if (roleHas) deny.delete(scope); else grant.add(scope);
    const scopes = catalogue.map((c) => c.scope).filter((s) => (c(s) || grant.has(s)) && !deny.has(s));
    function c(s: string) { return catalogue.find((x) => x.scope === s)?.roles.includes(scopeEdit!.role) ?? false; }
    setScopeEdit({ ...scopeEdit, scopes, overrides: { deny: [...deny].sort(), grant: [...grant].sort() } });
  }

  useEffect(refresh, [user]); // eslint-disable-line react-hooks/exhaustive-deps

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

  async function invite() {
    setBusy(true);
    setError(null);
    setMessage(null);
    setLastInviteUrl(null);
    try {
      const created = await api.createInvite(inviteEmail.trim(), inviteRole);
      setLastInviteUrl(created.invite_url ?? null);
      setMessage(`Invitation for ${created.email} created - share the link below (valid 48 hours, shown once).`);
      setInviteEmail("");
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function copyInvite() {
    if (!lastInviteUrl) return;
    try {
      await navigator.clipboard.writeText(lastInviteUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard unavailable - the link stays visible for manual copy
    }
  }

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <PageHeader title="Team" />
        <Card><p className="text-sm text-fg-muted">Log in from the Account tab to see your team.</p></Card>
      </div>
    );
  }

  const active = members.filter((m) => m.is_active);

  return (
    <div className="space-y-4">
      <PageHeader title="Team" description="Everyone in your organisation shares the same broker connections, deployments, positions and alerts. Owners manage the team; traders and strategy creators can trade and configure; viewers see everything and change nothing." />

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatTile icon={Users} label="Organisation" value={tenant?.name ?? "…"} accentClass="text-fg" />
        <StatTile icon={ShieldCheck} label="Your role" value={ROLE_LABELS[user.role as TenantRole] ?? user.role} accentClass="text-warn" />
        <StatTile icon={UserPlus} label="Active members" value={active.length} accentClass="text-fg" />
        <StatTile icon={Link2} label="Open invites" value={isOwner ? invites.length : "-"} accentClass="text-fg" />
      </div>

      {error && <div className="text-sm text-down">{error}</div>}
      {message && <div className="text-sm text-up">{message}</div>}

      {isOwner && (
        <Card title="Invite a teammate">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[16rem]">
              <label className="block text-xs text-fg-muted mb-1">Email</label>
              <input className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" type="email" value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} placeholder="colleague@company.com" />
            </div>
            <div>
              <label className="block text-xs text-fg-muted mb-1">Role</label>
              <select className="rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={inviteRole} onChange={(e) => setInviteRole(e.target.value as TenantRole)}>
                {INVITABLE.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
              </select>
            </div>
            <button onClick={invite} disabled={busy || !inviteEmail.includes("@")} className="flex items-center gap-1.5 rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-4 py-1.5 text-sm disabled:opacity-50">
              <UserPlus size={14} /> Create invite link
            </button>
          </div>
          {lastInviteUrl && (
            <div className="mt-3 flex gap-2">
              <input readOnly className="flex-1 rounded bg-surface-2 border border-border px-2 py-1.5 text-xs font-mono" value={lastInviteUrl} onFocus={(e) => e.target.select()} />
              <button onClick={copyInvite} className="flex items-center gap-1 rounded border border-border hover:bg-surface-2 text-fg px-3 py-1.5 text-xs shrink-0"><Copy size={12} /> {copied ? "Copied!" : "Copy"}</button>
            </div>
          )}
          {invites.length > 0 && (
            <table className="w-full text-xs mt-4">
              <thead className="text-fg-muted uppercase text-[10px] tracking-wide"><tr className="text-left"><th className="py-1 pr-3">Pending</th><th className="py-1 pr-3">Role</th><th className="py-1 pr-3">Expires</th><th /></tr></thead>
              <tbody>
                {invites.map((i) => (
                  <tr key={i.id} className="border-t border-border">
                    <td className="py-1 pr-3 text-fg">{i.email}</td>
                    <td className="py-1 pr-3"><RoleBadge role={i.role} /></td>
                    <td className="py-1 pr-3 text-fg-muted">{new Date(i.expires_at).toLocaleString()}</td>
                    <td className="py-1 text-right"><button disabled={busy} onClick={() => act(`Invite for ${i.email} revoked.`, () => api.revokeInvite(i.id))} className="text-down hover:underline">Revoke</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      <Card title={`Members (${members.length})`}>
        <table className="w-full text-xs">
          <thead className="text-fg-muted uppercase text-[10px] tracking-wide"><tr className="text-left"><th className="py-1 pr-3">Email</th><th className="py-1 pr-3">Role</th><th className="py-1 pr-3">Joined</th><th className="py-1 pr-3">Status</th>{isOwner && <th className="py-1 text-right">Actions</th>}</tr></thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id} className="border-t border-border">
                <td className="py-1.5 pr-3 text-fg">{m.email}{m.id === user.id && <span className="text-fg-muted"> (you)</span>}</td>
                <td className="py-1.5 pr-3">
                  {isOwner && m.role !== "SUPER_ADMIN" ? (
                    <select className="rounded bg-surface-2 border border-border px-1 py-0.5 text-xs" value={m.role} disabled={busy} onChange={(e) => act(`${m.email} is now ${ROLE_LABELS[e.target.value as TenantRole]}.`, () => api.changeMemberRole(m.id, e.target.value))}>
                      {ASSIGNABLE.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                    </select>
                  ) : <RoleBadge role={m.role} />}
                </td>
                <td className="py-1.5 pr-3 text-fg-muted">{new Date(m.created_at).toLocaleDateString()}</td>
                <td className={`py-1.5 pr-3 ${m.is_active ? "text-up" : "text-fg-muted"}`}>{m.is_active ? "active" : "removed"}{m.trading_disabled_reason && <div className="text-[10px] text-warn" title={m.trading_disabled_reason}>trading disabled</div>}</td>
                {isOwner && (
                  <td className="py-1.5 text-right">
                    {m.id !== user.id && m.role !== "SUPER_ADMIN" && m.is_active && (
                      <button disabled={busy} title="Log this member out of every device" onClick={() => act(`${m.email} logged out everywhere.`, () => api.logoutMemberEverywhere(m.id))} className="text-xs text-fg-muted hover:text-fg mr-2">log out</button>
                    )}
                    {m.role !== "SUPER_ADMIN" && m.is_active && (
                      <button disabled={busy} title="Fine-grained permissions on top of the role" onClick={() => api.memberScopes(m.id).then(setScopeEdit).catch((e) => setError(String(e)))} className="text-xs text-fg-muted hover:text-fg mr-2">permissions</button>
                    )}
                    {m.role !== "SUPER_ADMIN" && m.is_active && (m.trading_disabled_reason
                      ? <button disabled={busy} onClick={() => act(`${m.email} can trade again.`, () => api.enableMemberTrading(m.id))} className="text-xs text-brand hover:underline mr-2">enable trading</button>
                      : <button disabled={busy} title="Stop this member opening new positions (exits and reading stay allowed)" onClick={() => { const reason = window.prompt("Reason (shown to the member)"); if (reason && reason.length >= 3) void act(`${m.email}: trading disabled.`, () => api.disableMemberTrading(m.id, reason)); }} className="text-xs text-warn hover:underline mr-2">disable trading</button>)}
                    {m.id !== user.id && m.role !== "SUPER_ADMIN" && (m.is_active
                      ? <button disabled={busy} title="Remove from team" onClick={() => act(`${m.email} removed.`, () => api.removeMember(m.id))} className="p-1 text-down hover:bg-surface-2 rounded"><UserMinus size={14} /></button>
                      : <span className="flex items-center gap-2">
                          <button disabled={busy} onClick={() => act(`${m.email} reactivated.`, () => api.reactivateMember(m.id))} className="text-xs text-brand hover:underline">Reactivate</button>
                          {!m.email.endsWith("@erased.invalid") && (
                            <button disabled={busy} title="Erase personal data (irreversible)" onClick={() => {
                              if (window.confirm(`Erase ${m.email}'s personal data? Their trades, orders and audit rows stay under an anonymous id; email, password and MFA are removed. This cannot be undone.`)) {
                                act("Personal data erased.", () => api.eraseMember(m.id, "erased by owner"));
                              }
                            }} className="text-xs text-down hover:underline">Erase data</button>
                          )}
                        </span>)}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {scopeEdit && (
          <div className="mt-3 rounded-lg border border-border bg-surface-2/40 p-3 text-xs">
            <div className="flex items-center justify-between mb-2">
              <div className="font-bold text-sm">Permissions for {members.find((m) => m.id === scopeEdit.id)?.email ?? `#${scopeEdit.id}`} <span className="text-fg-muted font-normal">({ROLE_LABELS[scopeEdit.role as TenantRole] ?? scopeEdit.role})</span></div>
              <button onClick={() => setScopeEdit(null)} className="text-fg-muted hover:text-fg">close</button>
            </div>
            <p className="text-fg-muted mb-2">Unticking a permission the role normally has denies it for this member; ticking one the role lacks grants it (within what you hold yourself). Exits are never gated.</p>
            <div className="grid sm:grid-cols-2 gap-1.5">
              {catalogue.filter((c) => c.scope !== "admin:platform").map((c) => (
                <label key={c.scope} className="flex items-start gap-2 cursor-pointer">
                  <input type="checkbox" className="mt-0.5" checked={scopeEdit.scopes.includes(c.scope)} onChange={() => toggleScope(c.scope)} />
                  <span><span className="font-mono text-fg">{c.scope}</span> <span className="text-fg-muted">- {c.description}</span></span>
                </label>
              ))}
            </div>
            <div className="mt-3 flex gap-2">
              <button disabled={busy} onClick={() => act("Permissions saved.", () => api.setMemberScopes(scopeEdit.id, scopeEdit.overrides.deny, scopeEdit.overrides.grant).then(setScopeEdit))} className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-3 py-1">Save</button>
              <button disabled={busy} onClick={() => act("Overrides cleared.", () => api.setMemberScopes(scopeEdit.id, [], []).then(setScopeEdit))} className="rounded border border-border hover:bg-surface-2 text-fg px-3 py-1">Reset to role</button>
            </div>
          </div>
        )}
      </Card>

      {isOwner && tenant && (
        <Card title="Organisation">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[16rem]">
              <label className="block text-xs text-fg-muted mb-1">Name</label>
              <input className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={tenantName} onChange={(e) => setTenantName(e.target.value)} />
            </div>
            <button disabled={busy || !tenantName.trim() || tenantName === tenant.name} onClick={() => act("Organisation renamed.", () => api.renameTenant(tenantName))} className="rounded border border-border hover:bg-surface-2 text-fg px-4 py-1.5 text-sm disabled:opacity-50">Rename</button>
            <div className="text-xs text-fg-muted">Status: <span className={`font-semibold ${tenant.status === "active" ? "text-up" : "text-down"}`}>{tenant.status}</span></div>
          </div>
          <label className="mt-3 flex items-start gap-2 text-xs text-fg-muted cursor-pointer">
            <input type="checkbox" className="mt-0.5" checked={Boolean(tenant.require_mfa_for_live)} disabled={busy}
              onChange={(e) => act(e.target.checked ? "Two-factor authentication is now required for live trading and broker credentials." : "Two-factor requirement removed.", () => api.setTenantMfaPolicy(e.target.checked))} />
            <span><b>Require two-factor authentication</b> for LIVE deployments, broker credentials and broker login. Members without it will be asked to enable it on the Account tab first. (Enable it on your own account before turning this on.)</span>
          </label>
          <div className="mt-3 flex items-center gap-2 text-xs">
            <span className="text-fg font-semibold">Reporting currency</span>
            <select className="rounded bg-surface-2 border border-border px-1 py-0.5" value={tenant.base_currency ?? "INR"} disabled={busy}
              onChange={(e) => act(`Portfolio figures now reported in ${e.target.value}.`, () => api.setTenantBaseCurrency(e.target.value))}>
              {["INR", "USD", "USDT", "EUR", "GBP", "AED", "SGD"].map((c) => <option key={c}>{c}</option>)}
            </select>
            <span className="text-fg-muted">Portfolio exposure converts non-{tenant.base_currency ?? "INR"} instruments with the platform's FX rates.</span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="text-fg-muted">Default account routing</span>
            <select className="rounded bg-surface-2 border border-border px-1 py-0.5" value={tenant.default_routing_policy ?? "EXPLICIT"} disabled={busy}
              onChange={(e) => act("Default account routing updated.", () => api.setTenantRoutingPolicy(e.target.value))}>
              {ROUTING_POLICIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select>
            <span className="text-fg-muted">{ROUTING_POLICIES.find((p) => p.value === (tenant.default_routing_policy ?? "EXPLICIT"))?.help} LIVE deployments without their own policy use this.</span>
          </div>
          <div className="mt-4 border-t border-border pt-3">
            <div className="text-xs font-semibold text-fg">Exchange algo id (SEBI algo tagging)</div>
            <p className="text-xs text-fg-muted mt-1 mb-2">
              SEBI's retail algo framework requires every algorithmic order to carry the identifier the exchange
              issued when your broker registered the algo. Enter it here once and every entry, stop-loss and exit
              order this platform places is tagged <code>{(algoId || "ALGOID")}-strategy-leg</code> at the broker;
              the tag is also stored on each order for reconciliation. Leave blank until registration is done.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <input className="rounded bg-surface-2 border border-border px-2 py-1.5 text-sm font-mono w-56" placeholder="e.g. NSE1234567" value={algoId} maxLength={32}
                onChange={(e) => setAlgoId(e.target.value)} />
              <button disabled={busy || algoId === (tenant.algo_id ?? "")} onClick={() => act(algoId ? "Algo id saved - new orders will carry it." : "Algo id cleared.", () => api.setTenantAlgoId(algoId))}
                className="rounded border border-border hover:bg-surface-2 text-fg px-4 py-1.5 text-sm disabled:opacity-50">Save</button>
              <span className="text-xs text-fg-muted">{tenant.algo_id ? `Current: ${tenant.algo_id}` : "Not set - orders are tagged strategy-leg only."}</span>
            </div>
          </div>
        </Card>
      )}

      {tenant && (
        <Card title={`Plan: ${tenant.plan_name}`}>
          <p className="text-xs text-fg-muted mb-3">{tenant.plan_description}</p>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
            {([
              ["Active deployments", tenant.usage.active_deployments, tenant.limits.active_deployments],
              ["Custom strategies", tenant.usage.custom_strategies, tenant.limits.custom_strategies],
              ["Team members (incl. invites)", tenant.usage.members, tenant.limits.members],
              ["Alert channels", tenant.usage.alert_channels, tenant.limits.alert_channels],
            ] as [string, number, number][]).map(([label, used, max]) => (
              <div key={label} className="rounded-lg border border-border bg-surface-2/40 p-3">
                <div className="text-fg-muted">{label}</div>
                <div className={`font-tabular text-lg font-extrabold ${used >= max ? "text-warn" : "text-fg"}`}>{used} <span className="text-fg-muted text-xs font-semibold">/ {max}</span></div>
                <div className="h-1.5 w-full rounded-full bg-surface-2 overflow-hidden mt-1"><div className={`h-1.5 rounded-full ${used >= max ? "bg-warn" : "bg-brand"}`} style={{ width: `${Math.min(100, (used / Math.max(1, max)) * 100)}%` }} /></div>
              </div>
            ))}
          </div>
          <div className={`mt-3 text-xs font-semibold ${tenant.limits.live_trading ? "text-up" : "text-warn"}`}>
            {tenant.limits.live_trading ? "Live trading included." : "Paper trading only on this plan - LIVE deployments need Pro or Business. Contact support to upgrade."}
          </div>
        </Card>
      )}
    </div>
  );
}
