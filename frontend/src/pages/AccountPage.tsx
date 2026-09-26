import { LogOut, Lock, Mail } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ROLE_LABELS, type InviteInfo, type SessionInfo } from "../types";
import { Card } from "../components/ui";
import { LogoMark } from "../components/Logo";

export default function AccountPage() {
  const { user, login, register, acceptInvite, logout } = useAuth();
  const [mode, setMode] = useState<"login" | "register" | "invite">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [inviteToken, setInviteToken] = useState<string | null>(null);
  const [invite, setInvite] = useState<InviteInfo | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);

  useEffect(() => {
    if (!user) return;
    api.listSessions().then(setSessions).catch(() => setSessions([]));
  }, [user]);

  async function revoke(id: number) {
    await api.revokeSession(id).catch((e) => setError(String(e)));
    api.listSessions().then(setSessions).catch(() => {});
  }

  // Arriving via an owner's invite link (?invite=<token>): show who invited you, ask only for a
  // password, and join their organisation instead of creating a new one.
  useEffect(() => {
    try {
      const token = new URLSearchParams(window.location.search).get("invite");
      if (!token) return;
      setInviteToken(token);
      setMode("invite");
      api.inviteInfo(token).then((info) => { setInvite(info); setEmail(info.email); }).catch((e) => setError(String(e)));
      window.history.replaceState({}, "", window.location.pathname);
    } catch {
      // no URL API - ordinary login
    }
  }, []);

  if (user) {
    return (
      <div className="space-y-4 max-w-md">
        <Card>
          <div className="flex items-center gap-3 mb-4">
            <div className="w-11 h-11 rounded-full bg-brand/15 border border-brand/30 flex items-center justify-center text-brand font-semibold">
              {user.email[0]?.toUpperCase()}
            </div>
            <div>
              <div className="text-xs text-muted">Signed in as</div>
              <div className="text-sm font-medium text-slate-100">{user.email}</div>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={logout}
              className="flex items-center gap-2 rounded border border-border hover:bg-panel2 text-slate-200 px-4 py-1.5 text-sm transition-colors"
            >
              <LogOut size={14} /> Log out
            </button>
            <button
              onClick={() => api.logoutEverywhere().catch(() => {}).finally(logout)}
              className="rounded border border-danger/40 hover:bg-danger/10 text-danger px-4 py-1.5 text-sm transition-colors"
              title="Ends every session of your account on every device"
            >
              Log out everywhere
            </button>
          </div>
        </Card>
        <Card title={`Active sessions (${sessions.length})`}>
          {sessions.length === 0 ? (
            <div className="text-xs text-muted">No session data.</div>
          ) : (
            <table className="w-full text-xs">
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id} className="border-t border-border">
                    <td className="py-1.5 pr-3 text-slate-200">{s.current ? "This device" : "Other device"}</td>
                    <td className="py-1.5 pr-3 text-muted">{s.ip_address ?? "-"}</td>
                    <td className="py-1.5 pr-3 text-muted truncate max-w-[12rem]" title={s.user_agent ?? ""}>{s.user_agent ?? "-"}</td>
                    <td className="py-1.5 pr-3 text-muted whitespace-nowrap">active {new Date(s.last_used_at).toLocaleString()}</td>
                    <td className="py-1.5 text-right">{!s.current && <button onClick={() => revoke(s.id)} className="text-danger hover:underline">revoke</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <p className="text-xs text-muted leading-relaxed">
          Broker credentials and paper trades are tied to this account. Anonymous use of Signals
          and Backtesting still works without logging in - signing in additionally saves your
          paper-execute fills to the Positions tab.
        </p>
      </div>
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (mode === "login") {
        await login(email, password);
      } else if (mode === "invite" && inviteToken) {
        await acceptInvite(inviteToken, password);
      } else {
        await register(email, password);
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="max-w-sm space-y-5">
      <div className="flex flex-col items-center text-center gap-3 pt-4">
        <LogoMark size={44} />
        <div>
          <h1 className="text-lg font-semibold text-slate-100">
            {mode === "login" ? "Welcome back" : mode === "invite" ? "Join your team" : "Create your account"}
          </h1>
          <p className="text-xs text-muted mt-0.5">
            {mode === "login"
              ? "Log in to your trading console"
              : mode === "invite"
                ? invite
                  ? invite.valid
                    ? `You were invited to ${invite.tenant_name} as ${ROLE_LABELS[invite.role] ?? invite.role}. Choose a password to join.`
                    : invite.reason ?? "This invitation is no longer valid."
                  : "Checking your invitation…"
                : "Start building and running strategies"}
          </p>
        </div>
      </div>
      <Card className="shadow-card">
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-xs text-muted mb-1">Email</label>
            <div className="relative">
              <Mail size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
              <input
                type="email"
                required
                readOnly={mode === "invite"}
                className="w-full rounded bg-panel2 border border-border pl-8 pr-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-brand focus:border-brand transition-colors read-only:text-muted"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Password</label>
            <div className="relative">
              <Lock size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
              <input
                type="password"
                required
                minLength={6}
                className="w-full rounded bg-panel2 border border-border pl-8 pr-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-brand focus:border-brand transition-colors"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
          </div>
          {error && <div className="text-xs text-danger">{error}</div>}
          <button
            type="submit"
            disabled={loading || (mode === "invite" && !(invite && invite.valid))}
            className="w-full rounded bg-brand hover:bg-brand-dim text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50 transition-colors"
          >
            {loading ? "Please wait…" : mode === "login" ? "Log in" : mode === "invite" ? "Join team" : "Create account"}
          </button>
        </form>
        <button
          onClick={() => setMode(mode === "login" ? "register" : "login")}
          className="mt-3 text-xs text-brand hover:underline"
        >
          {mode === "login" ? "Need an account? Register" : "Already have an account? Log in"}
        </button>
      </Card>
    </div>
  );
}
