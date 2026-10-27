import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { T, mockTrading, type TradingMocks } from "./tradingMocks";

/**
 * UX-10 My trades wave: My trades › Orders with the `ui_v2` flag on (UI audit H5-H: today's fills table was 822 px in a
 * 322 px box at 390; H4-H: browser-locale times).
 * - Baselines `orders-v2-<width>-<theme>.png` at 390 and 1440. Every reply is hand-written, made-up data
 *   (e2e/fixtures/trading_v2.json). After an intended change:
 *   `npx playwright test --project=phone e2e/orders-v2.e2e.ts --update-snapshots`, check the new images, commit.
 */

async function open(page: Page, theme: "dark" | "light", mocks: TradingMocks = {}) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: T.features as Record<string, boolean>, brokers: T.token_status as unknown[] });
  await mockTrading(page, mocks);
  await page.goto("/positions/orders");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("main")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

const THEMES = ["dark", "light"] as const;

test.describe("v2 orders 390 x 844", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await expect(page.locator("[data-page='orders-v2']")).toBeVisible();
      await expect(page).toHaveScreenshot(`orders-v2-390-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  for (const state of [{ name: "with fills" }, { name: "empty", empty: true }]) {
    test(`${state.name}: no sideways scroll, 44 px targets, nothing hidden behind the bottom bar`, async ({ page }) => {
      await open(page, "dark", state);
      await expect(page.locator("[data-page='orders-v2']")).toBeVisible();
      await expectPhoneLayout(page);
    });
  }

  test("a fill per row with Entry / Exit and an IST time; the filter shows exits only", async ({ page }) => {
    await open(page, "light");
    await expect(page.getByRole("navigation", { name: "My trades" }).getByRole("link", { name: "Orders" })).toHaveAttribute("aria-current", "page");
    const rows = page.locator("[data-fill]");
    expect(await rows.count()).toBeGreaterThan(0);
    await expect(page.locator("[data-page='orders-v2'] table")).toHaveCount(0);
    await expect(rows.first()).not.toContainText(/AM|PM/);
    await page.locator("[data-orders-filter]").getByRole("button", { name: "Exits" }).click();
    const exits = await rows.count();
    for (let i = 0; i < exits; i++) await expect(rows.nth(i)).toContainText("Exit");
  });
});

test.describe("v2 orders 1440 desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await expect(page.locator("[data-page='orders-v2']")).toBeVisible();
      await expect(page).toHaveScreenshot(`orders-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }
});
