import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Settings redesign part A: "Connect AI in one paste" (`ui_v2` + `ai_one_key`), on made-up replies.
 * - The Copilot's empty state is the Connect AI panel; pasting a key names its service, the consent line is ticked once,
 *   and the panel saves and tests by itself (no Save button); then "Connected · Claude · key …a9F2" and the Copilot's
 *   small chip. Disconnect brings the panel back.
 * - Settings → AI shows only the panel (no provider card, no model table, no Vision card).
 * - An AI call answering `ai_not_connected` opens the panel in place.
 * - Baselines (new UI only): `connect-ai-v2-copilot-<state>-<width>-<theme>.png` (not connected / connected) and
 *   `connect-ai-v2-settings-<width>-<theme>.png`, at 360, 390 and 1440, light and dark.
 * - Master Order 2 part A: OpenRouter / Groq keys are named from their prefix, Mistral / Z.ai keys from the picker; the
 *   privacy notice, the provider's data line and (Groq, Z.ai) "cannot read charts" show before saving; the guides drawer
 *   lists every provider with its tags, free Google Gemini first. Baselines `connect-ai-v2-paste-groq-<width>-<theme>.png`
 *   and `connect-ai-v2-guides-drawer-<width>-<theme>.png`.
 * - MO2A-2: a Cloudflare token alone opens the "Cloudflare Account ID" box and nothing is sent until it holds an Account
 *   ID; a token pasted next to its Account ID goes in one paste. Baseline `connect-ai-v2-paste-cloudflare-<width>-<theme>.png`.
 * Key-shaped strings are built at run time from parts (this repository is public); none is a real key.
 * After an intended change: `npx playwright test --project=phone e2e/connect-ai-v2.e2e.ts --update-snapshots`.
 */

const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown> };
const CLAUDE_SHAPED = () => ["sk", "-ant-", "api03-", "q7W3".repeat(15), "a9F2"].join("");
const LIMITS = { month_inr: 42, monthly_cap_inr: 880, today_inr: 3, daily_cap_inr: 88, max_cap_inr: 44000,
  line: "AI this month: ₹42 of your ₹880 limit", reached: null };
const NONE = { connected: false, provider: null, label: null, key_end: null, ok: null, error: null, tested_at: null, consent: null,
  may_train: false, paid_plan: true, free_tier_warning: null, limits: LIMITS, copy_tenant_key: null };
const CLAUDE = { ...NONE, connected: true, provider: "anthropic", label: "Claude", key_end: "a9F2", ok: true, tested_at: "2026-10-22T05:30:00+00:00",
  consent: { accepted: true, text: "Your questions and chart images are sent to Claude to answer you. Never your broker login or passwords." } };
const GROQ_SHAPED = () => ["gsk", "_", "q7W3".repeat(13)].join("");
const MISTRAL_SHAPED = () => "q7W3".repeat(8);
const GROQ = { ...NONE, connected: true, provider: "groq", label: "Groq", key_end: "q7W3", ok: true, tested_at: "2026-10-22T05:30:00+00:00",
  vision: false, privacy: "Groq says it does not keep what you send by default.",
  consent: { accepted: true, text: "Your questions and chart images are sent to Groq to answer you. Never your broker login or passwords." } };
const NO_VISION = "This provider cannot read charts. Vision needs Gemini, Claude, OpenAI, OpenRouter, Mistral or Cloudflare Workers AI.";
const NOTICE = "Free tiers of some providers may use your prompts, charts and data to improve or train their models. Use a paid tier for private strategies.";

const CLOUDFLARE_SHAPED = () => ["cfut", "_", "q7W3".repeat(12)].join("");
const CF_ACCOUNT = "0123456789abcdef".repeat(2);
const CLOUDFLARE = { ...NONE, connected: true, provider: "cloudflare", label: "Cloudflare Workers AI", key_end: "q7W3", ok: true,
  tested_at: "2026-10-22T05:30:00+00:00", vision: true,
  privacy: "Cloudflare says it does not keep what you send after answering and does not train models on it.",
  consent: { accepted: true, text: "Your questions and chart images are sent to Cloudflare Workers AI to answer you. Never your broker login or passwords." } };

interface Calls { connect: { api_key: string; provider?: string; consent: boolean; account_id?: string }[]; disconnect: number }

async function open(page: Page, path: string, { theme = "light", connected = false, connectAs = CLAUDE }:
  { theme?: "light" | "dark"; connected?: boolean; connectAs?: Record<string, unknown> } = {}): Promise<Calls> {
  const calls: Calls = { connect: [], disconnect: 0 };
  let state: Record<string, unknown> = connected ? CLAUDE : NONE;
  await page.clock.install({ time: new Date("2026-10-22T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, ai_one_key: true }, brokers: [] });
  const home = () => {
    const ai = state.connected
      ? { ready: true, title: null, line: null, settings: "/settings#ai", reason: null, code: null, own_key: true }
      : { ready: false, title: "Add a free AI key to start", line: "Until then I answer from ATP's own data in short, fixed sentences.",
          settings: "/settings#ai", reason: null, code: "ai_not_connected", own_key: false };
    return { ...F.home, ai };
  };
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home)?$/, (r) => r.fulfill({ json: home() }));
  await page.route(/\/api(?:\/v1)?\/ai\/byok\/connection$/, (r: Route) => {
    if (r.request().method() === "DELETE") { calls.disconnect += 1; state = NONE; }
    return r.fulfill({ json: state });
  });
  await page.route(/\/api(?:\/v1)?\/ai\/byok\/connect$/, (r: Route) => {
    calls.connect.push(r.request().postDataJSON() as Calls["connect"][number]);
    state = connectAs;
    return r.fulfill({ json: state });
  });
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  return calls;
}

const panel = (page: Page) => page.locator("[data-connect-ai]").first();
const THEMES = ["light", "dark"] as const;
const SIZES = [
  { width: 360, height: 780, phone: true },
  { width: 390, height: 844, phone: true },
  { width: 1440, height: 900, phone: false },
] as const;

for (const size of SIZES) {
  test.describe(`connect AI ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, deviceScaleFactor: 1, ...(size.phone ? { isMobile: true, hasTouch: true } : {}) });

    for (const theme of THEMES) {
      test(`Copilot not connected looks the same (${theme})`, async ({ page }) => {
        await open(page, "/copilot", { theme });
        await expect(panel(page)).toHaveAttribute("data-connect-ai", "not-connected");
        await expect(page).toHaveScreenshot(`connect-ai-v2-copilot-off-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });

      test(`Copilot connected looks the same (${theme})`, async ({ page }) => {
        await open(page, "/copilot", { theme, connected: true });
        await expect(page.locator("[data-ai-chip]")).toBeVisible();
        await expect(page).toHaveScreenshot(`connect-ai-v2-copilot-on-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });

      test(`Settings → AI looks the same (${theme})`, async ({ page }) => {
        await open(page, "/settings/ai", { theme, connected: true });
        const section = page.locator("#ai");
        await expect(section.locator("[data-connected-line]")).toBeVisible();
        await section.scrollIntoViewIfNeeded();
        await expect(section).toHaveScreenshot(`connect-ai-v2-settings-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }

    for (const theme of THEMES) {
      test(`MO2 A: a Groq key before saving looks the same (${theme})`, async ({ page }) => {
        await open(page, "/settings/ai", { theme });
        const section = page.locator("#ai");
        await section.getByLabel("Paste your AI key").fill(GROQ_SHAPED());
        await expect(section.locator("[data-provider-terms='groq']")).toContainText(NO_VISION);
        await section.scrollIntoViewIfNeeded();
        await expect(section).toHaveScreenshot(`connect-ai-v2-paste-groq-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });

      test(`MO2A-2: a Cloudflare token before its Account ID looks the same (${theme})`, async ({ page }) => {
        await open(page, "/settings/ai", { theme });
        const section = page.locator("#ai");
        await section.getByLabel("Paste your AI key").fill(CLOUDFLARE_SHAPED());
        await expect(section.locator("[data-account-id]")).toBeVisible();
        await section.scrollIntoViewIfNeeded();
        await expect(section).toHaveScreenshot(`connect-ai-v2-paste-cloudflare-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });

      test(`MO2 A: the guides drawer with every provider looks the same (${theme})`, async ({ page }) => {
        await open(page, "/settings/ai", { theme });
        await page.locator("#ai").getByRole("button", { name: "Get a free key" }).click();
        const drawer = page.locator("[data-key-guides-drawer]");
        await expect(drawer.locator("[data-key-guide='gemini']")).toBeVisible();
        await expect(drawer).toHaveScreenshot(`connect-ai-v2-guides-drawer-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }

    if (size.phone) {
      test("MO2 A: the drawer and the Groq paste fit the phone (no sideways scroll, 44 px targets)", async ({ page }) => {
        await open(page, "/settings/ai");
        await page.locator("#ai").getByLabel("Paste your AI key").fill(GROQ_SHAPED());
        await expectPhoneLayout(page);
        await page.locator("#ai").getByRole("button", { name: "Get a free key" }).click();
        await expect(page.locator("[data-key-guides-drawer]")).toBeVisible();
        await expectPhoneLayout(page);
      });

      test("no sideways scroll and 44 px targets", async ({ page }) => {
        await open(page, "/copilot");
        await expect(panel(page)).toBeVisible();
        await expectPhoneLayout(page);
      });
    }
  });
}

test.describe("connect AI flow", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("paste, tick the consent line once, and it saves and tests by itself; then the chip; Disconnect", async ({ page }) => {
    const calls = await open(page, "/copilot");
    await expect(panel(page).getByRole("button", { name: /save/i })).toHaveCount(0);
    await expect(panel(page).locator("select")).toHaveCount(0);
    const key = CLAUDE_SHAPED();
    await panel(page).getByLabel("Paste your AI key").fill(`  ${key}\n`);
    await expect(panel(page).locator("[data-detected='anthropic']")).toHaveText("This is a Claude key.");
    await expect(panel(page).locator("[data-consent]")).toContainText("Your questions and chart images are sent to Claude to answer you. Never your broker login or passwords.");
    expect(calls.connect).toEqual([]);
    await panel(page).locator("[data-consent] input").click();         // it saves by itself: the box goes away at once
    await expect(page.locator("[data-ai-chip]")).toContainText("AI connected · Claude");
    expect(calls.connect).toEqual([{ api_key: key, provider: "anthropic", consent: true }]);

    await page.goto("/settings#ai");
    // Settings redesign B: the old link opens Settings → AI, its own page; the tour step is on the index row.
    await expect(page).toHaveURL(/\/settings\/ai$/);
    const section = page.locator("#ai");
    await expect(section.locator("[data-connected-line]")).toContainText("Connected · Claude · key …a9F2");
    await expect(section.locator("[data-limits-line]")).toHaveText("AI this month: ₹42 of your ₹880 limit");
    await expect(section.locator("[data-key-guides]")).toHaveCount(0);
    await expect(section).not.toContainText(/model/i);
    await section.getByRole("button", { name: "Disconnect" }).click();
    await expect(section.locator("[data-connect-ai]")).toHaveAttribute("data-connect-ai", "not-connected");
    expect(calls.disconnect).toBe(1);
  });

  test("Get a free key opens the guides in a drawer, free Google Gemini first", async ({ page }) => {
    await open(page, "/settings#ai");
    await page.locator("#ai").getByRole("button", { name: "Get a free key" }).click();
    const drawer = page.locator("[data-key-guides-drawer]");
    await expect(drawer).toBeVisible();
    await expect(drawer.locator("[data-provider]").first()).toHaveAttribute("data-provider", "gemini");
    await expect(drawer.locator("[data-key-guide='gemini']")).toBeVisible();
    await expect(drawer.locator("[data-paste-helper]")).toHaveCount(0);
  });

  test("an AI call that answers ai_not_connected opens the panel in place", async ({ page }) => {
    await open(page, "/settings#ai");
    await page.evaluate(() => window.dispatchEvent(new Event("atp:connect-ai")));
    await expect(page.locator("[data-connect-ai-dialog] [data-connect-ai]")).toBeVisible();
  });
});

test.describe("connect AI with the MO2 A providers", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a Groq key: named from its prefix, the privacy notice and the no-charts line, then connected as Groq", async ({ page }) => {
    const calls = await open(page, "/settings/ai", { connectAs: GROQ });
    const section = page.locator("#ai");
    const key = GROQ_SHAPED();
    await section.getByLabel("Paste your AI key").fill(key);
    await expect(section.locator("[data-detected='groq']")).toHaveText("This is a Groq key.");
    const terms = section.locator("[data-provider-terms='groq']");
    await expect(terms).toContainText(NOTICE);
    await expect(terms).toContainText("Groq says it does not keep what you send by default.");
    await expect(terms).toContainText(NO_VISION);
    await section.locator("[data-consent] input").click();
    await expect(section.locator("[data-connected-line]")).toContainText("Connected · Groq · key …q7W3");
    await expect(section.locator("[data-no-vision]")).toHaveText(NO_VISION);
    expect(calls.connect).toEqual([{ api_key: key, provider: "groq", consent: true }]);
  });

  test("a Mistral key has no prefix: the picker names Mistral and Z.ai GLM, and the choice is sent", async ({ page }) => {
    const calls = await open(page, "/settings/ai");
    const section = page.locator("#ai");
    const key = MISTRAL_SHAPED();
    await section.getByLabel("Paste your AI key").fill(key);
    const picker = section.locator("[data-picker]");
    for (const name of ["Google Gemini", "Claude", "OpenAI", "Mistral", "Z.ai GLM"]) await expect(picker).toContainText(name);
    await picker.getByLabel("Mistral").check();
    await expect(section.locator("[data-consent]")).toContainText("sent to Mistral");
    await expect(section.locator("[data-free-tier]")).toContainText("Free keys from Mistral may be used to train its models.");
    await section.locator("[data-consent] input").click();             // it saves by itself: the box goes away at once
    await expect.poll(() => calls.connect).toEqual([{ api_key: key, provider: "mistral", consent: true }]);
  });

  test("MO2A-2: a Cloudflare token alone asks for the Account ID and sends both; one paste with both needs nothing more", async ({ page }) => {
    const calls = await open(page, "/settings/ai", { connectAs: CLOUDFLARE });
    const section = page.locator("#ai");
    const token = CLOUDFLARE_SHAPED();
    await section.getByLabel("Paste your AI key").fill(token);
    await expect(section.locator("[data-detected='cloudflare']")).toHaveText("This is a Cloudflare Workers AI key.");
    await section.locator("[data-consent] input").click();
    await section.getByLabel("Cloudflare Account ID").fill("1234");
    await expect(section.locator("[data-account-id]")).toContainText("An Account ID is 32 letters and numbers.");
    expect(calls.connect).toEqual([]);                                  // nothing is sent without a valid Account ID
    await section.getByLabel("Cloudflare Account ID").fill(CF_ACCOUNT);
    await expect(section.locator("[data-connected-line]")).toContainText("Connected · Cloudflare Workers AI · key …q7W3");
    expect(calls.connect).toEqual([{ api_key: token, provider: "cloudflare", consent: true, account_id: CF_ACCOUNT }]);

    await section.getByRole("button", { name: "Disconnect" }).click();
    await section.getByLabel("Paste your AI key").fill(`${CF_ACCOUNT} ${token}`);
    await expect(section.locator("[data-detected='cloudflare']")).toHaveText("This is a Cloudflare Workers AI key with its Account ID.");
    await expect(section.locator("[data-account-id]")).toHaveCount(0);
    await section.locator("[data-consent] input").click();
    await expect.poll(() => calls.connect.length).toBe(2);
    expect(calls.connect[1]).toEqual({ api_key: `${CF_ACCOUNT} ${token}`, provider: "cloudflare", consent: true });
  });

  test("the drawer lists every provider in order with its tags; a text-only guide says it cannot read charts", async ({ page }) => {
    await open(page, "/settings/ai");
    await page.locator("#ai").getByRole("button", { name: "Get a free key" }).click();
    const drawer = page.locator("[data-key-guides-drawer]");
    await expect(drawer.locator("[data-provider]")).toHaveCount(8);
    const order = await drawer.locator("[data-provider]").evaluateAll((rows) => rows.map((r) => r.getAttribute("data-provider")));
    expect(order).toEqual(["gemini", "anthropic", "openai", "openrouter", "groq", "mistral", "zai", "cloudflare"]);
    await expect(drawer.locator("[data-provider='openrouter'] [data-tag='vision']")).toBeVisible();
    await expect(drawer.locator("[data-provider='groq'] [data-tag='vision']")).toHaveCount(0);
    await expect(drawer.locator("[data-provider='mistral'] [data-tag='may-train']")).toBeVisible();
    await drawer.locator("[data-provider='zai']").getByRole("button", { name: "Show the guide" }).click();
    const guide = drawer.locator("[data-key-guide='zai']");
    await expect(guide.locator("[data-no-vision]")).toContainText("Z.ai GLM does not read charts here.");
    await expect(guide.locator("[data-key-page]")).toHaveAttribute("href", "https://z.ai/manage-apikey/apikey-list");
  });
});

import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
/** UX-11 (owner, 27 Oct): numbers first - every card passes the text budget; the visible-word count is printed. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`connect-ai text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every card fits the text budget", async ({ page }) => {
      await open(page, "/settings/ai", { theme: "light", connected: true });
      console.log(`[words] connect-ai ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      expect(await textBudgetProblems(page)).toEqual([]);
    });
  });
}
