import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";

/**
 * Day-one fix 8 (docs/design/DAY_ONE_TRADER_TEST.md finding 9): Charts in a trader's words. The timeframes read
 * "1 min ... 1 hour, Daily" (never "1m ... 1H, D"), and a new trader's watchlist opens on the NIFTY 50 stocks (a list of
 * stocks to look through) instead of the Indices list. The volume pane's name ("Volume of the NIFTY October future") is
 * drawn on the canvas and pinned in src/chart/chartsPageV2.test.ts. Flags: the trader pack. Every reply is made up;
 * names only for public lists. With D1F_SHOTS=<folder> each step is saved as `<step>-<width>-<theme>.png`; no baseline.
 */

const PACK = (() => {
  const src = readFileSync(new URL("../../backend/app/platform/trader_pack.py", import.meta.url), "utf8");
  const body = /PACK: Tuple\[str, \.\.\.\] = \(([\s\S]*?)\n\)/.exec(src)?.[1] ?? "";
  return [...body.replace(/#.*$/gm, "").matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
})();
const FEATURES: Record<string, boolean> = Object.fromEntries(PACK.map((f) => [f, true]));
const SHOTS = process.env.D1F_SHOTS;
const FIFTY = [...(/NIFTY50_FALLBACK: readonly string\[\] = \[([\s\S]*?)\];/.exec(
  readFileSync(new URL("../src/screener/universe.ts", import.meta.url), "utf8"))?.[1] ?? "").matchAll(/"([^"]+)"/g)].map((m) => m[1]);
// --- the system lists (names only) --------------------------------------------------------------------------------
const named = (symbols: string[], exchange = "NSE") => symbols.map((symbol) => ({ symbol, exchange, name: symbol }));
const sysList = (key: string, group: string, label: string, name: string, items: { symbol: string; exchange: string; name: string }[]) =>
  ({ key, group, group_label: label, name, count: items.length, read_only: true, refreshed_at: "2026-10-22T13:00:00Z", items });
const SYS_LISTS = [
  sysList("indices", "INDICES", "Indices", "Indices", [...named(["NIFTY 50", "NIFTY BANK", "FINNIFTY", "NIFTY IT"]), ...named(["SENSEX"], "BSE")]),
  sysList("index-nifty50", "INDEX_STOCKS", "Index stocks", "NIFTY 50 stocks (50)", named(FIFTY)),
  sysList("index-niftybank", "INDEX_STOCKS", "Index stocks", "NIFTY BANK stocks (6)",
    named(["AXISBANK", "HDFCBANK", "ICICIBANK", "INDUSINDBK", "KOTAKBANK", "SBIN"])),
  sysList("sector-it", "SECTORS", "Sectors", "IT (5)", named(["HCLTECH", "INFY", "TCS", "TECHM", "WIPRO"])),
  sysList("sector-banking", "SECTORS", "Sectors", "Banking (6)", named(["AXISBANK", "HDFCBANK", "ICICIBANK", "INDUSINDBK", "KOTAKBANK", "SBIN"])),
  sysList("fno-stocks", "FNO", "F&O", "F&O stocks (8)", named(["AXISBANK", "HDFCBANK", "INFY", "ITC", "RELIANCE", "SBIN", "TCS", "TITAN"])),
];
const SYSTEM = {
  groups: ["INDICES", "INDEX_STOCKS", "SECTORS", "FNO"].map((g) => {
    const lists = SYS_LISTS.filter((l) => l.group === g);
    return { group: g, label: lists[0].group_label, lists: lists.map(({ items, ...summary }) => ({ ...summary, count: items.length })) };
  }),
  lists: Object.fromEntries(SYS_LISTS.map((l) => [l.key, l])),
};

const TOKEN = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null, needs_login: false,
  oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

async function mockWorld(page: Page) {
  await mockApi(page, { features: FEATURES, brokers: TOKEN });
  await mockDashboard(page, { state: "empty" });
  await mockCharts(page, { watchlists: [], systemLists: SYSTEM });
  const me = { ...(F.me as Record<string, unknown>), role: "USER", scopes: ["trading:write", "alerts:write"] };
  await page.route(api("/auth/me$"), (r) => r.fulfill({ json: me }));
  await page.route(api("/system/features$"), (r) => r.fulfill({ json: FEATURES }));
  await page.route(api("/broker/token-status$"), (r) => r.fulfill({ json: TOKEN }));
}

async function settle(page: Page) {
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`Day-one fix 8: Charts ${size.width} ${theme}`, () => {
      test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

      test("plain timeframes and the NIFTY 50 stocks on the first open", async ({ page }) => {
        const shot = async (step: string) => {
          if (SHOTS) await page.screenshot({ path: `${SHOTS}/${step}-${size.width}-${theme}.png`, fullPage: true, animations: "disabled" });
        };
        await page.addInitScript((t) => {
          try {
            if (sessionStorage.getItem("e2e_seeded")) return;
            sessionStorage.setItem("e2e_seeded", "1");
            localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
            localStorage.setItem("atp_install_dismissed", "1");
          } catch { /* storage unavailable */ }
        }, theme);
        await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
        await mockWorld(page);
        await page.goto("/charts");
        await settle(page);
        await expect(page.locator("[data-page='charts-v2'] .tv-lightweight-charts canvas").first()).toBeVisible();
        const tfs = page.getByRole("group", { name: "Timeframe" }).getByRole("button");
        await expect(tfs.first()).toHaveAccessibleName("5 min");             // TF-1: the favourites first, in words for a reader
        if (!size.phone) await expect(page.getByRole("group", { name: "Timeframe" }).getByRole("button", { name: "Daily", exact: true })).toBeVisible();
        await expect(page.getByRole("group", { name: "Timeframe" }).getByRole("button", { name: "1 hour", exact: true })).toBeVisible();
        await shot("01-charts");
        const panel = size.phone ? page.getByRole("dialog", { name: "Watchlist" }) : page.locator("main");
        if (size.phone) {
          await page.evaluate(() => window.scrollTo(0, 0));
          await page.getByRole("button", { name: "Watchlist", exact: true }).click();
          await expect(panel).toBeVisible();
        }
        await expect(panel.getByRole("button", { name: /^Choose a list\. Shown: / })).toContainText("NIFTY 50 stocks");
        await expect(panel.getByText(FIFTY[0], { exact: true }).first()).toBeVisible();
        await page.mouse.move(0, 0);
        await shot("02-charts-watchlist");
      });
    });
  }
}
