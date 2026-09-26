import { Copy, Link2, ShieldCheck, UserMinus, UserPlus, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, StatTile } from "../components/ui";
import { ROLE_LABELS, type TeamInvite, type TeamMember, type TenantInfo, type TenantRole } from "../types";

const INVITABLE: TenantRole[] = ["USER", "STRATEGY_CREATOR", "VIEWER"];
const ASSIGNABLE: TenantRole[] = ["OWNER", "USER", "STRATEGY_CREATOR", "VIEWER"];

function RoleBadge({ role }: { role: TenantRole }) {
  const cls =
    role === "OWNER" ? "border-amber-500/40 text-amber-400 bg-amber-500/10"
      : role === "SUPER_ADMIN" ? "border-rose-500/40 text-rose-400 bg-rose-500/10"
        : role === "VIEWER" || role === "SUPPORT" ? "border-border text-muted bg-panel2"
          : "border-sky-500/40 text-sky-400 bg-sky-500/10";
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
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isOwner = user?.role === "OWNER" || user?.role === "SUPER_ADMIN";

  function refresh() {
    if (!user) return;
    api.getTenant().then((t) => { setTenant(t); setTenantName(t.name); }).catch((e) => setError(String(e)));
    api.listMembers().then(setMembers).catch((e) => setError(String(e)));
    if (isOwner) api.listInvites().then(setInvites).catch(() => setInvites([]));
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
        <h1 className="text-xl font-extrabold text-orange-400">Team</h1>
        <Card><p className="text-sm text-muted">Log in from the Account tab to see your team.</p></Card>
      </div>
    );
  }

  const active = members.filter((m) => m.is_active);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-orange-400">Team</h1>
        <p className="text-sm font-semibold text-orange-400/60">
          Everyone in your organisation shares the same broker connections, deployments, positions
          and alerts. Owners manage the team; traders and strategy creators can trade and configure;
          viewers see everything and change nothing.
        </p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatTile icon={Users} label="Organisation" value={tenant?.name ?? "…"} accentClass="text-orange-400" />
        <StatTile icon={ShieldCheck} label="Your role" value={ROLE_LABELS[user.role as TenantRole] ?? user.role} accentClass="text-amber-400" />
        <StatTile icon={UserPlus} label="Active members" value={active.length} accentClass="text-sky-400" />
        <StatTile icon={Link2} label="Open invites" value={isOwner ? invites.length : "-"} accentClass="text-violet-400" />
      </div>

      {error && <div className="text-sm text-danger">{error}</div>}
      {message && <div className="text-sm text-accent">{message}</div>}

      {isOwner && (
        <Card title="Invite a teammate">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[16rem]">
              <label className="block text-xs text-muted mb-1">Email</label>
              <input className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" type="email" value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} placeholder="colleague@company.com" />
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">Role</label>
              <select className="rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={inviteRole} onChange={(e) => setInviteRole(e.target.value as TenantRole)}>
                {INVITABLE.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
              </select>
            </div>
            <button onClick={invite} disabled={busy || !inviteEmail.includes("@")} className="flex items-center gap-1.5 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50">
              <UserPlus size={14} /> Create invite link
            </button>
          </div>
          {lastInviteUrl && (
            <div className="mt-3 flex gap-2">
              <input readOnly className="flex-1 rounded bg-panel2 border border-border px-2 py-1.5 text-xs font-mono" value={lastInviteUrl} onFocus={(e) => e.target.select()} />
              <button onClick={copyInvite} className="flex items-center gap-1 rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1.5 text-xs shrink-0"><Copy size={12} /> {copied ? "Copied!" : "Copy"}</button>
            </div>
          )}
          {invites.length > 0 && (
            <table className="w-full text-xs mt-4">
              <thead className="text-muted uppercase text-[10px] tracking-wide"><tr className="text-left"><th className="py-1 pr-3">Pending</th><th className="py-1 pr-3">Role</th><th className="py-1 pr-3">Expires</th><th /></tr></thead>
              <tbody>
                {invites.map((i) => (
                  <tr key={i.id} className="border-t border-border">
                    <td className="py-1 pr-3 text-slate-200">{i.email}</td>
                    <td className="py-1 pr-3"><RoleBadge role={i.role} /></td>
                    <td className="py-1 pr-3 text-muted">{new Date(i.expires_at).toLocaleString()}</td>
                    <td className="py-1 text-right"><button disabled={busy} onClick={() => act(`Invite for ${i.email} revoked.`, () => api.revokeInvite(i.id))} className="text-danger hover:underline">Revoke</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      <Card title={`Members (${members.length})`}>
        <table className="w-full text-xs">
          <thead className="text-muted uppercase text-[10px] tracking-wide"><tr className="text-left"><th className="py-1 pr-3">Email</th><th className="py-1 pr-3">Role</th><th className="py-1 pr-3">Joined</th><th className="py-1 pr-3">Status</th>{isOwner && <th className="py-1 text-right">Actions</th>}</tr></thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id} className="border-t border-border">
                <td className="py-1.5 pr-3 text-slate-200">{m.email}{m.id === user.id && <span className="text-muted"> (you)</span>}</td>
                <td className="py-1.5 pr-3">
                  {isOwner && m.role !== "SUPER_ADMIN" ? (
                    <select className="rounded bg-panel2 border border-border px-1 py-0.5 text-xs" value={m.role} disabled={busy} onChange={(e) => act(`${m.email} is now ${ROLE_LABELS[e.target.value as TenantRole]}.`, () => api.changeMemberRole(m.id, e.target.value))}>
                      {ASSIGNABLE.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                    </select>
                  ) : <RoleBadge role={m.role} />}
                </td>
                <td className="py-1.5 pr-3 text-muted">{new Date(m.created_at).toLocaleDateString()}</td>
                <td className={`py-1.5 pr-3 ${m.is_active ? "text-accent" : "text-muted"}`}>{m.is_active ? "active" : "removed"}</td>
                {isOwner && (
                  <td className="py-1.5 text-right">
                    {m.id !== user.id && m.role !== "SUPER_ADMIN" && (m.is_active
                      ? <button disabled={busy} title="Remove from team" onClick={() => act(`${m.email} removed.`, () => api.removeMember(m.id))} className="p-1 text-danger hover:bg-panel2 rounded"><UserMinus size={14} /></button>
                      : <button disabled={busy} onClick={() => act(`${m.email} reactivated.`, () => api.reactivateMember(m.id))} className="text-xs text-brand hover:underline">Reactivate</button>)}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {isOwner && tenant && (
        <Card title="Organisation">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[16rem]">
              <label className="block text-xs text-muted mb-1">Name</label>
              <input className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={tenantName} onChange={(e) => setTenantName(e.target.value)} />
            </div>
            <button disabled={busy || !tenantName.trim() || tenantName === tenant.name} onClick={() => act("Organisation renamed.", () => api.renameTenant(tenantName))} className="rounded border border-border hover:bg-panel2 text-slate-200 px-4 py-1.5 text-sm disabled:opacity-50">Rename</button>
            <div className="text-xs text-muted">Plan: <span className="text-slate-200 font-semibold">{tenant.plan}</span> · Status: <span className="text-slate-200 font-semibold">{tenant.status}</span></div>
          </div>
        </Card>
      )}
    </div>
  );
}
