import { expect, test, type Page, type Route } from "@playwright/test";
import { mockApi } from "./mockApi";

/**
 * MO2 part B: Settings → AI, "Get an AI key" - the beginner guides and the paste helper, on made-up replies.
 * - "Start with free Google Gemini" opens that guide in one click; the key page button opens the provider's own page.
 * - The paste helper trims, names the provider, warns on another provider's key (and switches the guide), and "Save
 *   and test" saves the key through the existing BYOK path and tests it with one call; the box is cleared once sent.
 * - With BYOK off for the organisation the guides still show and the helper says the key cannot be saved yet.
 * - Baselines (new UI only): `key-guide-v2-<width>-<theme>.png`, the card with the Gemini guide open, 1440 and 390.
 * Key-shaped strings are built at run time from parts (this repository is public); none is a real key.
 * After an intended change: `npx playwright test --project=phone e2e/key-guides-v2.e2e.ts --update-snapshots`.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const fill = (n: number) => "q7W3".repeat(Math.ceil(n / 4)).slice(0, n);
const GEMINI_SHAPED = () => ["AI", "za", fill(35)].join("");
const ANTHROPIC_SHAPED = () => ["sk", "-ant-", "api03-", fill(60)].join("");

function provider(name: "anthropic" | "openai" | "gemini", label: string, keySet = false) {
  return { provider: name, label, console_url: "https://example.invalid/keys", key_set: keySet, key_hint: keySet ? "AIza...q7W3" : null,
    model: "made-up-model", model_pinned: false, default_model: "made-up-model", models: ["made-up-model"], last_used_at: null,
    last_tested_at: null, last_test_ok: keySet ? true : null, last_test_error: null, month_spend_usd: 0 };
}

function status(geminiSet = false) {
  return {
    enabled: false, active: false, reason: "vision off", vision_line: "Vision off - add your API key in Settings", provider: null, active_provider: null,
    providers: [provider("anthropic", "Anthropic (Claude)"), provider("openai", "OpenAI"), provider("gemini", "Google Gemini", geminiSet)],
    caps: { daily_cap_usd: 1, monthly_cap_usd: 10, max_cap_usd: 500 },
    spend: { today_usd: 0, month_usd: 0, month: "2026-10", usd_inr_rate: 84, resets: "daily at 00:00 UTC, monthly on the 1st (UTC)" },
    audit: { proposals: true, scan_alerts: false, daily_review: false }, channels: { telegram: false, in_app: true },
    notes: { subscription: "API keys only.", privacy: "Made-up privacy note." },
  };
}

interface Calls { saved: { provider: string; api_key: string }[]; tested: string[] }

async function open(page: Page, { theme = "light", byok = true }: { theme?: "light" | "dark"; byok?: boolean } = {}): Promise<Calls> {
  const calls: Calls = { saved: [], tested: [] };
  await page.clock.install({ time: new Date("2026-10-22T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true }, brokers: CONNECTED });
  let geminiSet = false;
  await page.route(/\/api(?:\/v1)?\/ai\/byok\/status$/, (r) => (byok ? r.fulfill({ json: status(geminiSet) }) : r.fulfill({ status: 404, json: { detail: "Not found" } })));
  await page.route(/\/api(?:\/v1)?\/ai\/byok\/key$/, (r: Route) => {
    calls.saved.push(r.request().postDataJSON() as { provider: string; api_key: string });
    geminiSet = true;
    return r.fulfill({ json: status(true) });
  });
  await page.route(/\/api(?:\/v1)?\/ai\/byok\/test\/(\w+)$/, (r: Route) => {
    const name = /test\/(\w+)$/.exec(r.request().url())![1];
    calls.tested.push(name);
    return r.fulfill({ json: { result: { provider: name, ok: true, error: null, model: "made-up-model", cost_usd: 0.00002 }, status: status(true) } });
  });
  await page.goto("/settings#ai");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await expect(page.locator("[data-key-guides]")).toBeVisible();
  return calls;
}

const guides = (page: Page) => page.locator("[data-key-guides]");

test.describe("key guides 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });

  test("the explainer, and Start with free Google Gemini opens its guide in one click", async ({ page }) => {
    await open(page);
    await expect(guides(page)).toContainText("An API key is like a password that lets ATP use an AI service on your account. Free keys take about 2 minutes to create.");
    await expect(guides(page).locator("[data-provider]").first()).toHaveAttribute("data-provider", "gemini");
    await expect(guides(page).locator("[data-provider='gemini']")).toContainText("Recommended");
    await guides(page).getByRole("button", { name: "Start with free Google Gemini" }).click();
    const guide = page.locator("[data-key-guide='gemini']");
    await expect(guide).toBeVisible();
    const link = guide.getByRole("link", { name: "Open Google AI Studio key page" });
    await expect(link).toHaveAttribute("href", "https://aistudio.google.com/apikey");
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(guide.locator("ol li")).toHaveCount(5);
    await expect(guide.locator("[data-screenshot-placeholder]").first()).toContainText("Screenshot coming");
    await expect(guide).toContainText("Starts with AIza");
    await expect(guide).toContainText("Is it free?");
    await expect(guide.locator("[data-checked-on]")).toBeVisible();
  });

  test("the paste helper: another provider's key is named and the guide switches; the right key is saved and tested once", async ({ page }) => {
    const calls = await open(page);
    await guides(page).getByRole("button", { name: "Start with free Google Gemini" }).click();
    const field = page.getByLabel("Paste your Google Gemini key");
    await field.fill(ANTHROPIC_SHAPED());
    await expect(page.locator("[data-key-warning]")).toContainText("This looks like an Anthropic (Claude) key, not a Google Gemini key.");
    await expect(page.getByRole("button", { name: "Save and test" })).toBeDisabled();
    await page.getByRole("button", { name: "Use the Anthropic (Claude) guide" }).click();
    await expect(page.locator("[data-key-guide='anthropic']")).toBeVisible();
    await guides(page).locator("[data-provider='gemini']").getByRole("button", { name: "Show the guide" }).click();
    const key = GEMINI_SHAPED();
    await page.getByLabel("Paste your Google Gemini key").fill(`  ${key}\n`);
    await expect(page.locator("[data-key-detected]")).toContainText("This looks like a Google Gemini key.");
    await page.getByRole("button", { name: "Save and test" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Your Google Gemini key works." })).toBeVisible();
    expect(calls.saved).toEqual([{ provider: "gemini", api_key: key }]);           // trimmed, sent once
    expect(calls.tested).toEqual(["gemini"]);
    await expect(page.getByLabel("Paste your Google Gemini key")).toHaveValue("");  // never kept in the box
    await expect(page.locator("body")).not.toContainText(key);
  });

  test("BYOK off: the guides still show and the helper says the key cannot be saved yet", async ({ page }) => {
    await open(page, { byok: false });
    await guides(page).getByRole("button", { name: "Start with free Google Gemini" }).click();
    await expect(page.locator("[data-byok-off]")).toBeVisible();
    await page.getByLabel("Paste your Google Gemini key").fill(GEMINI_SHAPED());
    await expect(page.getByRole("button", { name: "Save and test" })).toBeDisabled();
  });
});

for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`key guide ${viewport.width} ${theme}`, () => {
      test.use(viewport.width < 768 ? { viewport, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport, deviceScaleFactor: 1 });
      test("looks the same", async ({ page }) => {
        await open(page, { theme });
        await guides(page).getByRole("button", { name: "Start with free Google Gemini" }).click();
        await expect(page.locator("[data-key-guide='gemini']")).toBeVisible();
        await expect(guides(page)).toHaveScreenshot(`key-guide-v2-${viewport.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    });
  }
}
