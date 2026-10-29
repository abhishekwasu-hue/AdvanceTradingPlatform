import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import clientSource from "../api/client.ts?raw";
import settingsSource from "../settings/SettingsSectionV2.tsx?raw";
import type { AlertWebhookCreated, AlertWebhookList } from "../types";
import { SignedWebhooksBodyV2 } from "./SignedWebhooksV2";
import componentSource from "./SignedWebhooksV2.tsx?raw";
import { EMPTY, WEBHOOK_TEXT, sendTest, urlProblem, webhooksReducer, type WebhooksState } from "./signedWebhooks";

/** AUD-G2.5: Settings › Alerts › Webhooks - the secret is shown once, Send test reports the receiver's answer. */

const HOOK = { id: "a1b2c3d4e5f60718", label: "My n8n", url: "https://hooks.example.com/atp", event_types: [], min_severity: "INFO", created_at: "" };
const LIST: AlertWebhookList = { webhooks: [HOOK], max: 5, left: 4 };
const CREATED: AlertWebhookCreated = { ...HOOK, secret: "whsec_" + "t".repeat(40) };

const noop = () => undefined;
const render = (state: WebhooksState) => renderToStaticMarkup(
  <SignedWebhooksBodyV2 state={state} busy={null} url="" label="" onUrl={noop} onLabel={noop} onAdd={noop} onTest={noop} onRemove={noop} onDismiss={noop} />,
);

describe("AUD-G2.5 secret shown once", () => {
  it("the add answer reveals the secret; every later step hides it for good", () => {
    let s = webhooksReducer(EMPTY, { type: "loaded", list: LIST });
    expect(render(s)).not.toContain(CREATED.secret);
    s = webhooksReducer(s, { type: "created", created: CREATED });
    const shown = render(s);
    expect(shown).toContain(CREATED.secret);
    expect(shown).toContain(WEBHOOK_TEXT.secretOnce);
    expect(shown).toContain("Copy secret");
    // a reload of the list (which never carries the secret) keeps it on screen until the user is done with it ...
    s = webhooksReducer(s, { type: "loaded", list: LIST });
    expect(render(s)).toContain(CREATED.secret);
    // ... "I saved it", a test result or a removal hides it, and nothing brings it back
    expect(render(webhooksReducer(s, { type: "dismissed" }))).not.toContain(CREATED.secret);
    expect(render(webhooksReducer(s, { type: "noted", tone: "ok", text: "x" }))).not.toContain(CREATED.secret);
    const after = webhooksReducer(webhooksReducer(s, { type: "dismissed" }), { type: "loaded", list: LIST });
    expect(render(after)).not.toContain(CREATED.secret);
    expect(JSON.stringify(after)).not.toContain(CREATED.secret);
  });

  it("the list shows each webhook with Send test and Remove; the add form only while below the cap", () => {
    const html = render(webhooksReducer(EMPTY, { type: "loaded", list: LIST }));
    expect(html).toContain("My n8n");
    expect(html).toContain("https://hooks.example.com/atp");
    expect(html).toContain("Send test");
    expect(html).toContain("Remove");
    expect(html).toContain("Add webhook");
    const full = render(webhooksReducer(EMPTY, { type: "loaded", list: { ...LIST, left: 0 } }));
    expect(full).not.toContain("Add webhook");
    expect(full).toContain(WEBHOOK_TEXT.full);
  });

  it("https only, checked before asking the server", () => {
    expect(urlProblem("http://hooks.example.com")).toBe(WEBHOOK_TEXT.httpsOnly);
    expect(urlProblem("  ")).toBeTruthy();
    expect(urlProblem("https://hooks.example.com/x")).toBeNull();
  });
});

describe("AUD-G2.5 Send test", () => {
  it("reports the receiver's answer, a refusal and a failed call in plain words", async () => {
    const ok = { testAlertWebhook: vi.fn().mockResolvedValue({ ok: true, detail: "Your receiver answered HTTP 200" }) };
    expect(await sendTest(ok, HOOK.id)).toEqual({ type: "noted", tone: "ok", text: "Test sent. Your receiver answered HTTP 200." });
    expect(ok.testAlertWebhook).toHaveBeenCalledWith(HOOK.id);
    const refused = { testAlertWebhook: vi.fn().mockResolvedValue({ ok: false, detail: "The receiver answered HTTP 401" }) };
    expect(await sendTest(refused, HOOK.id)).toMatchObject({ tone: "problem", text: "Test not delivered: The receiver answered HTTP 401" });
    const down = { testAlertWebhook: vi.fn().mockRejectedValue(new Error("network")) };
    expect(await sendTest(down, HOOK.id)).toMatchObject({ tone: "problem" });
  });
});

describe("AUD-G2.5 wiring", () => {
  it("the client calls the member's own webhook routes; Settings shows the card only with the flag", () => {
    expect(clientSource).toContain('request<AlertWebhookList>("/alert-webhooks")');
    expect(clientSource).toMatch(/\/alert-webhooks\/\$\{encodeURIComponent\(id\)\}\/test`, \{ method: "POST" \}/);
    expect(settingsSource).toContain('useFeatures().on("alert_webhooks")');
    expect(settingsSource).toMatch(/\{webhooks && \([\s\S]*<SignedWebhooksV2 \/>/);
    expect(componentSource).toContain("api.addAlertWebhook");
    expect(componentSource).not.toMatch(/localStorage|sessionStorage/);         // the secret is never kept in the browser
  });
});
