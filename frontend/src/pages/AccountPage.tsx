import { LogOut, ShieldCheck } from "lucide-react";
import { resendVerification } from "../ui/verifyEmail";
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { api, setToken } from "../api/client";
import { friendlyError } from "../api/errors";
import { useAuth } from "../auth/AuthContext";
import SignInScreen from "../auth/SignInScreen";
import { deviceLabel } from "../auth/deviceLabel";
import { type MfaStatus, type SessionInfo } from "../types";
import MfaCard from "../components/MfaCard";
import { Card } from "../components/ui";
import { isAdminViewer } from "../ui/ComingSoon";
import { ErrorState } from "../ui/ErrorState";
import BillingCard from "../components/BillingCard";
import PlanUsageV2 from "../growth/PlanUsageV2";
import InviteFriendV2 from "../growth/InviteFriendV2";
import { useFeatures } from "../ui/useFeatures";
import { supportAccess, type SupportAccess } from "../api/viewAs";
import { formatDateTimeIST } from "../ui/format";
import MyDataCardV2 from "../settings/MyDataCardV2";
import { SkeletonRows } from "../components/primitives";

/** The signed-in trader's account: e-mail, two-factor, password, sessions. Signed out, App.tsx shows the sign-in screen
 * before any page; this page shows the same screen should it ever render without a user (UX1). */
export default function AccountPage() {
  const { user, logout, refreshUser } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [mfaStatus, setMfaStatus] = useState<MfaStatus | null>(null);
  const [pwCurrent, setPwCurrent] = useState("");
  const [pwNew, setPwNew] = useState("");
  const [pwMessage, setPwMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [verifyMessage, setVerifyMessage] = useState<string | null>(null);
  const [sessionsMessage, setSessionsMessage] = useState<string | null>(null);
  // Settings redesign B1: with `ui_v2`, Settings → Account & plan is this page, with the plan and billing below.
  const features = useFeatures();
  const v2 = features.on("ui_v2");
  // MO4 slice 3: the plan page's usage meter above the plans and billing (ui_v2 + growth_v2).
  const growth = v2 && features.on("growth_v2");
  const referrals = v2 && features.on("referrals");     // Readiness C13: "Invite a friend"

  useEffect(() => {
    if (!user) return;
    api.listSessions().then(setSessions).catch(() => setSessions([]));
    api.mfaStatus().then(setMfaStatus).catch(() => setMfaStatus(null));
  }, [user]);

  async function logoutOthers() {
    setError(null);
    setSessionsMessage(null);
    try {
      const { signed_out } = await api.logoutOtherDevices();
      setSessionsMessage(signed_out === 1 ? "1 other device is signed out." : `${signed_out} other devices are signed out.`);
    } catch (e) {
      setError(friendlyError(e));
    }
    api.listSessions().then(setSessions).catch(() => {});
  }

  async function revoke(id: number) {
    setError(null);
    await api.revokeSession(id).catch((e) => setError(friendlyError(e)));
    api.listSessions().then(setSessions).catch(() => {});
  }

  // An e-mail verification link opened while signed in (`?verify=<token>`): verify, then take the token out of the
  // address through the router. (Signed out, the sign-in screen handles it, and the invite / reset links.)
  useEffect(() => {
    if (!user) return;
    const params = new URLSearchParams(location.search);
    const verify = params.get("verify");
    if (!verify) return;
    params.delete("verify");
    const rest = params.toString();
    navigate({ pathname: location.pathname, search: rest ? `?${rest}` : "" }, { replace: true });
    api.verifyEmail(verify)
      .then((u) => { setVerifyMessage(`${u.email} is verified.`); void refreshUser(); })
      .catch((e) => setVerifyMessage(friendlyError(e)));
  }, [user]);

  if (!user) return <SignInScreen />;

  const account = (
    <div className="space-y-4 max-w-md">
      {error && <ErrorState variant="inline" message={error} />}
      <Card>
        <div className="flex items-center gap-3 mb-4">
          <div className="w-11 h-11 rounded-full bg-brand/15 border border-brand/30 flex items-center justify-center text-brand font-semibold">
            {user.email[0]?.toUpperCase()}
          </div>
          <div>
            <div className="text-xs text-fg-muted">Signed in as</div>
            <div className="text-sm font-medium text-fg">{user.email}</div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={logout}
            className="flex items-center gap-2 rounded border border-border hover:bg-surface-2 text-fg px-4 py-1.5 text-sm transition-colors"
          >
            <LogOut size={14} /> Log out
          </button>
          <button
            onClick={() => api.logoutEverywhere().catch(() => {}).finally(logout)}
            className="rounded border border-down/40 hover:bg-down/10 text-down px-4 py-1.5 text-sm transition-colors"
            title="Ends every session of your account on every device"
          >
            Log out everywhere
          </button>
        </div>
      </Card>
      <Card title="Email verification">
        {user.email_verified ? (
          <div className="text-xs text-up flex items-center gap-2"><ShieldCheck size={14} /> {user.email} is verified.</div>
        ) : (
          <div className="space-y-2 text-xs">
            <p className="text-fg-muted">Confirm that {user.email} is yours: open the link we emailed you, or send a fresh one.</p>
            <button
              className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-3 py-1.5"
              onClick={() => void resendVerification().then(setVerifyMessage)}
            >
              Resend verification email
            </button>
          </div>
        )}
        {verifyMessage && <div className="mt-2 text-xs text-fg-muted">{verifyMessage}</div>}
      </Card>
      {/* UX1 (audit P27): permission codes are for platform administrators only, never a trader's Account page. */}
      {isAdminViewer(user.role) && user.scopes && user.scopes.length > 0 && (
        <Card title="Your permissions">
          <div className="flex flex-wrap gap-1.5">
            {user.scopes.map((s) => <span key={s} className="rounded-md border border-border bg-surface-2 px-2 py-0.5 text-xs font-mono text-fg-muted">{s}</span>)}
          </div>
          <p className="mt-2 text-xs text-fg-muted">Derived from your role; your organisation's owner can deny or grant individual permissions from the Team tab.</p>
        </Card>
      )}
      <MfaCard status={mfaStatus} onChange={() => api.mfaStatus().then(setMfaStatus).catch(() => {})} />
      <Card title="Change password">
        <form
          className="space-y-2"
          onSubmit={async (e) => {
            e.preventDefault();
            setPwMessage(null);
            try {
              const res = await api.changePassword(pwCurrent, pwNew);
              setToken(res.access_token, res.refresh_token);
              setPwMessage("Password changed. Every other session has been logged out.");
              setPwCurrent("");
              setPwNew("");
              api.listSessions().then(setSessions).catch(() => {});
            } catch (err) {
              setPwMessage(friendlyError(err));
            }
          }}
        >
          <input type="password" required autoComplete="current-password" placeholder="Current password" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={pwCurrent} onChange={(e) => setPwCurrent(e.target.value)} />
          <input type="password" required minLength={10} autoComplete="new-password" placeholder="New password (10+ characters, not too common)" className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={pwNew} onChange={(e) => setPwNew(e.target.value)} />
          <div className="flex items-center gap-3">
            <button type="submit" className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-4 py-1.5 text-sm">Change password</button>
            {pwMessage && <span className={`text-xs ${pwMessage.startsWith("Password changed") ? "text-up" : "text-down"}`}>{pwMessage}</span>}
          </div>
        </form>
      </Card>
      <Card title={`Active sessions (${sessions.length})`}>
        {sessions.length === 0 ? (
          <div className="text-xs text-fg-muted">No session data.</div>
        ) : (
          <table className="w-full text-xs">
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id} className="border-t border-border align-top">
                  <td className="py-1.5 pr-3">
                    <div className="text-fg" title={s.user_agent ?? ""}>{deviceLabel(s.user_agent)}{s.current && <span className="ml-1.5 rounded bg-brand/15 px-1.5 py-0.5 text-xs font-medium text-brand">This device</span>}</div>
                    <div className="text-fg-muted">{s.ip_address ? `${s.ip_address} · ` : ""}active {formatDateTimeIST(s.last_used_at)}</div>
                  </td>
                  <td className="py-1.5 text-right">{!s.current && <button onClick={() => revoke(s.id)} className="text-down hover:underline">Sign out</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {/* A4: "I signed in somewhere I should not have" without having to sign in again here. */}
        {sessions.some((s) => !s.current) && (
          <button
            onClick={logoutOthers}
            className="mt-3 rounded border border-down/40 hover:bg-down/10 text-down px-3 py-1.5 text-xs transition-colors"
          >
            Log out other devices
          </button>
        )}
        {sessionsMessage && <div className="mt-2 text-xs text-fg-muted">{sessionsMessage}</div>}
        <p className="mt-2 text-xs text-fg-muted">For your safety you are asked to sign in again after a while, even on a device you use every day.</p>
      </Card>
      <MyDataCardV2 copilot={v2} />
      {v2 && <SupportAccessCard />}
    </div>
  );
  if (!v2) return account;
  return (
    <div className="space-y-4">
      {account}
      <div id="plan" className="scroll-mt-20 space-y-4">{growth && <PlanUsageV2 />}<BillingCard />{referrals && <InviteFriendV2 />}</div>
    </div>
  );
}

/** MO3-A1: when ATP support opened a read-only view of this account - the person's own record (the server keeps it
 * whatever happens on this page). */
export function SupportAccessCard() {
  const [views, setViews] = useState<SupportAccess[] | null>(null);
  useEffect(() => {
    let live = true;
    supportAccess().then((r) => { if (live) setViews(r.views); }).catch(() => { if (live) setViews([]); });
    return () => { live = false; };
  }, []);
  const when = (iso: string | null) => (iso ? formatDateTimeIST(iso) : "");   // REV-1 M3: IST
  return (
    <Card title="Support access">
      <div data-support-access className="space-y-2 text-xs">
        <p className="text-fg-muted">ATP support can open a read-only view of your account to help you. Nothing can be changed, placed or downloaded during it, and every view is listed here.</p>
        {views === null ? <SkeletonRows rows={2} label="Loading" /> : views.length === 0 ? (
          <p className="text-fg">Support has never viewed your account.</p>
        ) : (
          <ul className="divide-y divide-border">
            {views.map((v) => (
              <li key={v.id} data-support-view={v.id} className="py-1.5">
                <div className="text-fg">
                  {v.by} viewed your account (read only) · {when(v.started_at)}{v.active ? " · open now" : v.ended_at ? ` to ${when(v.ended_at)}` : ""}
                </div>
                <div className="text-fg-muted break-words">Reason: {v.reason}</div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}
