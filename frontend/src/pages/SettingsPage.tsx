import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card } from "../components/ui";
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
  const { user, loading: authLoading } = useAuth();
  const [brokers, setBrokers] = useState<string[]>([]);
  const [stored, setStored] = useState<StoredBrokerInfo[]>([]);
  const [selectedBroker, setSelectedBroker] = useState("");
  const [credentials, setCredentials] = useState<BrokerCredentialsInput>({});
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [webhook, setWebhook] = useState<WebhookTokenResponse | null>(null);
  const [webhookCopied, setWebhookCopied] = useState(false);

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

  async function handleCopyWebhookUrl() {
    if (!webhook) return;
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
      await api.storeBrokerCredentials(selectedBroker, credentials);
      setMessage(`Credentials for ${selectedBroker} stored (encrypted at rest).`);
      setCredentials({});
      refreshStored();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleAuthenticate(name: string) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api.authenticateBroker(name);
      setMessage(`Authenticated with ${name}.`);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(name: string) {
    await api.deleteBrokerCredentials(name);
    refreshStored();
  }

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-extrabold text-yellow-400">Settings</h1>
        <Card>
          <p className="text-sm text-muted">Log in from the Account tab to manage broker credentials.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-yellow-400">Settings</h1>
        <p className="text-sm font-semibold text-yellow-400/60">
          Broker credentials are encrypted at rest (Fernet) and only ever decrypted in memory
          when you authenticate - never logged, never returned in plaintext by any API response.
        </p>
      </div>

      <Card title={`Connected brokers (${stored.length})`}>
        {stored.length === 0 ? (
          <div className="text-sm text-muted py-2">None stored yet.</div>
        ) : (
          <div className="space-y-1.5">
            {stored.map((s) => (
              <div key={s.broker_name} className="flex items-center justify-between rounded border border-border px-3 py-2 text-sm">
                <div>
                  <span className="font-medium text-slate-200 capitalize">{s.broker_name}</span>{" "}
                  <span className="text-muted text-xs">updated {new Date(s.updated_at).toLocaleString()}</span>
                </div>
                <div className="flex gap-3">
                  <button onClick={() => handleAuthenticate(s.broker_name)} disabled={busy} className="text-xs text-brand hover:underline disabled:opacity-50">
                    Authenticate
                  </button>
                  <button onClick={() => handleDelete(s.broker_name)} className="text-xs text-danger hover:underline">
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="Add / update broker credentials">
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-muted mb-1">Broker</label>
            <select
              className="w-full sm:w-64 rounded bg-panel2 border border-border px-2 py-1.5 text-sm capitalize"
              value={selectedBroker}
              onChange={(e) => setSelectedBroker(e.target.value)}
            >
              {brokers.map((b) => (
                <option key={b} value={b} className="capitalize">{b}</option>
              ))}
            </select>
          </div>

          <div className="grid sm:grid-cols-2 gap-3">
            {CRED_FIELDS.map((f) => (
              <div key={f.key}>
                <label className="block text-xs text-muted mb-1">{f.label}</label>
                <input
                  type="password"
                  autoComplete="off"
                  className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                  value={credentials[f.key] ?? ""}
                  onChange={(e) => setCredentials({ ...credentials, [f.key]: e.target.value })}
                />
              </div>
            ))}
          </div>

          {error && <div className="text-sm text-danger">{error}</div>}
          {message && <div className="text-sm text-accent">{message}</div>}

          <button
            onClick={handleStore}
            disabled={busy || !selectedBroker}
            className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
          >
            {busy ? "Saving…" : "Store credentials"}
          </button>
        </div>
      </Card>

      <Card title="TradingView webhook">
        <p className="text-sm text-muted mb-3">
          Paste this URL into a TradingView alert's "Webhook URL" field, with a JSON message body
          of <code className="text-xs">{"{ strategy_id, symbol, direction, entry, stop_loss, target1, target2?, alert_id? }"}</code>.
          Every alert runs through the same risk engine and kill switches as a manual paper
          execute. The token in the URL is the only credential protecting it - rotate it if it
          ever leaks.
        </p>
        {webhook && (
          <div className="space-y-2">
            <div className="flex gap-2">
              <input
                readOnly
                className="flex-1 rounded bg-panel2 border border-border px-2 py-1.5 text-xs font-mono"
                value={new URL(webhook.webhook_url, window.location.origin).toString()}
                onFocus={(e) => e.target.select()}
              />
              <button
                onClick={handleCopyWebhookUrl}
                className="rounded border border-border hover:bg-panel2 text-slate-200 px-3 py-1.5 text-xs shrink-0"
              >
                {webhookCopied ? "Copied!" : "Copy"}
              </button>
            </div>
            <button
              onClick={handleRotateWebhook}
              disabled={busy}
              className="text-xs text-danger hover:underline disabled:opacity-50"
            >
              Rotate URL (invalidates the old one)
            </button>
          </div>
        )}
      </Card>
    </div>
  );
}
