import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";

/**
 * M75 NAV-4: capture-only (like m75w1-capture): the before / after shots of the NAV-4 comparison page - the Charts
 * tab's Futures hub, its basket badge clicked (before: the app leaves for the Option chain tab; after: the Option chain
 * opens in place over the tab and the address keeps its path), 390 and 1440, light and dark. No toHaveScreenshot, so
 * nothing here becomes a baseline; never runs in CI. By hand: `NAV4_OUT=<folder> npx playwright test --project=phone
 * e2e/m75nav4-capture-v2.e2e.ts` against a build.
 */
test.skip(!!process.env.CI, "capture-only: run by hand for the M75 NAV-4 comparison page, never in CI");
const OUT = process.env.NAV4_OUT ?? "test-results/m75-nav4";
mkdirSync(OUT, { recursive: true });
const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown>;
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const HUB = {
  symbol: "NIFTY 50", underlying: "NIFTY", spot_symbol: "NIFTY 50", spot_exchange: "NSE", has_fno: true, has_options: true, has_futures: true,
  lot_size: 75, option_expiries: [{ date: "2026-11-03", kind: "weekly" }], default_option_expiry: "2026-11-03",
  future_expiries: ["2026-10-27"], today: "2026-10-19", expiry_source: "master",
};
const FUTURES = {
  underlying: "NIFTY", spot: { symbol: "NIFTY 50", exchange: "NSE", ltp: 24500, change: 50, change_pct: 0.2 },
  rows: [{ underlying: "NIFTY", tradingsymbol: "NIFTY26OCTFUT", exchange: "NFO", instrument_key: "NSE_FO|NIFTY26OCTFUT", expiry: "2026-10-27",
    lot_size: 75, tick_size: 0.05, label: "NIFTY 27 OCT FUT", days_to_expiry: 8, ltp: 24560, prev_close: 24520, change: 40, change_pct: 0.16,
    oi: 12_000_000, oi_change: 150_000, volume: null, bid: 24559.5, ask: 24560.5, basis: 60, basis_pct: 0.245, missing: false, error: null }],
  rollover: null, oi_change: { status: "ready", source: "first_reading", since: "2026-10-19T03:50:00+00:00" }, market_open: true,
  source: { broker: "upstox", account_label: "primary" }, fetched_at: "2026-10-19T05:30:00+00:00", message: null,
};

async function prep(page: Page, theme: string) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts", "option-chain"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, paper_account: true, chart_workspace: true }, brokers: CONNECTED });
  await mockCharts(page, {});
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/?]+)\/(hub|futures|expiries|live)/, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/hub")) return route.fulfill({ json: HUB });
    if (url.pathname.endsWith("/futures")) return route.fulfill({ json: FUTURES });
    if (url.pathname.endsWith("/expiries")) return route.fulfill({ json: DATA.expiries });
    return route.fulfill({ json: { ...DATA.live, market_open: true } });
  });
  await page.route(/\/api(?:\/v1)?\/paper\/fno\//, (route) => route.fulfill(route.request().url().endsWith("/held") ? { json: [] } : { status: 404, json: { detail: "Not found" } }));
}

for (const [w, h] of [[390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`${w} ${theme}`, () => {
      test.use({ viewport: { width: w, height: h }, deviceScaleFactor: 1, ...(w === 390 ? { isMobile: true, hasTouch: true } : {}) });

      test(`futures basket badge ${w} ${theme}`, async ({ page }) => {
        await prep(page, theme);
        await page.goto("/charts/NIFTY%2050?hub=futures&tf=15min");
        await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
        const badge = page.locator("[data-futures-basket]");
        await expect(badge).toBeVisible({ timeout: 15_000 });
        await badge.click();
        await page.waitForTimeout(2500);
        const url = new URL(page.url());
        writeFileSync(`${OUT}/url-${w}-${theme}.txt`, `${url.pathname}${url.search}`);
        const inPlace = await page.locator("[data-inplace-panel='option-chain']").count();
        if (w === 390 && inPlace) {
          const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
          expect(overflow).toBeLessThanOrEqual(0);
        }
        await page.screenshot({ path: `${OUT}/badge-${w}-${theme}.png`, animations: "disabled" });
      });
    });
  }
}
