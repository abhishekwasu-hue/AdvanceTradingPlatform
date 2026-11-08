import { expect, test, type Page } from "@playwright/test";
import { DEMO_OVERVIEW } from "../src/markets/markets.fixture";
import { readFileSync } from "node:fs";
import { madeUpBars, mockCharts } from "./chartsMocks";
import { F, setupAnswer } from "./dashboardMocks";
import { mockTrading } from "./tradingMocks";
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
const REGISTRY = (JSON.parse(readFileSync(new URL("../src/screener/builderTexts.json", import.meta.url), "utf8")) as { registry: unknown }).registry;
const TAXONOMY = JSON.parse(readFileSync(new URL("../src/screener/taxonomySample.json", import.meta.url), "utf8")) as unknown;
const CHAIN = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as { expiries: unknown };
const STRIP_ROWS: [string, string, string][] = [["NIFTY 50", "NIFTY 50", "NSE"], ["BANK NIFTY", "NIFTY BANK", "NSE"], ["SENSEX", "SENSEX", "BSE"], ["INDIA VIX", "INDIA VIX", "NSE"]];
const EOD_LEVELS: Record<string, [number, number]> = { "NIFTY 50": [24310.5, 24255.2], "NIFTY BANK": [52480.35, 52610.9], "INDIA VIX": [12.84, 13.1] };

/** GET /market/strip for a user without a broker: before M86 every index `missing`; after, NSE's last close (`eod`). */
function strip(url: string) {
  const sensex = new URL(url).searchParams.get("sensex") === "true";
  const indices = STRIP_ROWS.filter(([label]) => sensex || label !== "SENSEX").map(([label, symbol, exchange]) => {
    const base = { key: symbol, label, symbol, exchange, ltp: null, prev_close: null, change: null, change_pct: null, as_of: null, stale: null, source: null, missing: true };
    const lv = EOD_LEVELS[symbol];
    if (!AFTER || !lv) return base;
    const change = Math.round((lv[0] - lv[1]) * 100) / 100;
    return { ...base, ltp: lv[0], prev_close: lv[1], change, change_pct: Math.round((change / lv[1]) * 10_000) / 100, as_of: LAST_DAY, stale: false, source: "eod", missing: false };
  });
  return { indices, source: AFTER ? "eod" : "none", broker: null, refresh_seconds: 300, fetched_at: "2026-11-08T05:00:00Z",
    market: { exchange: "NSE", is_open: false, phase: "closed", reason: "weekend", opens_at: "09:15", closes_at: "15:30", next_open: "2026-11-09T09:15:00+05:30", holiday: null } };
}

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
        // An option contract is not in the cash file: the backend answers it missing ("–", no line).
        : { symbol, exchange: "NSE", close: null, prev_close: null, change: null, change_pct: null, date: null, missing: true };
    });
    return r.fulfill({ json: { items, as_of: LAST_DAY, source: "eod" } });
  });
  await page.route(/\/api(?:\/v1)?\/markets\/overview$/, (r) => r.fulfill({ json: DEMO_OVERVIEW }));
  await page.route(/\/api(?:\/v1)?\/market\/strip(\?.*)?$/, (r) => r.fulfill({ json: strip(r.request().url()) }));
  await page.route(/\/api(?:\/v1)?\/me\/setup(\?|$)/, (r) => r.fulfill({ json: setupAnswer("none") }));
  await page.route(/\/api(?:\/v1)?\/option-chain\/[^/]+\/expiries/, (r) => r.fulfill({ json: CHAIN.expiries }));
  await page.route(/\/api(?:\/v1)?\/option-chain\/[^/]+\/live/, (r) => r.fulfill({ status: 409, json: { detail: NO_READER } }));
  await page.route(/\/api(?:\/v1)?\/market-data\/eod\/chain/, (r) => r.fulfill({ json: { underlying: "NIFTY", date: LAST_DAY, expiry: "2026-11-10",
    expiries: ["2026-11-10", "2026-11-17"], spot: 24310.5, source: "eod",
    rows: Array.from({ length: 11 }, (_, i) => {
      const strike = 24050 + i * 50;
      const dist = strike - 24310.5;
      return { strike, call: { close: Math.max(2, Math.round((120 - dist * 0.55) * 20) / 20), oi: 410_000 + i * 37_500 },
        put: { close: Math.max(2, Math.round((120 + dist * 0.55) * 20) / 20), oi: 820_000 - i * 52_500 } };
    }) } }));
}

/** Find setups' and Signals' own reads (not market data): the screener's catalogue and an empty signals inbox. */
async function mockFindAndSignals(page: Page) {
  await page.route(/\/api(?:\/v1)?\/screener\/(registry|taxonomy)$/, (r) => r.fulfill({ json: /registry$/.test(r.request().url())
    ? { version: "screenql/1", timeframes: [], entries: REGISTRY } : TAXONOMY }));
  await page.route(/\/api(?:\/v1)?\/signals\/feed/, (r) => {
    const tab = new URL(r.request().url()).searchParams.get("tab") ?? "waiting";
    return r.fulfill({ json: { tab, source: null, symbol: null, items: [], next_cursor: null, counts: { waiting: 0, today: 0 } } });
  });
  await page.route(/\/api(?:\/v1)?\/ai\/actions(\?|$)/, (r) => r.fulfill({ json: [] }));
}

/** No raw server text and no endless spinner on a page of a user without a broker. */
async function noRawError(page: Page) {
  await expect(page.getByText(NO_READER)).toHaveCount(0);
  await expect(page.getByText("Prices could not be loaded")).toHaveCount(0);
}

/** Today's own reads (positions, signals, bots ...) as an empty new account: nothing open yet. */
async function mockTradingFree(page: Page) {
  for (const [re, json] of [[/\/api(?:\/v1)?\/positions(\?|$)/, []], [/\/api(?:\/v1)?\/ai\/actions(\?|$)/, []], [/\/api(?:\/v1)?\/deployments(\?|$)/, []]] as [RegExp, unknown][]) {
    await page.route(re, (r) => r.fulfill({ json }));
  }
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
        await expect(page.locator("[data-no-broker='watchlist'] p")).toHaveText("Last close · 6 Nov");
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
    test("Today: the strip on NSE's last close with its date and ONE connect button, never the gate", async ({ page }) => {
      await noBroker(page, size.width, size.height);
      await mockTradingFree(page);
      await page.goto("/");
      await settle(page);
      if (AFTER) {
        const strip = page.locator("[data-block='strip']");
        await expect(strip.locator("[data-index='NIFTY 50']")).toContainText("Last close · 6 Nov");
        await expect(strip.locator("[data-connect-live]")).toHaveCount(1);
        await expect(page.locator("[data-broker-gate]")).toHaveCount(0);
        await noRawError(page);
        if (size.name === "phone") await expectPhoneLayout(page);
      }
      await shot(page, "today", size.name);
    });

    test("Terminal: the chart on D, the watchlist on the last close, no raw error", async ({ page }) => {
      await noBroker(page, size.width, size.height);
      await page.goto("/terminal");
      await settle(page);
      await page.waitForTimeout(500);
      if (AFTER) {
        await noRawError(page);
        await expect(page.getByText("Connect your broker for intraday bars.")).toHaveCount(0);
      }
      await shot(page, "terminal", size.name);
    });

    test("Option chain: the last end-of-day chain under one line with the button", async ({ page }) => {
      await noBroker(page, size.width, size.height);
      await page.goto("/option-chain");
      await settle(page);
      if (AFTER) {
        await expect(page.locator("[data-no-broker='page'] [data-connect-live]")).toHaveCount(1);
        await expect(page.locator("[data-eod-chain='2026-11-10']")).toContainText("Last close · 6 Nov");
        await expect(page.locator("[data-broker-gate]")).toHaveCount(0);
        await noRawError(page);
        if (size.name === "phone") await expectPhoneLayout(page);
      }
      await shot(page, "option-chain", size.name);
    });

    for (const [path, name] of [["/screener", "screener"], ["/signals", "signals"]] as const) {
      test(`${name}: the page on the last close under one line with the button, never the gate`, async ({ page }) => {
        await noBroker(page, size.width, size.height);
        await mockFindAndSignals(page);
        await page.goto(path);
        await settle(page);
        if (AFTER) {
          await expect(page.locator("[data-no-broker='page']")).toContainText("last close");
          await expect(page.locator("[data-no-broker='page'] [data-connect-live]")).toHaveCount(1);
          await expect(page.locator("[data-broker-gate]")).toHaveCount(0);
          if (size.name === "phone") await expectPhoneLayout(page);
        }
        await shot(page, name, size.name);
      });
    }

    test("Copilot: the numbers strip on the last close with one connect button", async ({ page }) => {
      await noBroker(page, size.width, size.height);
      await page.goto("/copilot");
      await settle(page);
      if (AFTER) {
        const numbers = page.locator("[data-numbers-strip]");
        await expect(numbers).toContainText("Last close · 6 Nov");
        await expect(numbers.locator("[data-connect-live]")).toHaveCount(1);
        if (size.name === "phone") await expectPhoneLayout(page);
      }
      await shot(page, "copilot", size.name);
    });

    test("My trades: one line with the button, each position's last close, its Exit kept", async ({ page }) => {
      await noBroker(page, size.width, size.height);
      await mockTrading(page, { paperAccount: true });
      await page.goto("/positions");
      await settle(page);
      if (AFTER) {
        await expect(page.locator("[data-no-broker='positions'] [data-connect-live]")).toHaveCount(1);
        await expect(page.locator("[data-broker-gate]")).toHaveCount(0);
        await expect(page.locator("[data-last-close]").first()).toContainText("Last close · 6 Nov");
        await expect(page.getByRole("button", { name: /Exit/ }).first()).toBeVisible();
        if (size.name === "phone") await expectPhoneLayout(page);
      }
      await shot(page, "positions", size.name);
    });

    test("D3: the button opens the broker sheet in place and closing it returns to the same page", async ({ page }) => {
      test.skip(!AFTER, "the button is new");
      await noBroker(page, size.width, size.height);
      await page.goto("/watchlist");
      await settle(page);
      await page.locator("[data-connect-live]").first().click();
      const broker = page.locator("[data-inplace-panel='broker']");
      await expect(broker).toBeVisible();
      expect(new URL(page.url()).pathname).toBe("/watchlist");
      // Escape closes the sheet once its focus has moved inside (the same key as every in-place panel).
      await expect(async () => {
        await page.keyboard.press("Escape");
        await expect(broker).toHaveCount(0, { timeout: 1_000 });
      }).toPass({ timeout: 10_000 });
      expect(new URL(page.url()).pathname).toBe("/watchlist");
      await expect(page.locator("[data-no-broker='watchlist']")).toBeVisible();
    });
  });
}
