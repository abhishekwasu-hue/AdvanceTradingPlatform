import { expect, test, type Page, type Route } from "@playwright/test";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M72 PR-1b (M72Q-1): Settings › AI with the platform AI key (`ui_v2` + `ai_platform_key`), on made-up replies.
 * - "AI use today": today's use against the limit that applies, his own lower daily limit (Not set by default), Save.
 *   At the limit the line says the rules answer until tomorrow. Saving sends only `{daily_cap_inr}` to his own prefs.
 * - Baselines (NEW): `platform-ai-v2-settings-<state>-<width>-<theme>.png` (state: room / limit), 390 and 1440, light and dark.
 * After an intended change: `npx playwright test --project=phone e2e/platform-ai-v2.e2e.ts --update-snapshots`.
 */

const ROOM = { in_use: true, available: true, reason: null, message: null, today_inr: 4.5, cap_inr: 10, operator_cap_inr: 10, own_cap_inr: null };
const LIMIT = { ...ROOM, available: false, reason: "daily_cap", today_inr: 10,
  message: "You reached your AI limit for today. Answers come from ATP's rules until it starts again tomorrow." };

async function open(page: Page, theme: "light" | "dark", usage: Record<string, unknown>): Promise<unknown[]> {
  const saved: unknown[] = [];
  await page.clock.install({ time: new Date("2026-10-22T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, ai_platform_key: true }, brokers: [] });
  let state = { ...usage };
  await page.route(/\/api(?:\/v1)?\/ai\/platform\/usage$/, (r) => r.fulfill({ json: state }));
  await page.route(/\/api(?:\/v1)?\/me\/prefs\/ai_usage$/, (r: Route) => {
    const body = r.request().postDataJSON() as { daily_cap_inr: number | null };
    saved.push(body);
    state = { ...state, own_cap_inr: body.daily_cap_inr };
    return r.fulfill({ json: { namespace: "ai_usage", version: 1, value: body } });
  });
  await page.goto("/settings/ai");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  return saved;
}

const SIZES = [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }] as const;

for (const size of SIZES) {
  test.describe(`platform AI ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, deviceScaleFactor: 1, ...(size.phone ? { isMobile: true, hasTouch: true } : {}) });

    for (const theme of ["light", "dark"] as const) {
      for (const [name, usage] of [["room", ROOM], ["limit", LIMIT]] as const) {
        test(`Settings → AI use today (${name}) looks the same (${theme})`, async ({ page }) => {
          await open(page, theme, usage);
          const card = page.locator("[data-platform-ai]");
          await expect(card.locator("[data-today]")).toBeVisible();
          if (name === "limit") await expect(card.locator("[data-reason]")).toContainText("limit for today");
          const section = page.locator("#ai");
          await section.scrollIntoViewIfNeeded();
          await expect(section).toHaveScreenshot(`platform-ai-v2-settings-${name}-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
        });
      }
    }

    test("saving his own limit sends only his number and stays on the page", async ({ page }) => {
      const saved = await open(page, "light", ROOM);
      const before = page.url();
      await page.locator("[data-own-cap]").fill("6");
      await page.locator("[data-save-cap]").click();
      await expect(page.getByRole("status")).toContainText("Your daily limit is saved.");
      expect(saved).toEqual([{ daily_cap_inr: 6 }]);
      expect(page.url()).toBe(before);
      await expect(page.locator("[data-clear-cap]")).toBeVisible();
    });

    if (size.phone) {
      test("no sideways scroll and 44 px targets", async ({ page }) => {
        await open(page, "light", LIMIT);
        await expectPhoneLayout(page);
      });
    }
  });
}
