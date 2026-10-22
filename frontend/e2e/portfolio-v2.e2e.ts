import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { T, mockTrading, type TradingMocks } from "./tradingMocks";

/**
 * UI2-9a (UI v2 proposal §2 row 16, §8, §9): My portfolio with the `ui_v2` flag on.
 * - Baselines `portfolio-v2-<width>-<theme>.png` at 1440, 390 and 360. Today's Portfolio page has no baseline of its
 *   own, and with the flag off it renders as before.
 * - The tiles, where the money sits by symbol, the booked P&L over time, the empty state, one primary action (none).
 * Every reply is hand-written, made-up data (e2e/fixtures/trading_v2.json). After an intended change:
 * `npx playwright test --project=phone e2e/portfolio-v2.e2e.ts --update-snapshots`, check the new images, commit.
 */

async function open(page: Page, theme: "dark" | "light", setup: TradingMocks & { flag?: boolean } = {}) {
  const { flag = true, ...mocks } = setup;
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: flag ? (T.features as Record<string, boolean>) : undefined, brokers: T.token_status as unknown[] });
  await mockTrading(page, mocks);
  await page.goto(flag ? "/positions/money" : "/portfolio");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("main")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

const THEMES = ["dark", "light"] as const;
const PHONES = [{ width: 360, height: 800 }, { width: 390, height: 844 }];

for (const size of PHONES) {
  test.describe(`v2 portfolio ${size.width} x ${size.height}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`looks the same (${theme})`, async ({ page }) => {
        await open(page, theme);
        await expect(page.locator("[data-page='portfolio-v2']")).toBeVisible();
        await expect(page).toHaveScreenshot(`portfolio-v2-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }

    for (const state of [{ name: "connected" }, { name: "empty", empty: true }]) {
      test(`${state.name}: no sideways scroll, 44 px targets, nothing hidden behind the bottom bar`, async ({ page }) => {
        await open(page, "dark", state);
        await expect(page.locator("[data-page='portfolio-v2']")).toBeVisible();
        await expectPhoneLayout(page);
      });
    }
  });
}

test.describe("v2 portfolio 1440 desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`looks the same (${theme})`, async ({ page }) => {
      await open(page, theme);
      await expect(page.locator("[data-page='portfolio-v2']")).toBeVisible();
      await expect(page).toHaveScreenshot(`portfolio-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("the tiles, where the money is by symbol, and the booked P&L over time", async ({ page }) => {
    await open(page, "light");
    await expect(page.locator("[data-shell='v2'] header").first()).toContainText("My trades");      // UX3-9: My trades › Money
    await expect(page.getByRole("navigation", { name: "My trades" }).getByRole("link", { name: "Money" })).toHaveAttribute("aria-current", "page");
    const tiles = page.locator("[data-block='summary']");
    await expect(tiles.locator("[data-tile='invested']")).toContainText("₹1.62 L");
    await expect(tiles.locator("[data-tile='invested']")).toContainText("32% of capital");
    await expect(tiles.locator("[data-tile='open-pnl']")).toContainText("+₹3,720");
    await expect(tiles.locator("[data-tile='booked']")).toContainText("+₹973");
    await expect(tiles.locator("[data-tile='booked']")).toContainText("3 closed trades");
    await expect(tiles.locator("[data-tile='risk']")).toContainText("₹5,970");

    const rows = page.locator("[data-block='allocation'] [data-symbol]");
    await expect(rows).toHaveCount(3);
    await expect(rows.first()).toHaveAttribute("data-symbol", "RELIANCE");
    await expect(rows.first()).toContainText("₹73,000");
    await expect(rows.first()).toContainText("15% of capital");
    await expect(rows.first()).toContainText("Open P&L +₹1,000.00");
    await expect(rows.first()).toContainText("Loss at the stop ₹1,500.00");
    await expect(page.locator("[data-long-short]")).toHaveText("Long ₹88,990 · Short ₹73,000 · Net ₹15,990");

    const booked = page.locator("[data-block='booked']");
    await expect(booked.getByRole("img")).toHaveAttribute("aria-label", "Booked P&L over 3 closed trades, now +₹972.50");
    await expect(page.getByRole("link", { name: "Risk limits" })).toHaveAttribute("href", "/risk-management");
  });

  test("no primary button, plain words, a glossary '?'", async ({ page }) => {
    await open(page, "light");
    await expect(page.locator("main .bg-brand:is(a, button)")).toHaveCount(0);
    await page.getByRole("button", { name: "What is Gross exposure?" }).click();
    await expect(page.getByRole("note")).toContainText("The value of everything you hold");
    for (const gone of ["Capital Deployed", "Symbols Held", "Phase", "Sample", "recommended"]) {
      await expect(page.locator("main").getByText(gone, { exact: false }), gone).toHaveCount(0);
    }
  });

  test("nothing open: the friendly empty states", async ({ page }) => {
    await open(page, "light", { empty: true });
    const block = page.locator("[data-block='allocation']");
    await expect(block).toContainText("Nothing is invested right now.");
    await expect(block.getByRole("link", { name: "Find setups" })).toHaveAttribute("href", "/screener");
    await expect(page.locator("[data-block='booked']")).toContainText("once you have closed two trades");
  });
});

test.describe("flag off", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  test("today's portfolio page, exactly as before", async ({ page }) => {
    await open(page, "light", { flag: false });
    await expect(page.locator("[data-shell='v2']")).toHaveCount(0);
    await expect(page.locator("[data-page='portfolio-v2']")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Portfolio", exact: true })).toBeVisible();
  });
});
