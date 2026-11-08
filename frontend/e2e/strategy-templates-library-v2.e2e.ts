import { expect, test, type Page } from "@playwright/test";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * P2-3: the Templates section under Strategies > Library (`ui_v2` + `strategy_templates`). Each card shows the name, the
 * family, one idea line, "A signal reads:" with the sentence from the template's data, the timeframes and two links; a
 * template that is not switched on shows one line and no links; the family chips filter. Made-up data only.
 * No baseline of its own; the existing `strategy-library-v2-*` baselines have the flag off, so they do not change.
 */
const TF = [{ value: "15m", label: "15-minute" }, { value: "1d", label: "Daily" }];
const CARD = { version: 1, difficulty: "Beginner", style: "Both", timeframes: TF, default_timeframe: "15m", sides_default: "both", params: [], rules: [] };
const TEMPLATES = [
  { ...CARD, id: "D2", name: "Made-up crossover", family: "D", family_name: "Trend following", idea: "A made-up crossover idea.", available: true,
    reads: { long: "The fast made-up line crossed above the slow one on the 15-minute chart.", short: "The fast made-up line crossed below the slow one on the 15-minute chart." } },
  { ...CARD, id: "B1", name: "Made-up range break", family: "B", family_name: "Breakout", idea: "A made-up breakout idea.", available: false,
    reads: { long: "A close beyond the range was made-up.", short: null } },
];

async function open(page: Page, flag: boolean) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ...(F.features as Record<string, boolean>), ui_v2: true, strategy_templates: flag }, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { me: true });
  await page.route(api("/strategies$"), (r) => r.fulfill({ json: [] }));
  await page.route(api("/deployments(\\?|$)"), (r) => r.fulfill({ json: [] }));
  await page.route(api("/strategy-templates$"), (r) => r.fulfill({ json: { templates: TEMPLATES } }));
  await page.goto("/strategies");
  await page.waitForLoadState("networkidle");
}

for (const size of [{ name: "1440", width: 1440, height: 900, mobile: false }, { name: "390", width: 390, height: 844, mobile: true }]) {
  test.describe(`Templates in the library ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    test("cards read from the data, links go to the bot and Test; a template that is off has no links", async ({ page }) => {
      await open(page, true);
      const on = page.locator("[data-template-card='D2']");
      await expect(on.getByText("Trend following")).toBeVisible();
      await expect(on.getByText("A signal reads:")).toBeVisible();
      await expect(on.getByText(/crossed above the slow one/)).toBeVisible();
      await expect(on.getByRole("link", { name: /Use on the trading bot/ })).toHaveAttribute("href", "/deployments?template=D2&timeframe=15m");
      await expect(on.getByRole("link", { name: /Test it first/ })).toHaveAttribute("href", "/strategies/test?template=D2&timeframe=15m");
      const off = page.locator("[data-template-card='B1']");
      await expect(off.getByText("Not switched on for your account yet")).toBeVisible();
      await expect(off.getByRole("link")).toHaveCount(0);
      await page.getByRole("button", { name: "Breakout" }).click();
      await expect(page.locator("[data-template-card]")).toHaveCount(1);
      await page.getByRole("button", { name: "All", exact: true }).last().click();
      await expect(page.locator("[data-template-card]")).toHaveCount(2);
    });

    test("no Templates section while the flag is off", async ({ page }) => {
      await open(page, false);
      await expect(page.locator("[data-templates-section]")).toHaveCount(0);
    });

    if (size.mobile) {
      test("no sideways scroll, 44 px targets", async ({ page }) => {
        await open(page, true);
        await page.locator("[data-templates-section]").scrollIntoViewIfNeeded();
        await expectPhoneLayout(page);
      });
    }
  });
}
