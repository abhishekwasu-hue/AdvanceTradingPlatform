import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { WebhookTokenResponse } from "../types";
import { Card } from "./ui";

/** The TradingView webhook (moved from Settings' broker area to Settings -> Integrations in P2b, unchanged). */
export default function WebhookCard() {
  const [webhook, setWebhook] = useState<WebhookTokenResponse | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getWebhookToken().then(setWebhook).catch((e) => setError(String(e)));
  }, []);

  async function copy() {
    if (!webhook?.webhook_url) return;
    try {
      await navigator.clipboard.writeText(new URL(webhook.webhook_url, window.location.origin).toString());
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard unavailable - the URL is still shown in the input for manual copy
    }
  }

  async function rotate() {
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

  return (
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
              <button onClick={copy} className="rounded border border-border hover:bg-surface-2 text-fg px-3 py-1.5 text-xs shrink-0">
                {copied ? "Copied!" : "Copy"}
              </button>
            </div>
          )}
          <button onClick={rotate} disabled={busy} className="text-xs text-down hover:underline disabled:opacity-50">
            {webhook.webhook_url ? "Rotate URL (invalidates the old one)" : "Rotate: issue a new webhook URL (owner)"}
          </button>
        </div>
      )}
      {error && <div className="mt-2 text-sm text-down">{error}</div>}
      {message && <div className="mt-2 text-sm text-up">{message}</div>}
    </Card>
  );
}
