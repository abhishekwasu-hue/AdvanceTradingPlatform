import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import settingsSource from "../pages/SettingsPage.tsx?raw";
import guideSource from "./KeyGuidesV2.tsx?raw";
import { BYOK_PROVIDERS } from "../components/visionByok";
import { checkPastedKey, cleanPastedKey, detectKey } from "./detect";
import { GUIDE_MAX_AGE_DAYS, KEY_GUIDES, VISION_CARD_GUIDES, fixFor, guideAgeDays, guideFor } from "./guides";
import { detectProvider } from "./keyFormats";
import KeyGuidesV2, { EXPLAINER } from "./KeyGuidesV2";

/** MO2 part B: the beginner AI-key guides (Settings → AI) and the paste helper. Key-shaped strings are built here at run
 * time from parts, never written whole: this repository is public. */

const fill = (n: number) => "x1Y2".repeat(Math.ceil(n / 4)).slice(0, n);
const fake = {
  gemini: () => ["AI", "za", fill(35)].join(""),
  anthropic: () => ["sk", "-ant-", "api03-", fill(60)].join(""),
  openai: () => ["sk", "-proj-", fill(60)].join(""),
  openrouter: () => ["sk", "-or-", "v1-", fill(40)].join(""),
  groq: () => ["gsk", "_", fill(40)].join(""),
};

describe("MO2 key guides: the content", () => {
  it("the explainer line, word for word", () => {
    expect(EXPLAINER).toBe("An API key is like a password that lets AiAlgoDesk use an AI service on your account. Free keys take about 2 minutes to create.");
  });

  it("a guide per provider ATP takes, Google Gemini first and the only one recommended; the Vision card keeps its three", () => {
    expect(KEY_GUIDES.map((g) => g.provider)).toEqual(["gemini", "anthropic", "openai", "openrouter", "groq", "mistral", "zai"]);
    expect(VISION_CARD_GUIDES.map((g) => g.provider)).toEqual(["gemini", "anthropic", "openai"]);
    expect([...VISION_CARD_GUIDES.map((g) => g.provider)].sort()).toEqual([...BYOK_PROVIDERS].sort());
    expect(KEY_GUIDES.filter((g) => g.recommended).map((g) => g.provider)).toEqual(["gemini"]);
    expect(guideFor("gemini").cost).toBe("Free tier");
  });

  it("each key page is the provider's own https page (the ordered addresses)", () => {
    expect(Object.fromEntries(KEY_GUIDES.map((g) => [g.provider, g.key_page.url]))).toEqual({
      gemini: "https://aistudio.google.com/apikey",
      anthropic: "https://console.anthropic.com/settings/keys",
      openai: "https://platform.openai.com/api-keys",
      openrouter: "https://openrouter.ai/settings/keys",
      groq: "https://console.groq.com/keys",
      mistral: "https://console.mistral.ai/api-keys",
      zai: "https://z.ai/manage-apikey/apikey-list",
    });
    for (const g of KEY_GUIDES) expect(g.key_page.label).toMatch(/^Open .+ key page$/);
  });

  it("numbered steps ending with the paste, what the key looks like (prefix only), Is it free?, common errors", () => {
    for (const g of KEY_GUIDES) {
      expect(g.steps.length).toBeGreaterThanOrEqual(3);
      expect(g.steps[g.steps.length - 1].text).toMatch(/paste/i);
      expect(g.looks_like.text).toContain(g.looks_like.prefix);
      expect(g.looks_like.prefix.length).toBeLessThanOrEqual(8);                   // a prefix, never a key
      expect(g.is_it_free.length).toBeGreaterThan(20);
      expect(g.errors.length).toBeGreaterThanOrEqual(2);
      for (const e of g.errors) { expect(e.problem).toBeTruthy(); expect(e.fix).toBeTruthy(); }
      if (g.looks_like.prefix) expect(detectProvider(g.looks_like.prefix + fill(40))).toMatchObject({ kind: "provider", provider: g.provider });
      else expect(detectProvider(fill(32)).kind).toBe("unknown");                 // no prefix: the picker asks
      if (VISION_CARD_GUIDES.includes(g)) expect(detectKey(g.looks_like.prefix + fill(40))).toEqual({ kind: "provider", provider: g.provider });
    }
  });

  // The CI reminder: a guide not checked against the provider's own page for 120 days fails here, so someone opens the
  // page, checks the steps and the address, and moves `checked_on` (src/aiKeys/guides.ts).
  it(`every guide was checked against the provider's page in the last ${GUIDE_MAX_AGE_DAYS} days`, () => {
    for (const g of KEY_GUIDES) {
      expect(g.checked_on).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      const age = guideAgeDays(g.checked_on);
      expect(age, `${g.name}: re-check ${g.key_page.url} and update checked_on`).toBeLessThanOrEqual(GUIDE_MAX_AGE_DAYS);
      expect(age, `${g.name}: checked_on is in the future`).toBeGreaterThanOrEqual(-1);
    }
    expect(guideAgeDays("2026-01-01", new Date("2026-05-01T12:00:00Z"))).toBe(120);
  });

  it("a failed test's message points at the matching fix", () => {
    expect(fixFor(guideFor("anthropic"), "Your credit balance is too low to access the API")?.fix).toMatch(/Billing/);
    expect(fixFor(guideFor("gemini"), "429 Resource has been exhausted (e.g. check quota)")?.fix).toMatch(/tomorrow/);
    expect(fixFor(guideFor("openai"), "You exceeded your current quota")?.fix).toMatch(/Billing/);
    expect(fixFor(guideFor("openai"), null)).toBeNull();
  });
});

describe("MO2 key guides: the paste helper", () => {
  it("trims spaces, line breaks and quotes", () => {
    const key = fake.gemini();
    expect(cleanPastedKey(`  "${key}"\n`)).toBe(key);
    expect(cleanPastedKey(`'${key}'`)).toBe(key);
  });

  it("detects the provider from the prefix (Anthropic before OpenAI: both start with sk-)", () => {
    expect(detectKey(fake.gemini())).toEqual({ kind: "provider", provider: "gemini" });
    expect(detectKey(fake.anthropic())).toEqual({ kind: "provider", provider: "anthropic" });
    expect(detectKey(fake.openai())).toEqual({ kind: "provider", provider: "openai" });
    expect(detectKey(fake.openrouter())).toEqual({ kind: "other", name: "OpenRouter" });
    expect(detectKey(fake.groq())).toEqual({ kind: "other", name: "Groq" });
    expect(detectKey("hello")).toEqual({ kind: "unknown" });
  });

  it("the right key: ready to save; another provider's: a warning and the guide to switch to", () => {
    expect(checkPastedKey("gemini", ` ${fake.gemini()} `)).toMatchObject({ ok: true, warning: null });
    const wrong = checkPastedKey("gemini", fake.anthropic());
    expect(wrong).toMatchObject({ ok: false, switchTo: "anthropic" });
    expect(wrong.warning).toBe("This looks like an Anthropic (Claude) key, not a Google Gemini key.");
    expect(checkPastedKey("anthropic", fake.openai())).toMatchObject({ ok: false, switchTo: "openai" });
    expect(checkPastedKey("openai", fake.openrouter()).warning).toMatch(/OpenRouter keys yet/);
    expect(checkPastedKey("gemini", "")).toMatchObject({ ok: false, warning: null });
    expect(checkPastedKey("gemini", "too-short").ok).toBe(false);
  });

  it("no message ever repeats the key", () => {
    for (const make of Object.values(fake)) {
      const key = make();
      for (const p of BYOK_PROVIDERS) expect(checkPastedKey(p, key).warning ?? "").not.toContain(key);
    }
  });
});

describe("MO2 key guides: the card", () => {
  it("renders the explainer and the one-click Gemini start; the guides open on demand", () => {
    const out = renderToStaticMarkup(<KeyGuidesV2 />);
    expect(out).toContain(EXPLAINER);
    expect(out).toContain("Start with free Google Gemini");
    expect(out).toContain("Recommended");
    expect((out.match(/Show the guide/g) ?? []).length).toBe(3);
    expect(out).not.toMatch(/text-\[\d+px\]/);
  });

  it("sits at the top of Settings → AI; Save and test uses the existing BYOK save + test path, no new endpoint", () => {
    expect(settingsSource.indexOf("<KeyGuidesV2 />")).toBeLessThan(settingsSource.indexOf("<AiProviderCard />"));
    expect(guideSource).toContain("api.byokSaveKey(");
    expect(guideSource).toContain("api.byokTestKey(");
    expect(guideSource).toContain('setRaw("")');                                       // the box is cleared once sent
    expect(guideSource).toContain("data-screenshot-placeholder");
    expect(guideSource).not.toMatch(/<img/);                                           // no third-party picture
  });
});
