import { expect, test, type Page } from "@playwright/test";
import { F, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { readCards, textBudgetProblems, visibleWords } from "./textBudget";

/**
 * UX-11 PR 3 (owner, 27 Oct): Today, "numbers first, text on demand" - every card passes the text-budget guard (one
 * short visible line per card, the rest in Details), connected and empty. Made-up data (e2e/fixtures/dashboard_v2.json).
 * The visible-word count is printed for the comparison page.
 */
async function open(page: Page, state: "connected" | "empty") {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: F.features as Record<string, boolean>, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { state, me: true });
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await expect(page.locator("main")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`Today text budget ${size.width}`, () => {
    test.use({ viewport: size });
    for (const state of ["connected", "empty"] as const) {
      test(`${state}: every card fits the text budget`, async ({ page }) => {
        await open(page, state);
        console.log(`[words] Today ${state} ${size.width}: ${await visibleWords(page)}`);
        console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
        expect(await textBudgetProblems(page)).toEqual([]);
      });
    }
  });
}
