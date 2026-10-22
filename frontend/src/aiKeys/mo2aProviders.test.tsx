import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import formats from "./__fixtures__/key_formats.json";
import type { AiConnection } from "./connect";
import { CONSENT_LINE, ConnectAiView, ProviderTerms, type ConnectAiViewProps } from "./ConnectAiPanelV2";
import { PLAIN_ERRORS } from "./errors";
import { FREE_TIER_NOTICE, KEY_GUIDES, VISION_CARD_GUIDES, guideFor, guideTags } from "./guides";
import KeyGuidesV2 from "./KeyGuidesV2";
import { CONNECT_ORDER, PICKER_ORDER, PROVIDER_FACTS, PROVIDER_LABELS, VISION_NAMES, detectProvider, type AiProviderName } from "./keyFormats";

/** Master Order 2 part A: OpenRouter, Groq, Mistral and Z.ai GLM in the Connect AI panel and its key guides (behind
 * `ai_one_key`). Key-shaped strings are built here at run time from parts: this repository is public. */

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;
const fill = (n: number) => "x1Y2".repeat(Math.ceil(n / 4)).slice(0, n);
const openrouterKey = () => ["sk", "-or-", "v1-", fill(40)].join("");
const groqKey = () => ["gsk", "_", fill(40)].join("");
const NEW: AiProviderName[] = ["openrouter", "groq", "mistral", "zai", "cloudflare"];

const LIMITS = { month_inr: 0, monthly_cap_inr: 880, today_inr: 0, daily_cap_inr: 88, max_cap_inr: 44000, line: "AI this month: ₹0 of your ₹880 limit", reached: null };
const NONE: AiConnection = {
  connected: false, provider: null, label: null, key_end: null, ok: null, error: null, tested_at: null, consent: null, may_train: false,
  paid_plan: true, free_tier_warning: null, limits: LIMITS, copy_tenant_key: null,
};

function view(props: Partial<ConnectAiViewProps> = {}): string {
  const all: ConnectAiViewProps = {
    conn: NONE, raw: "", picked: null, consent: false, step: "idle", notice: null, fixing: false, onRaw: noop, onPick: noop, onConsent: noop,
    onDisconnect: noop, onFix: noop, onGuides: noop, onAcknowledge: noop, onCopy: noop, onLimits: noop, ...props,
  };
  return renderToStaticMarkup(<MemoryRouter><ConnectAiView {...all} /></MemoryRouter>);
}

describe("MO2 A: the one provider table", () => {
  it("the browser reads the server's facts and orders (the fixture is the backend's byte-identical copy)", () => {
    expect(PROVIDER_FACTS).toEqual(formats.provider_facts);
    expect(CONNECT_ORDER).toEqual(["gemini", "anthropic", "openai", "openrouter", "groq", "mistral", "zai", "cloudflare"]);
    expect(PICKER_ORDER).toEqual(["gemini", "anthropic", "openai", "mistral", "zai", "cloudflare"]);
    expect(PROVIDER_LABELS.zai).toBe("Z.ai GLM");
  });

  it("the no-vision sentence names every provider that reads charts, and only those", () => {
    expect(VISION_NAMES).toEqual(["Google Gemini", "Claude", "OpenAI", "OpenRouter", "Mistral", "Cloudflare Workers AI"]);
    for (const name of VISION_NAMES) expect(PLAIN_ERRORS.no_vision).toContain(name.replace("Google ", ""));
    expect(PLAIN_ERRORS.no_vision).not.toMatch(/Groq|Z\.ai/);
  });

  it("OpenRouter and Groq keys are detected by prefix; Mistral and Z.ai keys ask the picker", () => {
    expect(detectProvider(openrouterKey())).toMatchObject({ kind: "provider", provider: "openrouter" });
    expect(detectProvider(groqKey())).toMatchObject({ kind: "provider", provider: "groq" });
    expect(detectProvider(fill(32)).kind).toBe("unknown");
    expect(detectProvider(`${fill(32)}.${fill(16)}`).kind).toBe("unknown");
  });
});

describe("MO2 A: the key guides", () => {
  it("free Google Gemini stays first; every new provider has a full guide with its tags", () => {
    expect(KEY_GUIDES[0].provider).toBe("gemini");
    for (const p of NEW) {
      const g = guideFor(p);
      expect(g.provider).toBe(p);
      expect(g.key_page.url).toMatch(/^https:\/\//);
      expect(g.checked_on).toBe("2026-10-22");
      expect(g.steps.length).toBeGreaterThanOrEqual(3);
      expect(g.is_it_free.length).toBeGreaterThan(40);
      expect(g.errors.length).toBeGreaterThanOrEqual(2);
      expect(guideTags(g)).toEqual({ vision: PROVIDER_FACTS[p].vision, may_train: PROVIDER_FACTS[p].may_train, privacy: PROVIDER_FACTS[p].privacy });
    }
    expect(guideFor("groq").is_it_free).toMatch(/does not read charts/);
    expect(guideFor("zai").is_it_free).toMatch(/does not read charts/);
  });

  it("every guide is plain English with no buy / sell / target / stop-loss words and no developer words", () => {
    const words = KEY_GUIDES.flatMap((g) => [g.name, g.is_it_free, g.looks_like.text, ...g.steps.map((s) => s.text), ...g.errors.flatMap((e) => [e.problem, e.fix]),
      guideTags(g).privacy]);
    for (const w of [...words, FREE_TIER_NOTICE, PLAIN_ERRORS.no_vision]) {
      expect(w).toMatch(/^[\x20-\x7E…→·₹]+$/);                                             // English only
      expect(w).not.toMatch(/\b(buy|sell|target|stop[- ]?loss|SL)\b/i);
      expect(w).not.toMatch(/\b(endpoint|base_url|JSON|HTTP|payload|token budget)\b/);
    }
  });

  it("the Vision card's guides (with the paste helper) stay the three it takes", () => {
    const out = renderToStaticMarkup(<KeyGuidesV2 />);
    expect((out.match(/Show the guide/g) ?? []).length).toBe(VISION_CARD_GUIDES.length);
    for (const p of NEW) expect(out).not.toContain(`data-provider="${p}"`);
    expect(out).not.toContain('data-tag="vision"');
  });

  it("the Connect AI drawer lists every provider with Free tier / Paid, Reads charts (vision) and the may-train mark", () => {
    const out = renderToStaticMarkup(<KeyGuidesV2 pasteHelper={false} startOpen="gemini" />);
    expect((out.match(/Show the guide|Hide the guide/g) ?? []).length).toBe(KEY_GUIDES.length);
    const order = CONNECT_ORDER.map((p) => out.indexOf(`data-provider="${p}"`));
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(out.indexOf("Start with free Google Gemini")).toBeLessThan(order[0]);
    expect((out.match(/data-tag="vision"/g) ?? []).length).toBe(VISION_NAMES.length);
    expect((out.match(/data-tag="may-train"/g) ?? []).length).toBe(CONNECT_ORDER.filter((p) => PROVIDER_FACTS[p].may_train).length);
    expect(text(out)).toContain("Reads charts (vision)");
    expect(text(out)).toContain("May train on free keys");
    expect(out).toContain("data-provider-terms");                                     // the open guide's data line
    expect(out).not.toContain("data-paste-helper");
  });

  it("a text-only provider's guide says it cannot read charts", () => {
    const out = text(renderToStaticMarkup(<KeyGuidesV2 pasteHelper={false} startOpen="groq" />));
    expect(out).toContain("Groq does not read charts here.");
    expect(out).toContain(PROVIDER_FACTS.groq.privacy);
  });
});

describe("MO2 A: the Connect AI panel with the new providers", () => {
  it("an OpenRouter key: detected, the consent line, the free-tier warning and the privacy notice before saving", () => {
    const out = text(view({ raw: openrouterKey() }));
    expect(out).toContain("This is an OpenRouter key.");
    expect(out).toContain(CONSENT_LINE("OpenRouter"));
    expect(out).toContain("Free keys from OpenRouter may be used to train its models.");
    expect(out).toContain(FREE_TIER_NOTICE);
    expect(out).not.toContain(PLAIN_ERRORS.no_vision);
  });

  it("a Groq key: the privacy notice, its data line and the cannot-read-charts sentence before saving", () => {
    const out = text(view({ raw: groqKey() }));
    expect(out).toContain(CONSENT_LINE("Groq"));
    expect(out).toContain(FREE_TIER_NOTICE);
    expect(out).toContain(PROVIDER_FACTS.groq.privacy);
    expect(out).toContain(PLAIN_ERRORS.no_vision);
    expect(out).not.toContain("may be used to train");
  });

  it("a Mistral or Z.ai key comes from the picker", () => {
    const raw = fill(32);
    expect(text(view({ raw }))).toContain("Z.ai GLM");
    expect(text(view({ raw, picked: "mistral" }))).toContain(CONSENT_LINE("Mistral"));
    expect(text(view({ raw, picked: "zai" }))).toContain(PLAIN_ERRORS.no_vision);
  });

  it("a paid provider shows only its data line (no free-tier notice)", () => {
    const html = renderToStaticMarkup(<ProviderTerms provider="anthropic" />);
    expect(text(html)).toContain(PROVIDER_FACTS.anthropic.privacy);
    expect(html).not.toContain("data-privacy-notice");
    expect(html).not.toContain("data-no-vision");
  });

  it("connected to a text-only provider: the data line and the cannot-read-charts sentence stay visible", () => {
    const groq: AiConnection = { ...NONE, connected: true, provider: "groq", label: "Groq", key_end: "6666", ok: true, vision: false,
      privacy: PROVIDER_FACTS.groq.privacy, consent: { accepted: true, text: CONSENT_LINE("Groq") } };
    const out = text(view({ conn: groq }));
    expect(out).toContain("Connected · Groq · key …6666");
    expect(out).toContain(PROVIDER_FACTS.groq.privacy);
    expect(out).toContain(PLAIN_ERRORS.no_vision);
    expect(text(view({ conn: { ...groq, provider: "mistral", label: "Mistral", vision: true } }))).not.toContain(PLAIN_ERRORS.no_vision);
  });
});
