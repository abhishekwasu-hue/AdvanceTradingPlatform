import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import copilotSource from "../copilot/chat/CopilotChatV2.tsx?raw";
import settingsSource from "../pages/SettingsPage.tsx?raw";
import adminSource from "../pages/AdminPage.tsx?raw";
import panelSource from "./ConnectAiPanelV2.tsx?raw";
import type { AiConnection } from "./connect";
import { isNotConnected } from "./connect";
import { CONSENT_LINE, ConnectAiChip, ConnectAiView, chosenProvider, readyToConnect, type ConnectAiViewProps } from "./ConnectAiPanelV2";
import { detectProvider } from "./keyFormats";

/** Settings redesign A: the Connect AI panel's states. Key-shaped strings are built here at run time from parts: this
 * repository is public. */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;
const claudeKey = () => ["sk", "-ant-", "api03-", "FAKEFAKE", "0".repeat(30), "a9F2"].join("");
const geminiKey = () => ["AI", "za", "FAKEFAKE", "0".repeat(30)].join("");

const LIMITS = { month_inr: 42, monthly_cap_inr: 880, today_inr: 3, daily_cap_inr: 88, max_cap_inr: 44000, line: "AI this month: ₹42 of your ₹880 limit", reached: null };
const NONE: AiConnection = {
  connected: false, provider: null, label: null, key_end: null, ok: null, error: null, tested_at: null, consent: null, may_train: false,
  paid_plan: true, free_tier_warning: null, limits: LIMITS, copy_tenant_key: null,
};
const CLAUDE: AiConnection = {
  ...NONE, connected: true, provider: "anthropic", label: "Claude", key_end: "a9F2", ok: true, tested_at: "2026-10-22T10:00:00+00:00",
  consent: { accepted: true, text: CONSENT_LINE("Claude") },
};

function view(props: Partial<ConnectAiViewProps> = {}): string {
  const all: ConnectAiViewProps = {
    conn: NONE, raw: "", picked: null, consent: false, step: "idle", notice: null, fixing: false, onRaw: noop, onPick: noop, onConsent: noop,
    onDisconnect: noop, onFix: noop, onGuides: noop, onAcknowledge: noop, onCopy: noop, onLimits: noop, ...props,
  };
  return renderToStaticMarkup(<MemoryRouter><ConnectAiView {...all} /></MemoryRouter>);
}

describe("Connect AI panel: not connected", () => {
  it("a paste box and Get a free key; no Save button, model box, provider dropdown or on/off switch", () => {
    const html = view();
    expect(html).toContain('data-connect-ai="not-connected"');
    expect(text(html)).toContain("Paste your AI key");
    expect(text(html)).toContain("Get a free key");
    expect(html).not.toMatch(/>\s*Save\b/);
    expect(html).not.toContain("<select");
    expect(text(html)).not.toMatch(/\bmodel\b/i);
    expect(html).not.toContain("data-limits");
  });

  it("a Claude key is detected and the consent line shows before the first save", () => {
    const html = text(view({ raw: claudeKey() }));
    expect(html).toContain("This is a Claude key.");
    expect(html).toContain("Your questions and chart images are sent to Claude to answer you. Never your broker login or passwords.");
    expect(html).not.toContain("may be used to train");
  });

  it("a Gemini key also gets the one-line free-tier warning", () => {
    expect(text(view({ raw: geminiKey() }))).toContain("Free keys from Google Gemini may be used to train its models.");
  });

  it("an unknown format gets the small picker (MO2 A: Mistral and Z.ai GLM keys have no prefix)", () => {
    const html = view({ raw: "FAKEFAKE-no-known-prefix-000000000000" });
    expect(html).toContain("data-picker");
    expect(text(html)).toContain("Which service is this key from?");
    for (const name of ["Google Gemini", "Claude", "OpenAI", "Mistral", "Z.ai GLM"]) expect(text(html)).toContain(name);
    expect(html).not.toContain("data-consent");
    expect(text(view({ raw: "FAKEFAKE-no-known-prefix-000000000000", picked: "openai" }))).toContain("sent to OpenAI");
  });

  it("an xAI key gets the plain not-supported line and no consent (MO2 A: OpenRouter and Groq keys are taken)", () => {
    const html = view({ raw: ["xai", "-", "FAKEFAKE0000000000000000"].join("") });
    expect(text(html)).toContain("AiAlgoDesk cannot use xAI keys. Use a key from Google Gemini, Claude, OpenAI, OpenRouter, Groq, Mistral, Z.ai or Cloudflare Workers AI.");
    expect(html).not.toContain("data-consent");
  });

  it("saves by itself only once the service is known and the consent is ticked", () => {
    expect(readyToConnect(claudeKey(), null, false, "idle")).toBe(false);
    expect(readyToConnect(claudeKey(), null, true, "idle")).toBe(true);
    expect(readyToConnect(claudeKey(), null, true, "busy")).toBe(false);
    expect(readyToConnect("FAKEFAKE-no-known-prefix-000000000000", null, true, "idle")).toBe(false);
    expect(readyToConnect("FAKEFAKE-no-known-prefix-000000000000", "gemini", true, "idle")).toBe(true);
    expect(chosenProvider(detectProvider(claudeKey()), "gemini")).toBe("anthropic");      // the key's own format wins
  });

  it("while it works: one plain line", () => {
    expect(text(view({ raw: claudeKey(), consent: true, step: "busy" }))).toContain("Connecting and testing your key…");
  });

  it("the owner who entered the organisation's key is offered to use it too", () => {
    const html = view({ conn: { ...NONE, copy_tenant_key: { label: "Claude" } } });
    expect(text(html)).toContain("Use this key for your account too");
    expect(html).toContain("data-copy-offer");
    expect(view()).not.toContain("data-copy-offer");
  });
});

describe("Connect AI panel: connected", () => {
  it("Connected · Claude · key …a9F2, Disconnect and the rupee limits bar", () => {
    const html = view({ conn: CLAUDE });
    expect(html).toContain('data-connect-ai="connected"');
    expect(text(html)).toContain("Connected · Claude · key …a9F2");
    expect(text(html)).toContain("Disconnect");
    expect(text(html)).toContain("AI this month: ₹42 of your ₹880 limit");
    expect(html).toContain('aria-valuenow="5"');
    expect(html).not.toContain("data-paste");
  });

  it("a failed test keeps the key: the plain sentence, Fix and Remove", () => {
    const html = view({ conn: { ...CLAUDE, ok: false, error: "This key has no credit left." } });
    expect(html).toContain('data-connect-ai="failed"');
    expect(text(html)).toContain("This key has no credit left.");
    expect(text(html)).toContain("Fix");
    expect(text(html)).toContain("Remove");
    expect(view({ conn: { ...CLAUDE, ok: false, error: "x" }, fixing: true })).toContain("data-paste");
  });

  it("a Gemini key on a free tier: the warning and the paid-plan tick", () => {
    const html = view({ conn: { ...CLAUDE, provider: "gemini", label: "Google Gemini", may_train: true, paid_plan: false,
      free_tier_warning: "Free keys from Google Gemini may be used to train its models.", paid_plan_text: "My key is on a paid plan." } });
    expect(html).toContain("data-free-tier");
    expect(html).toContain("data-paid-plan");
  });

  it("a key saved before the consent line asks for it once", () => {
    expect(view({ conn: { ...CLAUDE, consent: { accepted: false, text: CONSENT_LINE("Claude") } } })).toContain("data-consent-again");
  });

  it("a reached limit says when AI starts again", () => {
    expect(text(view({ conn: { ...CLAUDE, limits: { ...LIMITS, reached: "day" } } }))).toContain("Today's limit is reached. AI starts again tomorrow.");
  });

  it("the Copilot chip", () => {
    const html = renderToStaticMarkup(<MemoryRouter><ConnectAiChip label="Claude" /></MemoryRouter>);
    expect(text(html)).toContain("AI connected · Claude");
    expect(html).toContain('href="/settings/ai"');
  });
});

describe("where the panel lives", () => {
  it("Settings → AI shows only the panel with ai_one_key; the provider card moves to /admin", () => {
    expect(settingsSource).toContain('features.on("ai_one_key")');
    expect(settingsSource).toMatch(/oneKey \? <ConnectAiPanelV2 \/>/);
    expect(settingsSource).toContain('tour="settings-ai"');
    expect(adminSource).toContain("<AiProviderCard />");
  });

  it("the Copilot shows the panel in its empty state and the chip once connected", () => {
    expect(copilotSource).toContain("connect={oneKey ? <ConnectAiPanelV2 /> : undefined}");
    expect(copilotSource).toContain("<ConnectAiChip label={aiLabel} />");
  });

  it("the key is cleared from the box once sent and never kept", () => {
    expect(panelSource).toContain('setRaw("");');
    expect(panelSource).not.toMatch(/localStorage|sessionStorage|console\./);
  });

  it("the refusal code is recognised in an error body", () => {
    expect(isNotConnected('{"detail":{"code":"ai_not_connected","message":"x"}}')).toBe(true);
    expect(isNotConnected('{"detail":"other"}')).toBe(false);
  });
});
