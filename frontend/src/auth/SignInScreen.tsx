import { Eye, EyeOff, TriangleAlert } from "lucide-react";
import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router";
import { api } from "../api/client";
import { ApiError, friendlyError } from "../api/errors";
import { ThemeToggle } from "../components/AppearanceCard";
import { LogoMark } from "../components/Logo";
import { Button } from "../components/primitives";
import LegalConsentField from "../legal/LegalConsentField";
import { acceptanceFor, type LegalSummary } from "../legal/legal";
import { ROLE_LABELS, type InviteInfo } from "../types";
import { useAuth } from "./AuthContext";
import GoogleSignInButton from "./GoogleSignInButton";
import { takeGoogleReturn } from "./google";
import StatusLink from "../status/StatusLink";
import SiteLinks from "../site/SiteLinks";

/**
 * UX1 (audit P01, problem 2): the sign-in screen a signed-out visitor gets at any address (App.tsx SignInGate) - the
 * brand, one card, nothing else: no sidebar, no phone bar, no data. The same card handles every way in:
 * - sign in (email, password, "Forgot password?"), then the two-factor step when the account has one;
 * - "Create an account" - shown only while the platform accepts new accounts (flag `self_signup`, read without a
 *   login from /auth/sign-up-open; closed while that read has not answered);
 * - a team invite link (`?invite=`), a password reset link (`?reset=`) and an e-mail verification link (`?verify=`).
 * Every field has a label tied to it; a failed attempt shows one plain line above the button.
 * GS-1 (#336, Google sign-in): "Continue with Google" under the primary button in "signin" mode - it renders nothing
 * until the server says Google sign-in is set up; back from Google, the two-factor step or the reason it did not work.
 */
export type SignInMode = "signin" | "register" | "invite" | "forgot" | "reset" | "mfa";

const HEADINGS: Record<SignInMode, string> = {
  signin: "Sign in", register: "Create your account", invite: "Join your team", forgot: "Forgot your password?",
  reset: "Choose a new password", mfa: "Two-factor check",
};
const ACTIONS: Record<SignInMode, string> = {
  signin: "Sign in", register: "Create account", invite: "Join team", forgot: "Send reset link", reset: "Set new password",
  mfa: "Verify",
};

const inputClass = "h-11 w-full rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg "
  + "placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/40 read-only:text-fg-muted";

function Field({ id, label, children, hint }: { id: string; label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="space-y-space-1">
      <label htmlFor={id} className="block text-t14 font-medium text-fg">{label}</label>
      {children}
      {hint && <p id={`${id}-hint`} className="text-t12 text-fg-muted">{hint}</p>}
    </div>
  );
}

function PasswordInput({ id, value, onChange, autoComplete, minLength, describedBy }: {
  id: string; value: string; onChange: (v: string) => void; autoComplete: string; minLength: number; describedBy?: string;
}) {
  const [shown, setShown] = useState(false);
  return (
    <div className="relative">
      <input id={id} type={shown ? "text" : "password"} required minLength={minLength} autoComplete={autoComplete}
        aria-describedby={describedBy} className={`${inputClass} pr-12`} value={value} onChange={(e) => onChange(e.target.value)} />
      <button type="button" onClick={() => setShown((s) => !s)} aria-label={shown ? "Hide password" : "Show password"}
        aria-pressed={shown} aria-controls={id}
        className="absolute right-0 top-0 flex h-11 w-11 items-center justify-center rounded-control text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        {shown ? <EyeOff size={18} aria-hidden /> : <Eye size={18} aria-hidden />}
      </button>
    </div>
  );
}

const linkClass = "text-t14 font-medium text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand rounded-control";

export default function SignInScreen() {
  const { login, completeMfaLogin, register, acceptInvite, resetPassword } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const ids = { email: useId(), password: useId(), code: useId(), beta: useId(), error: useId() };
  const [mode, setMode] = useState<SignInMode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [mfaCode, setMfaCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [signUpOpen, setSignUpOpen] = useState(false);
  const [inviteToken, setInviteToken] = useState<string | null>(null);
  const [invite, setInvite] = useState<InviteInfo | null>(null);
  const [resetToken, setResetToken] = useState<string | null>(null);
  const [resetHint, setResetHint] = useState<{ email_hint: string; valid: boolean; reason: string | null } | null>(null);
  // PW6: the closed-beta invite code - the field opens when the server asks for one (or the visitor says they have one).
  const [betaCode, setBetaCode] = useState("");
  const [betaField, setBetaField] = useState(false);
  // Z10-f: the legal documents' current versions, accepted with the account (only while the server publishes them).
  const [legalDocs, setLegalDocs] = useState<LegalSummary[] | null>(null);
  const [legalAccepted, setLegalAccepted] = useState(false);
  const needsLegal = mode === "register" || mode === "invite";

  useEffect(() => {
    api.signUpOpen().then((r) => setSignUpOpen(r.open === true)).catch(() => setSignUpOpen(false));
  }, []);

  useEffect(() => {
    if (!needsLegal || legalDocs) return;
    api.legalDocuments().then((r) => setLegalDocs(r.documents)).catch(() => setLegalDocs(null));
  }, [needsLegal, legalDocs]);

  // The links the server e-mails land here with a token in the query. Read it once, then take it out of the address
  // through the router (so the page reached after signing in does not see it again).
  useEffect(() => {
    // GS-1: back from Google - the authenticator step, or why it did not work, in plain words.
    const google = takeGoogleReturn();
    if (google?.kind === "mfa") {
      setMfaToken(google.mfaToken);
      setMode("mfa");
      setNotice("Google confirmed your email. Enter your authenticator code to finish signing in.");
    } else if (google?.kind === "error") {
      setError(google.message);
    }
    const params = new URLSearchParams(location.search);
    const verify = params.get("verify");
    const reset = params.get("reset");
    const inviteParam = params.get("invite");
    if (!verify && !reset && !inviteParam) return;
    for (const k of ["verify", "reset", "invite"]) params.delete(k);
    const rest = params.toString();
    navigate({ pathname: location.pathname, search: rest ? `?${rest}` : "" }, { replace: true });
    if (verify) {
      api.verifyEmail(verify)
        .then((u) => setNotice(`${u.email} is verified. Sign in to continue.`))
        .catch((e) => setError(friendlyError(e)));
    }
    if (reset) {
      setResetToken(reset);
      setMode("reset");
      api.resetInfo(reset).then(setResetHint).catch((e) => setError(friendlyError(e)));
    } else if (inviteParam) {
      setInviteToken(inviteParam);
      setMode("invite");
      api.inviteInfo(inviteParam).then((info) => { setInvite(info); setEmail(info.email); }).catch((e) => setError(friendlyError(e)));
    }
  }, []);

  function switchTo(next: SignInMode) {
    setError(null);
    setNotice(null);
    setMode(next);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "signin") {
        const challenge = await login(email, password);
        if (challenge) { setMfaToken(challenge); setMode("mfa"); }
      } else if (mode === "mfa" && mfaToken) {
        await completeMfaLogin(mfaToken, mfaCode.replace(/\s+/g, ""));
      } else if (mode === "invite" && inviteToken) {
        await acceptInvite(inviteToken, password, legalDocs && legalAccepted ? acceptanceFor(legalDocs) : undefined);
      } else if (mode === "reset" && resetToken) {
        await resetPassword(resetToken, password);
      } else if (mode === "forgot") {
        setNotice((await api.forgotPassword(email)).detail);
      } else if (mode === "register") {
        await register(email, password, betaCode.trim() || undefined, legalDocs && legalAccepted ? acceptanceFor(legalDocs) : undefined);
      }
    } catch (err) {
      if (mode === "register" && err instanceof ApiError && /"code"\s*:\s*"beta_(invite|cohort)_/.test(err.body)) setBetaField(true);
      // Z10-f: a document changed while the form was open (or the list did not load) - load it again and ask again.
      if (err instanceof ApiError && (err.body.includes("legal_version_changed") || err.body.includes("legal_acceptance_required"))) {
        setLegalAccepted(false);
        setLegalDocs(null);
      }
      setError(friendlyError(err));
    } finally {
      setBusy(false);
    }
  }

  const intro = mode === "signin" ? "Welcome back. Sign in to your trading desk."
    : mode === "mfa" ? "Enter the 6-digit code from your authenticator app, or one of your backup codes."
    : mode === "forgot" ? "Enter the email you sign in with. We send you a link to choose a new password."
    : mode === "reset" ? (resetHint ? (resetHint.valid ? `For ${resetHint.email_hint}. You will be signed out on every other device.` : resetHint.reason ?? "This link is no longer valid.") : "Checking your link…")
    : mode === "invite" ? (invite ? (invite.valid ? `You were invited to ${invite.tenant_name} as ${ROLE_LABELS[invite.role] ?? invite.role}. Choose a password to join.` : invite.reason ?? "This invitation is no longer valid.") : "Checking your invitation…")
    : "Start on paper first.";
  const blocked = (needsLegal && legalDocs !== null && !legalAccepted)
    || (mode === "invite" && !(invite && invite.valid)) || (mode === "reset" && !(resetHint && resetHint.valid));
  const newPassword = mode === "register" || mode === "invite" || mode === "reset";

  return (
    <div className="flex min-h-screen flex-col bg-surface text-fg" data-screen="sign-in">
      <div className="flex justify-end px-space-4 pt-[max(var(--space-2),env(safe-area-inset-top))]">
        <ThemeToggle />
      </div>
      <main className="flex flex-1 items-start justify-center px-space-4 pb-space-10 pt-space-4 sm:items-center sm:pt-0">
        <div className="w-full max-w-[420px] space-y-space-6">
          <div className="flex flex-col items-center gap-space-3 text-center">
            <LogoMark size={48} />
            <p className="text-t20 font-bold tracking-wide text-fg">AiAlgoDesk</p>
          </div>
          <section aria-labelledby="sign-in-heading" className="rounded-panel border border-subtle bg-surface-1 p-space-5 shadow-card sm:p-space-6">
            <h1 id="sign-in-heading" className="text-t20 font-semibold text-fg">{HEADINGS[mode]}</h1>
            <p className="mt-space-1 text-t14 text-fg-muted">{intro}</p>
            <form onSubmit={submit} className="mt-space-5 space-y-space-4" aria-describedby={error ? ids.error : undefined}>
              {mode === "mfa" && (
                <Field id={ids.code} label="Authenticator code">
                  <input id={ids.code} autoFocus required inputMode="numeric" autoComplete="one-time-code" placeholder="123 456"
                    className={`${inputClass} font-mono tracking-widest`} value={mfaCode} onChange={(e) => setMfaCode(e.target.value)} />
                </Field>
              )}
              {mode !== "reset" && mode !== "mfa" && (
                <Field id={ids.email} label="Email">
                  <input id={ids.email} type="email" required autoComplete="email" inputMode="email" readOnly={mode === "invite"}
                    className={inputClass} value={email} onChange={(e) => setEmail(e.target.value)} />
                </Field>
              )}
              {mode !== "forgot" && mode !== "mfa" && (
                <Field id={ids.password} label={mode === "reset" ? "New password" : "Password"}
                  hint={newPassword ? "At least 10 characters, not a common password." : undefined}>
                  <PasswordInput id={ids.password} value={password} onChange={setPassword}
                    autoComplete={newPassword ? "new-password" : "current-password"} minLength={newPassword ? 10 : 1}
                    describedBy={newPassword ? `${ids.password}-hint` : undefined} />
                </Field>
              )}
              {mode === "signin" && (
                <div className="-mt-space-2 flex justify-end">
                  <button type="button" onClick={() => switchTo("forgot")} className={linkClass}>Forgot password?</button>
                </div>
              )}
              {mode === "register" && (betaField ? (
                <Field id={ids.beta} label="Beta invite code">
                  <input id={ids.beta} autoComplete="off" spellCheck={false} maxLength={64} placeholder="XXXX-XXXX-XXXX-XXXX"
                    className={`${inputClass} font-mono uppercase tracking-wider`} value={betaCode} onChange={(e) => setBetaCode(e.target.value)} />
                </Field>
              ) : (
                <button type="button" onClick={() => setBetaField(true)} className="text-t14 text-fg-muted hover:text-fg hover:underline">
                  Have a beta invite code?
                </button>
              ))}
              {needsLegal && legalDocs && <LegalConsentField checked={legalAccepted} onChange={setLegalAccepted} />}
              {notice && <p role="status" className="text-t14 text-up">{notice}</p>}
              {error && (
                <p id={ids.error} role="alert" className="flex items-start gap-space-2 rounded-control bg-warn-tint px-space-3 py-space-2 text-t14 text-fg">
                  <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0 text-warn" />
                  <span>{error}</span>
                </p>
              )}
              <Button type="submit" variant="primary" loading={busy} disabled={blocked} className="h-11 w-full text-t14">
                {busy ? "Please wait…" : ACTIONS[mode]}
              </Button>
            </form>
            {mode === "signin" && <GoogleSignInButton />}
            <div className="mt-space-5 flex flex-wrap items-center justify-center gap-x-space-4 gap-y-space-2 border-t border-subtle pt-space-4 text-t14 text-fg-muted">
              {mode === "signin" ? (
                signUpOpen ? (
                  <span>New here? <button type="button" onClick={() => switchTo("register")} className={linkClass}>Create an account</button></span>
                ) : (
                  <span>New here? Ask your team owner for an invite link.</span>
                )
              ) : (
                <button type="button" onClick={() => { setMfaToken(null); setMfaCode(""); switchTo("signin"); }} className={linkClass}>
                  {mode === "register" ? "Already have an account? Sign in" : "Back to sign in"}
                </button>
              )}
            </div>
          </section>
          <p className="text-center text-t12 text-fg-muted">Information and education, not investment advice.</p>
          <footer className="-mt-space-4 flex flex-col items-center gap-space-2 empty:hidden" data-sign-in-footer><StatusLink /><SiteLinks /></footer>
        </div>
      </main>
    </div>
  );
}
