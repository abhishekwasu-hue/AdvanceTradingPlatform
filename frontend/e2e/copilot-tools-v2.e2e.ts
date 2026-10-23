import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Owner order 23 Oct ("the Copilot uses the user's own AI key and its tools are wired"), on made-up replies:
 * - Settings → AI for a plain member (role USER, not the owner) with `ai_one_key` on: the Connect AI panel, not the
 *   owner-only provider card.
 * - A Copilot answer built from the tools (job 10, the full view of a symbol): its sections and "How I worked this out"
 *   with "Tools used" in plain names, no flag name for a member.
 * Answers are the backend's rule answers on its synthetic eval fixtures (e2e/fixtures/copilot_tools_v2.json). No
 * baseline is stored; with OKT_SHOTS=<folder> the test also saves screenshots there (390 and 1440, light and dark).
 */
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown> };
const T = JSON.parse(readFileSync(new URL("./fixtures/copilot_tools_v2.json", import.meta.url), "utf8")) as {
  answers: Record<string, Record<string, unknown>>;
};
const ME = JSON.parse(readFileSync(new URL("./fixtures/me.json", import.meta.url), "utf8")) as Record<string, unknown>;
const LIMITS = { month_inr: 12, monthly_cap_inr: 880, today_inr: 1, daily_cap_inr: 88, max_cap_inr: 44000,
  line: "AI this month: ₹12 of your ₹880 limit", reached: null };
const NONE = { connected: false, provider: null, label: null, key_end: null, ok: null, error: null, tested_at: null, consent: null,
  may_train: false, paid_plan: true, free_tier_warning: null, limits: LIMITS, copy_tenant_key: null };
const SHOTS = process.env.OKT_SHOTS;

async function open(page: Page, path: string, theme: "light" | "dark"): Promise<void> {
  await page.clock.install({ time: new Date("2026-10-23T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, ai_one_key: true }, brokers: [] });
  await page.route(/\/api(?:\/v1)?\/auth\/me$/, (r) => r.fulfill({ json: { ...ME, role: "USER", scopes: ["trading:write", "alerts:write"] } }));
  await page.route(/\/api(?:\/v1)?\/ai\/byok\/connection$/, (r) => r.fulfill({ json: NONE }));
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home)?$/, async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ json: { ...F.home, ai: { ready: true, title: null, line: null, settings: "/settings/ai", reason: null, code: null, own_key: true } } });
    }
    const message = (route.request().postDataJSON() as { message: string }).message;
    return route.fulfill({ json: T.answers[message] ?? T.answers["Full view of NIFTY"] });
  });
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
}

const THEMES = ["light", "dark"] as const;
const SIZES = [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }] as const;

for (const size of SIZES) {
  test.describe(`copilot tools ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, deviceScaleFactor: 1, ...(size.phone ? { isMobile: true, hasTouch: true } : {}) });

    for (const theme of THEMES) {
      test(`Settings → AI shows Connect AI to a plain member (${theme})`, async ({ page }) => {
        await open(page, "/settings/ai", theme);
        const section = page.locator("#ai");
        await expect(section.locator("[data-connect-ai]")).toBeVisible();
        if (size.phone) await expectPhoneLayout(page);
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/settings-ai-member-${size.width}-${theme}.png`, fullPage: true });
      });

      test(`a Copilot answer lists the tools it used (${theme})`, async ({ page }) => {
        await open(page, "/copilot", theme);
        await page.locator("#copilot-question").fill("Full view of NIFTY");
        await page.getByRole("button", { name: "Ask", exact: true }).click();
        const answer = page.locator("[data-answer='views']");
        await expect(answer).toContainText("NIFTY: 4 of 4 views read.");
        await answer.locator("[data-worked] summary").click();
        await expect(answer.locator("[data-tools-used]")).toHaveText("Tools used: Chart structure, Option chain analyses, News feed, Market memory");
        await expect(answer).not.toContainText("flag");
        await expect(answer.locator("[data-admin-not-set-up]")).toHaveCount(0);
        if (size.phone) await expectPhoneLayout(page);
        if (SHOTS) {
          await answer.scrollIntoViewIfNeeded();
          await page.screenshot({ path: `${SHOTS}/copilot-tools-answer-${size.width}-${theme}.png`, fullPage: true });
        }
      });
    }
  });
}
