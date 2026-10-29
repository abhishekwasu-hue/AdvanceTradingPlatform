import { Copy, Webhook } from "lucide-react";
import { useCallback, useEffect, useReducer, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import { Skeleton } from "../components/primitives";
import { ActionV2, Note } from "../setup/StepsV2";
import { EMPTY, WEBHOOK_TEXT, sendTest, urlProblem, webhooksReducer, type WebhooksAction, type WebhooksState } from "./signedWebhooks";

/**
 * AUD-G2.5 (flag alert_webhooks, needs ui_v2): "Webhooks" in Settings › Alerts. The member adds an https address of
 * his own tool; the server makes the secret and sends it once - shown here once, with a copy button, and gone after.
 * Each webhook has Send test and Remove. Only the member's own webhooks are listed.
 */

const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const INPUT = `h-11 w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg placeholder:text-fg-muted md:h-9 ${FOCUS}`;

function problem(e: unknown, fallback: string): string {
  if (e instanceof ApiError && (e.status === 400 || e.status === 409) && e.detail) return e.detail;
  return fallback;
}

export function SignedWebhooksBodyV2({ state, busy, url, label, onUrl, onLabel, onAdd, onTest, onRemove, onDismiss }: {
  state: WebhooksState; busy: string | null; url: string; label: string;
  onUrl: (v: string) => void; onLabel: (v: string) => void; onAdd: () => void;
  onTest: (id: string) => void; onRemove: (id: string) => void; onDismiss: () => void;
}) {
  const list = state.list;
  if (!list) return <Skeleton className="h-20" label="Loading" />;
  return (
    <div className="space-y-space-3" data-signed-webhooks>
      <div className="flex items-start gap-space-3">
        <Webhook size={18} aria-hidden className="mt-px shrink-0 text-fg-muted" />
        <p className="min-w-0 flex-1 text-t14 text-fg-muted">{WEBHOOK_TEXT.intro}</p>
      </div>

      {state.revealed && (
        <div className="space-y-space-2 rounded-control border border-subtle p-space-3" role="group" aria-label="Your new secret" data-webhook-secret>
          <p className="text-t14 text-fg">{WEBHOOK_TEXT.secretOnce}</p>
          <code className="block break-all rounded-control bg-surface-2 p-space-2 font-tabular text-t13 text-fg">{state.revealed.secret}</code>
          <div className="flex flex-col gap-space-2 sm:flex-row">
            <ActionV2 onClick={() => { void navigator.clipboard?.writeText(state.revealed?.secret ?? "").catch(() => undefined); }}>
              <Copy size={16} aria-hidden /> Copy secret
            </ActionV2>
            <ActionV2 primary onClick={onDismiss}>{WEBHOOK_TEXT.saved}</ActionV2>
          </div>
        </div>
      )}

      {list.webhooks.map((w) => (
        <fieldset key={w.id} className="space-y-space-2 rounded-control border border-subtle p-space-3" disabled={busy !== null} data-webhook-row={w.id}>
          <legend className="px-space-1 text-t14 font-semibold text-fg">{w.label || "Webhook"}</legend>
          <p className="break-all text-t13 text-fg-muted">{w.url}</p>
          <div className="flex flex-col gap-space-2 sm:flex-row">
            <ActionV2 busy={busy === `test-${w.id}`} onClick={() => onTest(w.id)}>Send test</ActionV2>
            <ActionV2 busy={busy === `remove-${w.id}`} onClick={() => onRemove(w.id)}>Remove</ActionV2>
          </div>
        </fieldset>
      ))}

      {list.left > 0 ? (
        <div className="space-y-space-2">
          <label className="block space-y-space-1">
            <span className="text-t13 text-fg-muted">Address of your tool</span>
            <input type="url" inputMode="url" placeholder="https://" value={url} onChange={(e) => onUrl(e.target.value)} className={INPUT} />
          </label>
          <label className="block space-y-space-1">
            <span className="text-t13 text-fg-muted">Name (optional)</span>
            <input type="text" maxLength={60} value={label} onChange={(e) => onLabel(e.target.value)} className={INPUT} />
          </label>
          <ActionV2 primary busy={busy === "add"} disabled={busy !== null} onClick={onAdd}>Add webhook</ActionV2>
        </div>
      ) : (
        <p className="text-t13 text-fg-muted">{WEBHOOK_TEXT.full}</p>
      )}
      {state.note && <Note tone={state.note.tone}>{state.note.text}</Note>}
    </div>
  );
}

export default function SignedWebhooksV2() {
  const [state, dispatch] = useReducer(webhooksReducer, EMPTY);
  const [busy, setBusy] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");

  const load = useCallback(async () => {
    try {
      dispatch({ type: "loaded", list: await api.alertWebhooks() });
    } catch {
      dispatch({ type: "noted", tone: "problem", text: "Your webhooks could not be loaded just now." });
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function step(key: string, fn: () => Promise<WebhooksAction | null>) {
    setBusy(key);
    try {
      const action = await fn();
      if (action) dispatch(action);
    } finally {
      setBusy(null);
    }
  }

  const add = () => step("add", async () => {
    const wrong = urlProblem(url);
    if (wrong) return { type: "noted", tone: "problem", text: wrong };
    try {
      const created = await api.addAlertWebhook({ url: url.trim(), label: label.trim() });
      setUrl(""); setLabel("");
      dispatch({ type: "loaded", list: await api.alertWebhooks() });
      return { type: "created", created };
    } catch (e) {
      return { type: "noted", tone: "problem", text: problem(e, "The webhook could not be added just now. Try again in a moment.") };
    }
  });

  const test = (id: string) => step(`test-${id}`, () => sendTest(api, id));

  const remove = (id: string) => step(`remove-${id}`, async () => {
    try {
      await api.removeAlertWebhook(id);
      await load();
      return { type: "noted", tone: "ok", text: "Webhook removed. It gets no more alerts." };
    } catch (e) {
      return { type: "noted", tone: "problem", text: problem(e, "The webhook could not be removed just now.") };
    }
  });

  return (
    <SignedWebhooksBodyV2 state={state} busy={busy} url={url} label={label} onUrl={setUrl} onLabel={setLabel}
      onAdd={() => void add()} onTest={(id) => void test(id)} onRemove={(id) => void remove(id)} onDismiss={() => dispatch({ type: "dismissed" })} />
  );
}
