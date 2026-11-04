import { ExternalLink, TriangleAlert } from "lucide-react";
import { useId, useState } from "react";
import { api, ApiError } from "../api/client";
import UpstoxRedirectUrl from "../components/UpstoxRedirectUrl";
import { failureText } from "./ConnectPanel";
import { BTN_PRIMARY, BTN_SECONDARY, ResponsiveDialog } from "./parts";

/**
 * Owner 4 Nov: "regular access token ni kase login karu, live trading sathi analytics token useful nahi". The Upstox
 * Analytics Token is read-only (market data, charts, paper trading) and can never place an order; orders need Upstox's
 * daily login (the access token). This sheet is that login: Upstox's own login page (OAuth, the existing
 * /broker/upstox/oauth/start + callback), which needs the trader's own Upstox app API key and secret once. The Analytics
 * Token stays for data - saving the key and secret keeps it (the server merges the stored payload).
 * The LIVE switches stay the owner's (live_trading / phase0_paper_only): this only gives the broker session.
 */

const INPUT = "h-11 w-full rounded-control border border-border bg-surface-2 px-space-3 text-sm text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:text-t14";

/** The server's "store the API key / secret first" answers of /oauth/start (404 no row key, 400 no key or secret). */
export function needsAppKeys(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 400 || e.status === 404) && /api[ _]?key|api_secret|secret/i.test(`${e.detail ?? ""} ${e.body ?? ""}`);
}

export default function UpstoxOrderLogin({ open, onOpenChange, accountLabel = "primary", onChanged }: {
  open: boolean; onOpenChange: (open: boolean) => void; accountLabel?: string; onChanged?: () => void;
}) {
  const ids = useId();
  const [keys, setKeys] = useState(false);              // the one-time API key / secret step is shown
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [opened, setOpened] = useState<{ link: string; opened: boolean } | null>(null);

  // Upstox's login page, opened inside the click (a tab opened later is blocked by the browser).
  function login() {
    setBusy(true);
    setError(null);
    let tab: Window | null = null;
    try { tab = window.open("about:blank", "_blank"); if (tab) tab.opener = null; } catch { tab = null; }
    api.upstoxOAuthStart(accountLabel)
      .then(({ authorization_url }) => {
        if (tab) tab.location.href = authorization_url;
        setOpened({ link: authorization_url, opened: !!tab });
        onChanged?.();
      })
      .catch((e: unknown) => {
        tab?.close();
        if (needsAppKeys(e)) { setKeys(true); return; }
        setError(failureText(e, "Upstox"));
      })
      .finally(() => setBusy(false));
  }

  async function saveKeys() {
    setBusy(true);
    setError(null);
    try {
      await api.storeBrokerCredentials("upstox", { api_key: apiKey.trim(), api_secret: apiSecret.trim() }, accountLabel);
      setKeys(false);
      setApiKey("");
      setApiSecret("");
      onChanged?.();
    } catch (e) {
      setError(failureText(e, "Upstox"));
    } finally {
      setBusy(false);
    }
  }

  const ready = apiKey.trim().length >= 8 && apiSecret.trim().length >= 4;
  return (
    <ResponsiveDialog open={open} onOpenChange={onOpenChange} title="Upstox login for orders" marker="upstox-order-login">
      <div className="space-y-space-4" data-upstox-order-login>
        <ul className="space-y-space-1 text-t14 text-fg">
          <li><span className="font-semibold">Analytics Token</span>: charts and market data, saved once a year. It cannot place orders.</li>
          <li><span className="font-semibold">Upstox login</span>: needed for orders. Upstox ends it every morning, so log in once each trading day.</li>
        </ul>

        {keys ? (
          <div className="space-y-space-3" data-upstox-keys>
            <p className="text-t14 text-fg">One time only: your Upstox app&apos;s API key and secret.</p>
            <ol className="list-decimal space-y-space-1 pl-space-5 text-t13 text-fg-muted">
              <li>Upstox <a href="https://account.upstox.com/developer/apps" target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">Developer Apps</a>: open your app (or create one).</li>
              <li>Set its <span className="font-semibold text-fg">Redirect URL</span> to the address below.</li>
              <li>Copy the <span className="font-semibold text-fg">API Key</span> and the <span className="font-semibold text-fg">API Secret</span> here.</li>
            </ol>
            <UpstoxRedirectUrl selected />
            <div className="space-y-space-1">
              <label htmlFor={`${ids}-key`} className="text-t13 font-medium text-fg">API Key</label>
              <input id={`${ids}-key`} data-field="api_key" autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false}
                value={apiKey} onChange={(e) => setApiKey(e.target.value)} className={INPUT} />
            </div>
            <div className="space-y-space-1">
              <label htmlFor={`${ids}-secret`} className="text-t13 font-medium text-fg">API Secret</label>
              <input id={`${ids}-secret`} data-field="api_secret" type="password" autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false}
                value={apiSecret} onChange={(e) => setApiSecret(e.target.value)} className={INPUT} />
            </div>
            <button type="button" onClick={() => void saveKeys()} disabled={!ready || busy} aria-busy={busy || undefined} className={`${BTN_PRIMARY} w-full`} data-save-keys>
              Save
            </button>
          </div>
        ) : (
          <button type="button" onClick={login} disabled={busy} aria-busy={busy || undefined} className={`${BTN_PRIMARY} w-full`} data-upstox-login>
            <ExternalLink size={16} aria-hidden /> Log in to Upstox
          </button>
        )}

        {opened && !keys && (
          <p className="text-t13 text-fg-muted" role="status">
            {opened.opened
              ? "The Upstox login page opened in a new tab. Sign in there, then come back here - this page updates by itself."
              : <>Your browser blocked the new tab. <a href={opened.link} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">Open the Upstox login page</a>.</>}
          </p>
        )}
        {error && <p role="alert" className="flex items-start gap-space-2 text-t14 text-fg"><TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0 text-warn" />{error}</p>}
        {!keys && (
          <button type="button" onClick={() => { setKeys(true); setError(null); }} className={`${BTN_SECONDARY} w-full`} data-change-keys>
            Change the API key / secret
          </button>
        )}
      </div>
    </ResponsiveDialog>
  );
}
