import { ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import { api } from "../api/client";
import { oauthErrorText } from "../utils/oauthErrors";
import { useAuth } from "../auth/AuthContext";
import AlertChannelsCard from "../components/AlertChannelsCard";
import AiProviderCard from "../components/AiProviderCard";
import ApiKeysCard from "../components/ApiKeysCard";
import BillingCard from "../components/BillingCard";
import BrokerAccountsCard from "../components/BrokerAccountsCard";
import BrokerTokenBanner from "../components/BrokerTokenBanner";
import StaticIpCard from "../components/StaticIpCard";
import StoredBrokerList from "../components/StoredBrokerList";
import VisionByokCard from "../components/VisionByokCard";
import { Card } from "../components/ui";
import AppearanceCard from "../components/AppearanceCard";
import { PageHeader } from "../components/primitives";
import { SetupLinkV2 } from "../setup/SetupChipV2";
import { GO_LIVE_PATH } from "../setup/getStarted";
import GoLiveChecklistV2 from "../setup/GoLiveChecklistV2";
import ShowGetStartedV2 from "../setup/ShowGetStartedV2";
import { useFeatures } from "../ui/useFeatures";
import type { BrokerCredentialsInput, StoredBrokerInfo, WebhookTokenResponse } from "../types";

const CRED_FIELDS: { key: keyof BrokerCredentialsInput; label: string }[] = [
  { key: "api_key", label: "API Key" },
  { key: "api_secret", label: "API Secret" },
  { key: "access_token", label: "Access Token" },
  { key: "request_token", label: "Request Token" },
  { key: "client_id", label: "Client ID" },
  { key: "pin", label: "PIN" },
  { key: "totp_secret", label: "TOTP Secret" },
  { key: "redirect_uri", label: "Redirect URI" },
];

export default function SettingsPage() {
  const v2 = useFeatures().on("ui_v2");
  const { user, loading: authLoading } = useAuth();
  const [brokers, setBrokers] = useState<string[]>([]);
  const [stored, setStored] = useState<StoredBrokerInfo[]>([]);
  const [selectedBroker, setSelectedBroker] = useState("");
  const [credentials, setCredentials] = useState<BrokerCredentialsInput>({});
  const [accountLabel, setAccountLabel] = useState("primary");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [webhook, setWebhook] = useState<WebhookTokenResponse | null>(null);
  const [webhookCopied, setWebhookCopied] = useState(false);
  const [oauthOutcome, setOauthOutcome] = useState<{ ok: boolean; text: string } | null>(null);
  // P3: Settings → Advanced holds the Go-live checklist; "Prepare for LIVE" (/settings#go-live) opens it, and the
  // Get started card's Telegram step leads to /settings#alerts.
  const location = useLocation();
  const [advancedOpen, setAdvancedOpen] = useState(() => ADVANCED_HASHES.has(location.hash));

  function refreshStored() {
    api.listStoredBrokerCredentials().then(setStored).catch((e) => setError(String(e)));
  }

  useEffect(() => {
    api.availableBrokers().then((r) => {
      setBrokers(r.brokers);
      if (r.brokers.length) setSelectedBroker(r.brokers[0]);
    });
  }, []);

  useEffect(() => {
    if (user) {
      refreshStored();
      api.getWebhookToken().then(setWebhook).catch((e) => setError(String(e)));
    }
  }, [user]);

  // Landing back here after the Upstox OAuth round-trip: the callback redirects to the app root
  // with the outcome in the query string. Show it once, then clean the URL.
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      if (!params.has("broker")) return;
      const broker = params.get("broker") ?? "broker";
      if (params.get("connected") === "1") {
        setOauthOutcome({ ok: true, text: `Logged in to ${broker} - session token stored (encrypted) and marked valid for today.` });
      } else if (params.get("error")) {
        setOauthOutcome({ ok: false, text: `${broker} login did not complete: ${oauthErrorText(params.get("error") ?? "")}` });
      }
      window.history.replaceState(window.history.state, "", window.location.pathname);
    } catch {
      // URL APIs unavailable - nothing to show
    }
  }, []);

  useEffect(() => {
    const id = location.hash.slice(1);
    if (!id || authLoading || !user) return;
    if (ADVANCED_HASHES.has(location.hash)) setAdvancedOpen(true);
    // After the shell's own scroll to the top of a new page, and once more when the cards above have loaded.
    const go = () => document.getElementById(id)?.scrollIntoView({ block: "start" });
    const timers = [window.setTimeout(go, 0), window.setTimeout(go, 500)];
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [location.hash, location.key, authLoading, user]);

  async function handleCopyWebhookUrl() {
    if (!webhook?.webhook_url) return;
    try {
      await navigator.clipboard.writeText(new URL(webhook.webhook_url, window.location.origin).toString());
      setWebhookCopied(true);
      setTimeout(() => setWebhookCopied(false), 2000);
    } catch {
      // clipboard unavailable - the URL is still shown in the input for manual copy
    }
  }

  async function handleRotateWebhook() {
    setBusy(true);
    setError(null);
    try {
      setWebhook(await api.rotateWebhookToken());
      setMessage("Webhook URL rotated - update it in TradingView's alert settings.");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleStore() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api.storeBrokerCredentials(selectedBroker, credentials, accountLabel || "primary");
      setMessage(`Credentials for ${selectedBroker} (${accountLabel || "primary"}) stored (encrypted at rest).`);
      setCredentials({});
      refreshStored();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleAuthenticate(name: string, label = "primary") {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api.authenticateBroker(name, label);
      setMessage(`Authenticated with ${name} (${label}).`);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(name: string, label = "primary") {
    await api.deleteBrokerCredentials(name, label);
    refreshStored();
  }

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <PageHeader title="Settings" />
        <AppearanceCard />
        <Card>
          <p className="text-sm text-fg-muted">Log in from the Account tab to manage broker credentials.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Settings"
        description="Broker credentials are encrypted at rest and only ever decrypted in memory when you authenticate - never logged, never returned in plaintext by any API response."
        actions={<Link to={GO_LIVE_PATH} className="inline-flex min-h-11 items-center gap-1 rounded-md text-sm font-medium text-brand hover:underline md:min-h-0">Prepare for LIVE <ChevronRight size={14} aria-hidden /></Link>} />
      {/* UI2-7: Settings → Setup (the wizard), only while ui_v2 is on; off, nothing renders. P3: the go-live checklist is
          under Advanced, below. */}
      {v2 && <SetupLinkV2 />}
      <AppearanceCard />

      {oauthOutcome && (
        <div className={`rounded-lg border px-3 py-2 text-sm ${oauthOutcome.ok ? "border-up/40 bg-up/10 text-up" : "border-down/40 bg-down/10 text-down"}`}>
          {oauthOutcome.text}
        </div>
      )}

      <Card title="Broker session health">
        <p className="text-xs text-fg-muted mb-3">
          Broker access tokens expire every trading morning (Upstox 03:30 IST, Kite and Fyers 06:00 IST)
          and cannot be refreshed automatically - log in again each day before the market opens, or
          LIVE deployments stay on hold. For Upstox the button below completes the login in your
          browser and stores the new token for you. For Fyers and Kite it opens the broker's login
          page; paste the code the broker returns (or the whole redirected address) and the session is
          stored for the day. Fyers needs the app's Redirect URL stored once (field below).
        </p>
        <BrokerTokenBanner key={stored.length} />
      </Card>

      <Card title={`Connected brokers (${stored.length})`}>
        <StoredBrokerList stored={stored} busy={busy} onAuthenticate={handleAuthenticate} onDelete={handleDelete} />
      </Card>

      <Card title="Add / update broker credentials">
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-fg-muted mb-1">Broker</label>
            <select
              className="w-full sm:w-64 rounded bg-surface-2 border border-border px-2 py-1.5 text-sm capitalize"
              value={selectedBroker}
              onChange={(e) => setSelectedBroker(e.target.value)}
            >
              {brokers.map((b) => (
                <option key={b} value={b} className="capitalize">{b}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs text-fg-muted mb-1">Account label <span className="text-fg-muted">(a second account at the same broker gets its own label)</span></label>
            <input className="w-full sm:w-64 rounded bg-surface-2 border border-border px-2 py-1.5 text-sm" value={accountLabel} onChange={(e) => setAccountLabel(e.target.value)} placeholder="primary" />
          </div>

          <p className="text-xs text-fg-muted">
            Fill only what you are changing - a field left blank keeps its stored value (e.g. paste just today's
            Access Token and the saved API key/secret stay). Values are encrypted on save and never shown again.
          </p>

          <div className="grid sm:grid-cols-2 gap-3">
            {CRED_FIELDS.map((f) => (
              <div key={f.key}>
                <label className="block text-xs text-fg-muted mb-1">{f.label}</label>
                <input
                  type="password"
                  autoComplete="off"
                  className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
                  value={credentials[f.key] ?? ""}
                  onChange={(e) => setCredentials({ ...credentials, [f.key]: e.target.value })}
                />
              </div>
            ))}
          </div>

          {error && <div className="text-sm text-down">{error}</div>}
          {message && <div className="text-sm text-up">{message}</div>}

          <button
            onClick={handleStore}
            disabled={busy || !selectedBroker}
            className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
          >
            {busy ? "Saving…" : "Store credentials"}
          </button>
        </div>
      </Card>

      <BrokerAccountsCard refreshKey={stored.length} />
      <StaticIpCard />

      <BillingCard />

      <div id="alerts" className="scroll-mt-4">
        <AlertChannelsCard />
      </div>

      <ApiKeysCard />

      <AiProviderCard />

      <VisionByokCard />

      <Card title="TradingView webhook">
        <p className="text-sm text-fg-muted mb-3">
          Paste this URL into a TradingView alert's "Webhook URL" field, with a JSON message body
          of <code className="text-xs">{"{ strategy_id, symbol, direction, entry, stop_loss, target1, target2?, alert_id? }"}</code>.
          Every alert runs through the same risk engine and kill switches as a manual paper
          execute. The token in the URL is the only credential protecting it - rotate it if it
          ever leaks.
        </p>
        {webhook && !webhook.webhook_url && (
          <p className="text-xs text-fg-muted mb-2">
            The URL is stored only as a hash on the server (it is a credential). Press <b>Rotate</b> to get a new
            one - it is shown once, so copy it into TradingView right away.
            {webhook.configured ? " The current URL keeps working until you rotate." : ""}
          </p>
        )}
        {webhook && (
          <div className="space-y-2">
            {webhook.webhook_url && (
            <div className="flex gap-2">
              <input
                readOnly
                className="flex-1 rounded bg-surface-2 border border-border px-2 py-1.5 text-xs font-mono"
                value={new URL(webhook.webhook_url, window.location.origin).toString()}
                onFocus={(e) => e.target.select()}
              />
              <button
                onClick={handleCopyWebhookUrl}
                className="rounded border border-border hover:bg-surface-2 text-fg px-3 py-1.5 text-xs shrink-0"
              >
                {webhookCopied ? "Copied!" : "Copy"}
              </button>
            </div>
            )}
            <button
              onClick={handleRotateWebhook}
              disabled={busy}
              className="text-xs text-down hover:underline disabled:opacity-50"
            >
              {webhook.webhook_url ? "Rotate URL (invalidates the old one)" : "Rotate: issue a new webhook URL (owner)"}
            </button>
          </div>
        )}
      </Card>

      {user.role !== "VIEWER" && (
        <Card title="Help">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-fg-muted">Bring back the Get started steps on your Dashboard: broker, paper bot and Telegram alerts.</p>
            <ShowGetStartedV2 className="self-start sm:self-auto" />
          </div>
        </Card>
      )}

      <details id="advanced" open={advancedOpen} onToggle={(e) => setAdvancedOpen(e.currentTarget.open)}
        className="group/advanced scroll-mt-4">
        <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 rounded-md text-sm font-semibold text-fg [&::-webkit-details-marker]:hidden">
          <ChevronRight size={16} aria-hidden className="shrink-0 text-fg-muted transition-transform group-open/advanced:rotate-90" />
          Advanced
        </summary>
        <div id="go-live" className="scroll-mt-4 pt-2">
          {advancedOpen && <GoLiveChecklistV2 />}
        </div>
      </details>
    </div>
  );
}

/** The addresses that open Settings → Advanced (the Go-live checklist). */
const ADVANCED_HASHES: ReadonlySet<string> = new Set(["#go-live", "#advanced"]);
