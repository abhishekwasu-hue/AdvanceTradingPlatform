import { KeyRound, LogIn, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BrokerTokenInfo } from "../types";

/**
 * "Is my broker session alive today?" - the one thing an operator must check every trading
 * morning. Indian retail broker tokens (Upstox 03:30 IST, Kite 06:00 IST) expire daily with no
 * refresh token, so LIVE deployments cannot run until someone logs in again. For Upstox the
 * button drives the whole OAuth round-trip; other brokers still need a fresh token pasted in.
 */
export default function BrokerTokenBanner({ compact = false }: { compact?: boolean }) {
  const [tokens, setTokens] = useState<BrokerTokenInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.brokerTokenStatus().then(setTokens).catch((e) => setError(String(e)));
  }, []);

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
            key={t.broker_name}
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
            </div>
          </div>
        );
      })}
    </div>
  );
}
