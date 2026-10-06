import { ExternalLink, KeyRound, LogIn, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BrokerTokenInfo } from "../types";

/**
 * "Is my broker session alive today?" - the one thing an operator must check every trading
 * morning. Indian retail broker tokens (Upstox 03:30 IST, Kite/Fyers 06:00 IST) expire daily with
 * no refresh token, so LIVE deployments cannot run until someone logs in again. For Upstox the
 * button drives the whole OAuth round-trip; Fyers and Kite send the browser to the redirect URL
 * registered on the broker's console instead, so the banner opens their login page and takes the
 * pasted one-time code (or the whole redirected address) and finishes the login in one step.
 */
export default function BrokerTokenBanner({ compact = false }: { compact?: boolean }) {
  const [tokens, setTokens] = useState<BrokerTokenInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [codes, setCodes] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, { ok: boolean; text: string; link?: string }>>({});

  useEffect(() => {
    api.brokerTokenStatus().then(setTokens).catch((e) => setError(String(e)));
  }, []);

  function keyOf(t: BrokerTokenInfo) {
    return `${t.broker_name}/${t.account_label ?? "primary"}`;
  }

  async function openLogin(t: BrokerTokenInfo) {
    setBusy(true);
    setNotes((n) => ({ ...n, [keyOf(t)]: { ok: true, text: "" } }));
    // Open the tab synchronously inside the click (popup blockers refuse a window opened after an await),
    // then point it at the broker once the URL is back; the note always carries the link as a fallback.
    const win = window.open("about:blank", "_blank");
    if (win) win.opener = null;
    try {
      const { authorization_url, code_param } = await api.brokerLoginUrl(t.broker_name, t.account_label ?? "primary");
      if (win) win.location.href = authorization_url;
      setNotes((n) => ({ ...n, [keyOf(t)]: { ok: true, link: authorization_url, text: win
        ? `Login page opened in a new tab. After logging in, copy the ${code_param} from the address bar (or the whole address) and paste it here.`
        : `The browser blocked the new tab - open the login page with the link, then paste the ${code_param} here.` } }));
    } catch (e) {
      if (win) win.close();
      setNotes((n) => ({ ...n, [keyOf(t)]: { ok: false, text: String(e).replace(/^Error:\s*/, "") } }));
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(t: BrokerTokenInfo) {
    const code = (codes[keyOf(t)] ?? "").trim();
    if (!code) return;
    setBusy(true);
    try {
      const updated = await api.brokerLoginCode(t.broker_name, code, t.account_label ?? "primary");
      setTokens((rows) => rows.map((r) => (keyOf(r) === keyOf(t) ? { ...r, ...updated } : r)));
      setCodes((c) => ({ ...c, [keyOf(t)]: "" }));
      setNotes((n) => ({ ...n, [keyOf(t)]: { ok: true, text: `Logged in - session token stored (encrypted) and valid until ${updated.token_expires_at ? new Date(updated.token_expires_at).toLocaleString() : "the broker's daily expiry"}.` } }));
    } catch (e) {
      setNotes((n) => ({ ...n, [keyOf(t)]: { ok: false, text: String(e).replace(/^Error:\s*/, "") } }));
    } finally {
      setBusy(false);
    }
  }

  async function loginToUpstox() {
    setBusy(true);
    setError(null);
    try {
      const { authorization_url } = await api.upstoxOAuthStart();
      window.location.href = authorization_url;
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  if (error) return <div className="text-xs text-danger">{error}</div>;
  if (tokens.length === 0) return null;

  return (
    <div className="space-y-2">
      {tokens.map((t) => {
        const ok = !t.needs_login;
        return (
          <div
            key={keyOf(t)}
            className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-xs ${
              ok ? "border-accent/30 bg-accent/[0.06]" : "border-warn/40 bg-warn/[0.08]"
            }`}
          >
            <div className="flex items-center gap-2">
              {ok ? <ShieldCheck size={14} className="text-accent" /> : <KeyRound size={14} className="text-warn" />}
              <span className="font-bold capitalize text-slate-100">{t.broker_name}</span>
              <span className={`rounded border px-1.5 py-0.5 font-semibold ${ok ? "border-accent/40 text-accent" : "border-warn/40 text-warn"}`}>
                {t.token_status}
              </span>
              {!compact && (
                <span className="text-muted">
                  {ok && t.token_expires_at
                    ? `session valid until ${new Date(t.token_expires_at).toLocaleString()}`
                    : t.token_status === "EXPIRED"
                      ? "session expired - LIVE deployments are on hold until you log in again"
                      : "session not yet verified - authenticate or log in to enable LIVE"}
                </span>
              )}
            </div>
            <div className="flex items-center gap-3">
              {t.oauth_supported && (
                <button
                  onClick={loginToUpstox}
                  disabled={busy}
                  className="flex items-center gap-1.5 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 disabled:opacity-50"
                >
                  <LogIn size={12} /> {busy ? "Redirecting…" : ok ? "Re-login to Upstox" : "Login to Upstox"}
                </button>
              )}
              {!compact && t.oauth_callback_url && (
                <span className="text-muted hidden md:inline" title="Register this exact redirect URI on the Upstox developer console">
                  redirect URI: <code className="font-mono">{t.oauth_callback_url}</code>
                </span>
              )}
              {t.login_url_supported && (
                <button
                  onClick={() => void openLogin(t)}
                  disabled={busy}
                  className="flex items-center gap-1.5 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 disabled:opacity-50"
                  title={`Opens the ${t.broker_name} login page in a new tab; paste the ${t.code_param ?? "code"} it returns below`}
                >
                  <ExternalLink size={12} /> {ok ? `Re-login to ${t.broker_name}` : `Open ${t.broker_name} login`}
                </button>
              )}
            </div>
            {t.login_url_supported && !compact && (
              <div className="basis-full flex flex-wrap items-center gap-2">
                <input
                  type="password"
                  autoComplete="off"
                  className="flex-1 min-w-[16rem] rounded bg-panel2 border border-border px-2 py-1 text-xs"
                  placeholder={`Paste today's ${t.code_param ?? "code"} (or the whole redirected address)`}
                  value={codes[keyOf(t)] ?? ""}
                  onChange={(e) => setCodes((c) => ({ ...c, [keyOf(t)]: e.target.value }))}
                  onKeyDown={(e) => { if (e.key === "Enter") void submitCode(t); }}
                />
                <button
                  onClick={() => void submitCode(t)}
                  disabled={busy || !(codes[keyOf(t)] ?? "").trim()}
                  className="flex items-center gap-1.5 rounded border border-border px-3 py-1 text-slate-100 hover:bg-panel2 disabled:opacity-50"
                >
                  <LogIn size={12} /> {busy ? "Logging in…" : "Login with code"}
                </button>
                {notes[keyOf(t)]?.text && (
                  <span className={`basis-full ${notes[keyOf(t)].ok ? "text-accent" : "text-danger"}`}>
                    {notes[keyOf(t)].text}
                    {notes[keyOf(t)].link && (
                      <> <a href={notes[keyOf(t)].link} target="_blank" rel="noopener noreferrer" className="underline">Open the {t.broker_name} login page</a></>
                    )}
                  </span>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
