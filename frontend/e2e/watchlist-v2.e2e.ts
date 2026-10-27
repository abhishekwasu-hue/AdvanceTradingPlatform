import { expect, test, type Page } from "@playwright/test";
import { mockCharts, type ChartsCalls, type ChartsMocks } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * P1-8 (2) W-PR: the Watchlist page at /watchlist with `ui_v2` and `watchlists` on - the same panel as Charts on its own
 * page, a tab of the phone bar (after Charts) and an entry of the desktop sidebar. A row opens its chart. With the flag
 * off there is no entry and the address says "Coming soon". While the ready-made lists are not filled the page says so
 * in one line (never sample rows). Every reply is made-up data (e2e/chartsMocks.ts): no real account, price or id.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

const sysList = (key: string, group: string, label: string, name: string, items: { symbol: string; exchange: string; name: string }[]) =>
  ({ key, group, group_label: label, name, count: items.length, read_only: true, refreshed_at: "2026-10-18T13:00:00Z", items });
const SYS_LISTS = [
  sysList("indices", "INDICES", "Indices", "Indices", [{ symbol: "NIFTY 50", exchange: "NSE", name: "NIFTY 50" },
    { symbol: "NIFTY BANK", exchange: "NSE", name: "NIFTY BANK" }, { symbol: "SENSEX", exchange: "BSE", name: "SENSEX" }]),
  sysList("sector-it", "SECTORS", "Sectors", "IT", [{ symbol: "INFY", exchange: "NSE", name: "Made-up Software A" },
    { symbol: "TCS", exchange: "NSE", name: "Made-up Software B" }]),
];
const SYSTEM = {
  groups: ["INDICES", "SECTORS"].map((g) => {
    const lists = SYS_LISTS.filter((l) => l.group === g);
    return { group: g, label: lists[0].group_label, lists: lists.map(({ items, ...summary }) => ({ ...summary, count: items.length })) };
  }),
  lists: Object.fromEntries(SYS_LISTS.map((l) => [l.key, l])),
};
const NOT_FILLED = { groups: [], lists: {} };

async function open(page: Page, setup: ChartsMocks & { watch?: boolean; path?: string } = {}): Promise<ChartsCalls> {
  const { watch = true, path = "/watchlist", ...mocks } = setup;
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ...(watch ? { watchlists: true } : {}) }, brokers: CONNECTED });
  const calls = await mockCharts(page, mocks);
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  return calls;
}

const rows = (page: Page) => page.getByRole("list", { name: /^Symbols in / }).locator("[data-row]");
const chooser = (page: Page) => page.getByRole("button", { name: /^Choose a list\. Shown: / });

test.describe("Watchlist page 1440 desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the sidebar entry opens the page with the Charts panel; a row opens its chart", async ({ page }) => {
    const calls = await open(page, { path: "/", systemLists: SYSTEM });
    const nav = page.getByRole("navigation", { name: "Pages" });
    await nav.getByRole("link", { name: "Watchlist" }).click();
    await expect(page).toHaveURL(/\/watchlist$/);
    await expect(page.getByRole("heading", { level: 1, name: "Watchlist" })).toBeVisible();
    await expect(chooser(page)).toContainText("Indices (3)");
    await expect(rows(page)).toHaveText([/NIFTY 50/, /NIFTY BANK/, /SENSEX/]);
    await expect(page.locator("[data-ready-made]")).toHaveCount(0);
    await expect(page.getByText(/sample/i)).toHaveCount(0);
    expect(calls.systemCalls).toContain("GET indices");
    await rows(page).filter({ hasText: "SENSEX" }).getByRole("button", { name: /^SENSEX/ }).click();
    await expect(page).toHaveURL(/\/charts\/SENSEX\?exchange=BSE$/);
  });

  test("before the daily list update: one honest line and the panel's own empty state, never sample rows", async ({ page }) => {
    await open(page, { systemLists: NOT_FILLED });
    await expect(page.locator("[data-ready-made='empty']")).toHaveText(/fill after the daily list update/);
    await expect(page.getByText(/You have no watchlists yet/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Create a list" })).toBeVisible();
    await expect(page.getByText(/sample/i)).toHaveCount(0);
  });

  test("with `watchlists` off: no entry, and the address says Coming soon", async ({ page }) => {
    await open(page, { watch: false, path: "/" });
    await expect(page.getByRole("navigation", { name: "Pages" }).getByRole("link", { name: "Watchlist" })).toHaveCount(0);
    await page.goto("/watchlist");
    await expect(page.getByRole("heading", { name: "Coming soon" })).toBeVisible();
    await expect(page.locator("[data-watchlist]")).toHaveCount(0);
  });
});

test.describe("Watchlist page 390 phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("Watchlist is a tab of the bottom bar after Charts; a row opens its chart", async ({ page }) => {
    await open(page, { path: "/", systemLists: SYSTEM });
    const bar = page.locator("[data-bottom-nav-v2]");
    await expect(bar.getByRole("link")).toHaveText(["Today", "Charts", "Watchlist", "Signals", "My trades"]);
    await bar.getByRole("link", { name: "Watchlist" }).click();
    await expect(page).toHaveURL(/\/watchlist$/);
    await expect(bar.getByRole("link", { name: "Watchlist" })).toHaveAttribute("aria-current", "page");
    await expect(rows(page)).toHaveText([/NIFTY 50/, /NIFTY BANK/, /SENSEX/]);
    await expectPhoneLayout(page);
    // The More sheet does not repeat it.
    await bar.getByRole("button", { name: /^More pages/ }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("dialog").getByRole("link", { name: "Watchlist" })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await rows(page).filter({ hasText: "NIFTY BANK" }).getByRole("button", { name: /^NIFTY BANK/ }).click();
    await expect(page).toHaveURL(/\/charts\/NIFTY%20BANK$/);
  });

  test("with `watchlists` off the bar is as before", async ({ page }) => {
    await open(page, { watch: false, path: "/" });
    await expect(page.locator("[data-bottom-nav-v2]").getByRole("link")).toHaveText(["Today", "Charts", "Signals", "My trades"]);
  });
});

/** UX-11 PR 5 (owner, 27 Oct): numbers first - every Watchlist card passes the text budget; the visible-word count is printed. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`v2 Watchlist text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every card fits the text budget", async ({ page }) => {
      await open(page, { systemLists: SYSTEM });
      console.log(`[words] Watchlist ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      expect(await textBudgetProblems(page)).toEqual([]);
    });
  });
}
