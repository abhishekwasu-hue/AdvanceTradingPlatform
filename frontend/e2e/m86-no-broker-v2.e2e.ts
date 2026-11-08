import { expect, test, type Page } from "@playwright/test";
import { DEMO_OVERVIEW } from "../src/markets/markets.fixture";
import { madeUpBars, mockCharts } from "./chartsMocks";
import { F } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M86 (owner): a signed-in user WITH NO broker connected (`brokers: []`) sees the last end-of-day close (NSE's files)
 * with "Last close <date>" - never a blank, a spinner or a raw error. Made-up data only; every broker-dependent read
 * answers what the backend answers such a user (read from the routes):
 * - POST /market-data/candles (store-first, `market_store_reads` on): Daily / Weekly from the store, intraday an empty
 *   answer with "Connect your broker for intraday bars.";
 * - GET /market-data/ltp and POST /market-data/quotes: 409 "No broker session with a valid token. ...";
 * - GET /market-data/eod/* (this order's shared end-of-day reads): made-up closes and bars.
 * M86_SHOTS=<dir>: also writes phone 390 / desktop 1440 dark screenshots for the before / after page (M86_PHASE names
 * them; the "before" run is pointed at a build of origin/main, where the assertions below are skipped).
 */
const SHOTS = process.env.M86_SHOTS;
const PHASE = process.env.M86_PHASE ?? "after";
const AFTER = PHASE === "after";
const FEATURES = { ui_v2: true, ai_copilot: true, terminal: true, screener_v2: true, markets_view: true, watchlists: true, market_store_reads: true,
  history_backfill: true, data_hub: true, paper_account: true };
const NO_READER = "No broker session with a valid token. Add the broker's API key on the Broker page (it is stored encrypted, never in the environment) and log in to it, then try again.";
const LIST = [{ id: 1, name: "My list", items: [{ symbol: "RELIANCE", exchange: "NSE" }, { symbol: "TCS", exchange: "NSE" }, { symbol: "INFY", exchange: "NSE" }, { symbol: "NIFTY 50", exchange: "NSE" }] }];
const CLOSES: Record<string, [number, number]> = { RELIANCE: [1412.4, 1398.1], TCS: [3021.0, 3040.55], INFY: [1488.9, 1475.3], "NIFTY 50": [24310.5, 24255.2] };
const LAST_DAY = "2026-11-06";
const SIZES = [{ name: "phone", width: 390, height: 844 }, { name: "desktop", width: 1440, height: 900 }] as const;

async function noBroker(page: Page, width: number, height: number) {
  await page.setViewportSize({ width, height });
  await page.clock.install({ time: new Date("2026-11-08T10:30:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["markets", "charts", "watchlist", "option-chain", "positions"]));
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: FEATURES, brokers: [], connections: [] });
  await page.route(/\/api(?:\/v1)?\/auth\/me$/, (r) => r.fulfill({ json: F.me }));
  await mockCharts(page, { market: "closed", watchlists: LIST });
  // The backend's answers for a user without a broker (registered after mockCharts: these answer first).
  await page.route(/\/api(?:\/v1)?\/market-data\/candles$/, (r) => {
    const body = r.request().postDataJSON() as { symbols: string[]; timeframe: string; lookback_days: number; before: string | null };
    const symbol = body.symbols[0];
    const daily = ["day", "week", "month"].includes(body.timeframe);
    const bars = daily && !body.before ? madeUpBars(body.timeframe) : [];
    return r.fulfill({ json: { source: { broker: "store", account_label: "" }, exchange: "NSE", timeframe: body.timeframe, base_interval: daily ? "day" : "1min",
      lookback_days: body.lookback_days, before: body.before, fetched_at: "2026-11-08T05:00:00Z", warnings: [], note: "",
      symbols: { [symbol]: { bars, count: bars.length, first: bars[0]?.timestamp ?? null, last: bars.at(-1)?.timestamp ?? null, error: null, volume_source: null, gaps: [],
        ...(bars.length ? {} : { empty_reason: "Connect your broker for intraday bars." }) } } } });
  });
  await page.route(/\/api(?:\/v1)?\/market-data\/ltp/, (r) => r.fulfill({ status: 409, json: { detail: NO_READER } }));
  await page.route(/\/api(?:\/v1)?\/market-data\/quotes$/, (r) => r.fulfill({ status: 409, json: { detail: NO_READER } }));
  await page.route(/\/api(?:\/v1)?\/market-data\/eod\/closes/, (r) => {
    const symbols = (new URL(r.request().url()).searchParams.get("symbols") ?? "").split(",");
    const items = symbols.map((symbol) => {
      const c = CLOSES[symbol];
      return c ? { symbol, exchange: "NSE", close: c[0], prev_close: c[1], change: Math.round((c[0] - c[1]) * 100) / 100,
        change_pct: Math.round(((c[0] - c[1]) / c[1]) * 10_000) / 100, date: LAST_DAY, missing: false }
        : { symbol, exchange: "NSE", close: null, prev_close: null, change: null, change_pct: null, date: null, missing: true };
    });
    return r.fulfill({ json: { items, as_of: LAST_DAY, source: "eod" } });
  });
  await page.route(/\/api(?:\/v1)?\/markets\/overview$/, (r) => r.fulfill({ json: DEMO_OVERVIEW }));
}

async function settle(page: Page) {
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; transition: none !important; animation: none !important; }" });
  await page.waitForLoadState("networkidle");
  // The broker sign-in prompt (shown once per sign-in to a user without a broker) is not part of these pages.
  const later = page.getByRole("button", { name: "Later" });
  if (await later.isVisible().catch(() => false)) await later.click();
}

async function shot(page: Page, name: string, size: string) {
  if (!SHOTS) return;
  await page.evaluate(() => { window.scrollTo(0, 0); document.querySelectorAll("main, [data-scroll-root]").forEach((e) => e.scrollTo(0, 0)); });
  await page.screenshot({ path: `${SHOTS}/${PHASE}-${name}-${size}.png`, fullPage: false });
}

for (const size of SIZES) {
  test.describe(`M86 no broker (${size.name})`, () => {
    test("Charts: Daily bars from NSE's end-of-day files under one line, never the gate or an empty intraday chart", async ({ page }) => {
      await noBroker(page, size.width, size.height);
      await page.goto("/charts/RELIANCE");
      await settle(page);
      if (AFTER) {
        await expect(page.locator("[data-no-broker='page']")).toContainText("last close");
        await expect(page.locator("[data-broker-gate]")).toHaveCount(0);
        await expect(page.getByText("Connect your broker for intraday bars.")).toHaveCount(0);
        if (size.name === "phone") await expectPhoneLayout(page);
      }
      await page.waitForTimeout(500);
      await shot(page, "charts", size.name);
    });

    test("Watchlist: the last close with its date, never 'Prices could not be loaded'", async ({ page }) => {
      await noBroker(page, size.width, size.height);
      await page.goto("/watchlist");
      await settle(page);
      if (AFTER) {
        await expect(page.locator("[data-no-broker='watchlist']")).toHaveText("Last close · 6 Nov");
        await expect(page.getByText("1,412.40").first()).toBeVisible();
        await expect(page.getByText("Prices could not be loaded")).toHaveCount(0);
        if (size.name === "phone") await expectPhoneLayout(page);
      }
      await shot(page, "watchlist", size.name);
    });

    test("Sectors & indices: the day's moves say 'Last close <date>'", async ({ page }) => {
      await noBroker(page, size.width, size.height);
      await page.goto("/markets");
      await settle(page);
      if (AFTER) {
        await expect(page.locator("[data-block='markets-dish']")).toContainText("Last close ·");
        if (size.name === "phone") await expectPhoneLayout(page);
      }
      await shot(page, "markets", size.name);
    });
  });
}
