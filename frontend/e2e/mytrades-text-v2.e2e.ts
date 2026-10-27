import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
import { mockMyTradesCards } from "./myTradesCardsMocks";
import { T, mockTrading } from "./tradingMocks";

/**
 * UX-11 PR 2 (owner, 27 Oct): My trades, "numbers first, text on demand" - every card on Positions, Orders and Money
 * passes the text-budget guard (one short visible line per card, the rest in Details). Made-up data
 * (e2e/fixtures/trading_v2.json). The visible-word count per tab is printed for the comparison page.
 */
async function open(page: Page, path: string) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ...(T.features as Record<string, boolean>), portfolio_risk: true, experience_levels: true, ladder_evidence: true },
    brokers: T.token_status as unknown[] });
  await mockTrading(page, { paperAccount: true });
  await mockMyTradesCards(page);
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  await expect(page.locator("main")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

const TABS = [{ name: "Positions", path: "/positions" }, { name: "Orders", path: "/positions/orders" }, { name: "Money", path: "/positions/money" }];

for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`My trades text budget ${size.width}`, () => {
    test.use({ viewport: size });
    for (const tab of TABS) {
      test(`${tab.name}: every card fits the text budget`, async ({ page }, info) => {
        await open(page, tab.path);
        const words = await visibleWords(page);
        info.annotations.push({ type: "visible-words", description: `${tab.name} ${size.width}: ${words}` });
        console.log(`[words] ${tab.name} ${size.width}: ${words}`);
        console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
        expect(await textBudgetProblems(page)).toEqual([]);
      });
    }
  });
}
