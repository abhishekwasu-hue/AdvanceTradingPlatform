import { expect, test, type Page } from "@playwright/test";
import { F, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { cardOverflows } from "./phoneLayout";

/**
 * UI-28 U3.7 (owner, 28 Oct): reflow at 390 px, dark and light, on the v2 pages no other spec measures on a phone.
 * Each page opens with the shared made-up replies (e2e/fixtures/dashboard_v2.json; the rest answer 404, which the pages
 * read as "not known"), then: the page never scrolls sideways (document scrollWidth <= clientWidth) and no element runs
 * out of its card (SCR-FIX E2 `cardOverflows`). The pages with their own phone specs (Today, Charts, Screener, Signals,
 * My trades, Option chain, ...) keep those.
 */
const PAGES = [
  "/analytics/coach", "/analytics/journal", "/fundamentals", "/instruments", "/markets", "/news-events", "/notifications",
  "/option-chain/plan", "/positions/money", "/quant", "/settings/trading-defaults", "/strategies/build",
];

async function open(page: Page, path: string, theme: "dark" | "light") {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: F.features as Record<string, boolean>, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { me: true });
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("main")).toBeVisible();
}

test.describe("U3.7 reflow 390 x 844", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  for (const theme of ["dark", "light"] as const) {
    for (const path of PAGES) {
      test(`${path} (${theme}): no sideways scroll, nothing out of its card`, async ({ page }) => {
        await open(page, path, theme);
        const size = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
        expect(size.scroll, `${path} is wider than the phone`).toBeLessThanOrEqual(size.client);
        expect(await cardOverflows(page)).toEqual([]);
      });
    }
  }
});
