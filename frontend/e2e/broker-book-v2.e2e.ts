import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { T, mockTrading } from "./tradingMocks";

/**
 * Owner 14 Nov "Ho" (flags `ui_v2` + `broker_book`): "From your broker" on My trades › Positions, Money and Orders -
 * his own broker's positions, holdings and today's orders, read-only, with a Refresh button and a plain sentence where
 * a broker refused a list. Every reply is hand-written, made-up data. No baselines: `BB_PROOF_DIR` (optional) receives
 * before / after screenshots for the PR's comparison page.
 */

const PROOF = process.env.BB_PROOF_DIR;

const ZERODHA = { broker: "zerodha", broker_label: "Zerodha (Kite)", account_label: null, age_seconds: 0 };
const UPSTOX = { broker: "upstox", broker_label: "Upstox", account_label: null, age_seconds: 0 };
const STATIC_IP = { rows: [], message: "Upstox needs your static IP for this list. Register ATP's static IP with Upstox (steps on the Broker page)." };
const LISTS = {
  positions: { message: null, rows: [
    { symbol: "NIFTY26NOVFUT", exchange: "NFO", product: "NRML", quantity: -75, side: "Short", average_price: 24012.5, ltp: 23910.0, pnl: 7687.5 },
    { symbol: "HDFCBANK", exchange: "NSE", product: "MIS", quantity: 40, side: "Long", average_price: 1712.4, ltp: 1704.1, pnl: -332.0 },
    { symbol: "TCS", exchange: "NSE", product: "CNC", quantity: 0, side: "Closed", average_price: 3500.0, ltp: 3510.0, pnl: 120.0 },
  ] },
  holdings: { message: null, rows: [
    { symbol: "INFY", exchange: "NSE", quantity: 10, average_price: 1500.0, ltp: 1550.0, value: 15500.0, pnl: 500.0 },
    { symbol: "ITC", exchange: "NSE", quantity: 120, average_price: 455.2, ltp: 441.9, value: 53028.0, pnl: -1596.0 },
  ] },
  orders: { message: null, rows: [
    { symbol: "TCS", exchange: "NSE", side: "Sell", quantity: 5, filled_quantity: 0, order_type: "LIMIT", product: "CNC", status: "OPEN", price: 3600.0, average_price: null, placed_at: "2026-10-19T05:12:00+00:00" },
    { symbol: "HDFCBANK", exchange: "NSE", side: "Buy", quantity: 40, filled_quantity: 40, order_type: "MARKET", product: "MIS", status: "COMPLETE", price: null, average_price: 1712.4, placed_at: "2026-10-19T04:01:00+00:00" },
  ] },
} as const;

type Part = keyof typeof LISTS;
const PAGES: { part: Part; path: string; page: string }[] = [
  { part: "positions", path: "/positions", page: "positions-v2" },
  { part: "holdings", path: "/positions/money", page: "portfolio-v2" },
  { part: "orders", path: "/positions/orders", page: "orders-v2" },
];

async function open(page: Page, path: string, { flag = true, empty = false } = {}) {
  const reads: string[] = [];
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ...(T.features as Record<string, boolean>), broker_book: flag }, brokers: T.token_status as unknown[] });
  await mockTrading(page);
  await page.route(/\/api(?:\/v1)?\/me\/broker-book/, async (route) => {
    const url = new URL(route.request().url());
    reads.push(`${route.request().method()} ${url.search}`);
    const parts = (url.searchParams.get("parts") || "positions,holdings,orders").split(",") as Part[];
    const pick = (lists: Record<string, unknown>) => Object.fromEntries(parts.map((p) => [p, lists[p]]));
    const accounts = empty ? [] : [{ ...ZERODHA, ...pick(LISTS) }, { ...UPSTOX, ...pick({ positions: STATIC_IP, holdings: STATIC_IP, orders: { rows: [], message: null } }) }];
    await route.fulfill({ json: { parts, accounts } });
  });
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("main")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  return reads;
}

async function proof(page: Page, name: string) {
  if (!PROOF) return;
  const card = page.locator("[data-block='broker-book']");
  if (await card.count()) await card.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${PROOF}/${name}.png`, fullPage: false, animations: "disabled" });
}

for (const size of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 360, height: 800 }]) {
  const phone = size.width < 768;
  test.describe(`From your broker ${size.width}`, () => {
    test.use(phone ? { viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size });

    for (const p of PAGES) {
      test(`${p.part}: read-only rows, a refusal sentence, Refresh`, async ({ page }) => {
        const before = await open(page, p.path, { flag: false });
        await expect(page.locator(`[data-page='${p.page}']`)).toBeVisible();
        await expect(page.locator("[data-block='broker-book']")).toHaveCount(0);       // flag off: nothing new, nothing read
        expect(before).toEqual([]);
        if (size.width !== 360) await proof(page, `${p.part}-${size.width}-before`);

        const reads = await open(page, p.path);
        const card = page.locator("[data-block='broker-book']");
        await expect(card).toBeVisible();
        expect(reads[0]).toBe(`GET ?parts=${p.part}`);
        await expect(card.locator("[data-broker-row]")).toHaveCount(LISTS[p.part].rows.length);
        await expect(card.locator("[data-broker-account='zerodha'] [data-broker-app]")).toHaveAttribute("href", "https://kite.zerodha.com");
        await expect(card.locator("[data-broker-account='upstox'] [data-broker-app]")).toHaveAttribute("href", "https://upstox.com");
        await expect(card.getByRole("button")).toHaveText(["Refresh"]);                  // no Exit / Modify / Cancel
        if (p.part === "orders") await expect(card.locator("[data-broker-account='upstox']")).toContainText("No orders today at Upstox.");
        else await expect(card.locator("[data-broker-account='upstox'] [role='alert']")).toContainText("Upstox needs your static IP for this list.");
        if (phone) await expectPhoneLayout(page);
        if (size.width !== 360) await proof(page, `${p.part}-${size.width}-after`);

        await card.locator("[data-broker-book-refresh]").click();
        await expect.poll(() => reads.length).toBe(2);
        expect(reads[1]).toBe(`GET ?parts=${p.part}&refresh=true`);
      });
    }

    test("no broker linked: one line and the connect button", async ({ page }) => {
      await open(page, "/positions/orders", { empty: true });
      const card = page.locator("[data-block='broker-book']");
      await expect(card).toContainText("Connect your broker to see its own orders today here.");
      if (phone) await expectPhoneLayout(page);
      if (size.width === 390) await proof(page, "orders-390-empty");
    });
  });
}
