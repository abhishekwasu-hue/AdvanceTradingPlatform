import type { AlertWebhookCreated, AlertWebhookList } from "../types";

/**
 * AUD-G2.5 (flag alert_webhooks, needs ui_v2): the state of Settings › Alerts › Webhooks.
 * The secret lives only in `revealed`, set by the add answer and cleared by every other step (a reload, a test, a
 * removal, "I saved it"), so it is shown once and never comes back.
 */
export interface WebhooksState {
  list: AlertWebhookList | null;
  revealed: { id: string; secret: string } | null;
  note: { tone: "ok" | "problem"; text: string } | null;
}

export type WebhooksAction =
  | { type: "loaded"; list: AlertWebhookList }
  | { type: "created"; created: AlertWebhookCreated }
  | { type: "dismissed" }
  | { type: "noted"; tone: "ok" | "problem"; text: string };

export const EMPTY: WebhooksState = { list: null, revealed: null, note: null };

export function webhooksReducer(state: WebhooksState, action: WebhooksAction): WebhooksState {
  switch (action.type) {
    case "loaded":
      return { ...state, list: action.list, revealed: state.revealed && action.list.webhooks.some((w) => w.id === state.revealed?.id) ? state.revealed : null };
    case "created":
      return { ...state, revealed: { id: action.created.id, secret: action.created.secret }, note: null };
    case "dismissed":
      return { ...state, revealed: null };
    case "noted":
      return { ...state, revealed: null, note: { tone: action.tone, text: action.text } };
  }
}

export const WEBHOOK_TEXT = {
  intro: "Send your alerts to your own tool (n8n, Zapier, your own server). Each message is signed, so your tool can check it came from AiAlgoDesk.",
  secretOnce: "Copy this secret now. It is shown only once; your tool uses it to check the signature.",
  saved: "I saved it",
  httpsOnly: "The address must start with https://",
  full: "You have added as many webhooks as you can. Remove one to add another.",
} as const;

/** A quick check before asking the server (the server decides; private addresses are refused there). */
export function urlProblem(url: string): string | null {
  const text = url.trim();
  if (!text) return "Enter the address of your tool";
  if (!/^https:\/\//i.test(text)) return WEBHOOK_TEXT.httpsOnly;
  return null;
}

export interface TestClient {
  testAlertWebhook: (id: string) => Promise<{ ok: boolean; detail: string }>;
}

/** Send test: the receiver's answer as one plain line. */
export async function sendTest(client: TestClient, id: string): Promise<WebhooksAction> {
  try {
    const r = await client.testAlertWebhook(id);
    return { type: "noted", tone: r.ok ? "ok" : "problem", text: r.ok ? `Test sent. ${r.detail}.` : `Test not delivered: ${r.detail}` };
  } catch {
    return { type: "noted", tone: "problem", text: "The test could not be sent just now. Try again in a moment." };
  }
}
