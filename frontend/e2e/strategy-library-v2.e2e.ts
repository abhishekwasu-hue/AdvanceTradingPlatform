import { expect, test, type Page } from "@playwright/test";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M49 U3 (owner-approved mockup docs/ui/U3_LIBRARY.html): the Strategy Library under `ui_v2` - each card shows the name,
 * one plain line and at most three settings; the rest (and the built-in's suggested stop / targets) sit under "More
 * settings"; the category is the shared Chip; a filter with no strategy shows one short line with "Show all"; on a wide
 * screen the chosen strategy's full settings stand on the right.
 * - Baselines (NEW): `strategy-library-v2-{1440,390}-{light,dark}.png`. No other baseline changes.
 * Every reply is made-up data (made-up names; the setting keys are the engine's own): no real account, symbol or id.
 * After an intended change: `npx playwright test --project=phone e2e/strategy-library-v2.e2e.ts --update-snapshots`.
 */

const STRATEGIES = [
  { id: "demo_cross", name: "Made-up average cross", description: "Marks when a fast average crosses a slow one. A made-up strategy for the browser tests.",
    category: "indicator_based", timeframes: ["5min"],
    default_params: { tf: "5min", ema_fast: 9, ema_slow: 21, rsi_period: 14, rsi_mid: 50, atr_period: 14, atr_mult_sl: 1.0, target_rr: [1.5, 2.0] } },
  { id: "demo_band", name: "Made-up band return", description: "Marks a close back inside the band after a stretched move.",
    category: "indicator_based", timeframes: ["15min"],
    default_params: { tf: "15min", bb_period: 20, bb_k: 2.0, rsi_period: 14, rsi_low: 30, rsi_high: 70, atr_buffer: 0.2, target_rr: [1.2, 2.0] } },
  { id: "demo_daily", name: "Made-up daily pullback", description: "Marks a pullback that touches the fast average on the daily chart.",
    category: "indicator_based", timeframes: ["day"], default_params: { ema_fast: 20, ema_slow: 50, touch_pct: 0.5, target_rr: [2.0, 3.0] } },
  { id: "demo_two_charts", name: "Made-up two-chart trend", description: "The bigger chart's trend picks the side; the smaller chart marks the moment.",
    category: "multi_timeframe", timeframes: ["5min", "30min"],
    default_params: { ema_fast: 9, ema_slow: 21, htf_ema_period: 20, rsi_period: 14, adx_min: 18, atr_mult_sl: 1.2, target_rr: [1.5, 2.5] } },
];
const BOT = { ...(F.deployments as Record<string, unknown>[])[0], strategy_id: "demo_cross", status: "ACTIVE" };

async function open(page: Page, theme: "light" | "dark" = "light") {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try { localStorage.setItem("atp_appearance", JSON.stringify({ theme: t })); localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ...(F.features as Record<string, boolean>), ui_v2: true }, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { me: true });
  await page.route(api("/strategies$"), (r) => r.fulfill({ json: STRATEGIES }));
  await page.route(api("/deployments(\\?|$)"), (r) => r.fulfill({ json: [BOT] }));
  await page.goto("/strategies");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.getByText("Made-up average cross").locator("visible=true").first()).toBeVisible();
}

const SHOT = { animations: "disabled" as const, fullPage: true, maxDiffPixelRatio: 0.02, timeout: 15_000 };

for (const size of [{ name: "1440", width: 1440, height: 900, mobile: false }, { name: "390", width: 390, height: 844, mobile: true }]) {
  test.describe(`Strategy library ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as const) {
      test(`the page looks the same (${theme})`, async ({ page }) => {
        await open(page, theme);
        await expect(page).toHaveScreenshot(`strategy-library-v2-${size.name}-${theme}.png`, SHOT);
      });
    }

    test("at most three settings on a card; More settings has the rest; no raw setting names", async ({ page }) => {
      await open(page);
      const card = page.locator("[data-library-card='demo_cross']");
      await expect(card.locator("[data-main-settings] li")).toHaveText(["Fast EMA 9", "Slow EMA 21", "Chart 5-minute"]);
      await expect(card.getByText("You use it")).toBeVisible();
      await expect(page.locator("[data-library-card='demo_band']").getByText("Indicators")).toBeVisible();
      await expect(page.getByText(/ema_fast|target_rr|atr_mult_sl|indicator_based|Indicator-based/)).toHaveCount(0);
      const detail = size.mobile ? card : page.locator("[data-library-detail]");
      await detail.getByText("More settings (5)").click();
      await expect(detail.getByText("RSI middle level")).toBeVisible();
      await expect(detail.getByText("Suggested — change in settings")).toBeVisible();
      await expect(detail.getByText("Targets (R)")).toBeVisible();
    });

    test("a category with no strategy shows one line and Show all", async ({ page }) => {
      await open(page);
      await page.getByRole("button", { name: /^Multi-timeframe/ }).click();
      await expect(page.locator("[data-library-card]:visible")).toHaveCount(1);
      await page.getByRole("button", { name: /^All/ }).click();
      await expect(page.locator("[data-library-card]:visible")).toHaveCount(4);
      if (!size.mobile) {
        await page.getByRole("searchbox", { name: "Search strategies" }).fill("nothing like this");
        await expect(page.getByText("No strategies here yet.")).toBeVisible();
        await page.getByRole("button", { name: "Show all" }).click();
        await expect(page.locator("[data-library-card]:visible")).toHaveCount(4);
        await page.locator("[data-library-card='demo_band'] [data-library-pick]").click();
        await expect(page.locator("[data-library-detail] h2")).toHaveText("Made-up band return");
      }
    });

    if (size.mobile) {
      test("no sideways scroll, 44 px targets, nothing hidden behind the bottom bar", async ({ page }) => {
        await open(page);
        await page.locator("[data-library-card='demo_cross'] summary").first().click();
        await expectPhoneLayout(page);
      });
    }
  });
}
