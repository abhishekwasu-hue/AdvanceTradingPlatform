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

interface Calls { connect: { api_key: string; provider?: string; consent: boolean }[]; disconnect: number }

async function open(page: Page, path: string, { theme = "light", connected = false }: { theme?: "light" | "dark"; connected?: boolean } = {}): Promise<Calls> {
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
    state = CLAUDE;
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
        await open(page, "/settings#ai", { theme, connected: true });
        const section = page.locator("#ai");
        await expect(section.locator("[data-connected-line]")).toBeVisible();
        await section.scrollIntoViewIfNeeded();
        await expect(section).toHaveScreenshot(`connect-ai-v2-settings-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }

    if (size.phone) {
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
    await panel(page).locator("[data-consent] input").check();
    await expect(page.locator("[data-ai-chip]")).toContainText("AI connected · Claude");
    expect(calls.connect).toEqual([{ api_key: key, provider: "anthropic", consent: true }]);

    await page.goto("/settings#ai");
    const section = page.locator("#ai");
    await expect(section.locator("[data-connected-line]")).toContainText("Connected · Claude · key …a9F2");
    await expect(section.locator("[data-limits-line]")).toHaveText("AI this month: ₹42 of your ₹880 limit");
    await expect(section).toHaveAttribute("data-tour", "settings-ai");
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
