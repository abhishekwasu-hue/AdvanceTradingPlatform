import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { api, mockCharts } from "./chartsMocks";
import { mockApi, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * FNO-3: the symbol hub (owner order section 1) and the futures chain (section 4), v2 only, with every reply made up
 * here or in the OC1 fixture (round numbers, not market data).
 * - Baselines (NEW, only the hub's tab row and the futures chain): `hub-tabs-v2-<width>-<theme>.png` and
 *   `futures-chain-v2-<width>-<theme>.png` at 1440 and 390, light and dark. No existing baseline changes: without a hub
 *   answer (every older spec) there is no tab row, and the chart and the chain render exactly as before.
 * - The tabs show / hide by F&O availability; the symbol, the expiry and the timeframe carry across the tabs; each
 *   futures row's actions (Chart, Buy / Sell -> the PAPER ticket, Add to basket, Add to watchlist); Overview.
 * After an intended change: `npx playwright test --project=phone e2e/hub-futures-v2.e2e.ts --update-snapshots`.
 */
const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown>;
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, paper_account: true, watchlists: true, chart_workspace: true }, brokers: CONNECTED };
const THEMES = ["light", "dark"] as const;

const NIFTY_HUB = {
  symbol: "NIFTY 50", underlying: "NIFTY", spot_symbol: "NIFTY 50", spot_exchange: "NSE", has_fno: true, has_options: true, has_futures: true,
  lot_size: 75, option_expiries: [{ date: "2026-10-27", kind: "monthly" }, { date: "2026-11-03", kind: "weekly" }], default_option_expiry: "2026-11-03",
  future_expiries: ["2026-10-27", "2026-11-24", "2026-12-29"], today: "2026-10-19", expiry_source: "master",
};
const PLAIN_HUB = { ...NIFTY_HUB, symbol: "BANKBEES", underlying: "BANKBEES", spot_symbol: "BANKBEES", has_fno: false, has_options: false,
  has_futures: false, lot_size: null, option_expiries: [], default_option_expiry: null, future_expiries: [] };

const fut = (sym: string, expiry: string, label: string, days: number, ltp: number | null, oi: number | null, oiChange: number | null) => ({
  underlying: "NIFTY", tradingsymbol: sym, exchange: "NFO", instrument_key: `NSE_FO|${sym}`, expiry, lot_size: 75, tick_size: 0.05, label,
  days_to_expiry: days, ltp, prev_close: ltp === null ? null : ltp - 40, change: ltp === null ? null : 40, change_pct: ltp === null ? null : 0.16,
  oi, oi_change: oiChange, volume: null, bid: ltp === null ? null : ltp - 0.5, ask: ltp === null ? null : ltp + 0.5,
  basis: ltp === null ? null : ltp - 24500, basis_pct: ltp === null ? null : Math.round(((ltp - 24500) / 24500) * 100_000) / 1000,
  missing: ltp === null, error: ltp === null ? "No price from your broker for this contract just now." : null,
});
const FUTURES = {
  underlying: "NIFTY", spot: { symbol: "NIFTY 50", exchange: "NSE", ltp: 24500, change: 50, change_pct: 0.2 },
  rows: [fut("NIFTY26OCTFUT", "2026-10-27", "NIFTY 27 OCT FUT", 8, 24560, 12_000_000, 150_000),
    fut("NIFTY26NOVFUT", "2026-11-24", "NIFTY 24 NOV FUT", 36, 24700, 4_000_000, 90_000),
    fut("NIFTY26DECFUT", "2026-12-29", "NIFTY 29 DEC FUT", 71, null, null, null)],
  rollover: { near_expiry: "2026-10-27", next_expiry: "2026-11-24", near_label: "NIFTY 27 OCT FUT", next_label: "NIFTY 24 NOV FUT",
    next_share_pct: 25, spread: 140, spread_pct: 0.57, near_oi: 12_000_000, next_oi: 4_000_000,
    caption: "25.0% of the near and next month futures OI is in the next month; the next month trades 140.00 above the near month." },
  oi_change: { status: "ready", source: "first_reading", since: "2026-10-19T03:50:00+00:00" }, market_open: true,
  source: { broker: "upstox", account_label: "primary" }, fetched_at: "2026-10-19T05:30:00+00:00", message: null,
};

interface Seen { posts: { url: string; body: unknown }[]; hub: string[]; futures: number }

async function open(page: Page, theme: "light" | "dark", path: string, opts: { hub?: Record<string, unknown>; options?: MockOptions } = {}): Promise<Seen> {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      if (sessionStorage.getItem("e2e_seeded")) return;
      sessionStorage.setItem("e2e_seeded", "1");
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts", "option-chain"]));
      sessionStorage.removeItem("atp_fno_basket");
    } catch { /* storage unavailable */ }
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get: () => "visible" });
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get: () => false });
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, opts.options ?? ON);
  await mockCharts(page, { watchlists: [{ id: 3, name: "My list", items: [{ symbol: "RELIANCE", exchange: "NSE" }] }] });
  const seen: Seen = { posts: [], hub: [], futures: 0 };
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/?]+)\/(hub|futures|expiries|live)/, async (route) => {
    const url = new URL(route.request().url());
    const name = decodeURIComponent(url.pathname.split("/").slice(-2)[0]);
    if (url.pathname.endsWith("/hub")) { seen.hub.push(name); return route.fulfill({ json: opts.hub ?? (name.startsWith("NIFTY") ? NIFTY_HUB : PLAIN_HUB) }); }
    if (url.pathname.endsWith("/futures")) { seen.futures += 1; return route.fulfill({ json: FUTURES }); }
    if (url.pathname.endsWith("/expiries")) return route.fulfill({ json: DATA.expiries });
    return route.fulfill({ json: { ...DATA.live, expiry: url.searchParams.get("expiry") ?? DATA.live.expiry, market_open: true } });
  });
  await page.route(/\/api(?:\/v1)?\/paper\/fno\//, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/held")) return route.fulfill({ json: [] });
    const body = route.request().postDataJSON() as Record<string, unknown>;
    seen.posts.push({ url: url.pathname, body });
    return route.fulfill({ json: {
      mode: "PAPER", practice: "Practice mode (PAPER): no real order is sent and no money moves.", contract: null, label: "NIFTY 27 OCT FUT",
      side: body.side, lots: body.lots, lot_size: 75, units: 75, order_type: body.order_type, product: body.product, price: 24560, price_source: "live",
      fill_price: 24560, fill_note: "Fills at the live price 24,560.00", stop_loss: body.stop_loss ?? null,
      margin: { value: null, estimated: true, basis: "A future needs your broker's SPAN and exposure margin, which practice mode does not have.", note: "Contract value ₹18,42,000" },
      max_loss: { value: 4500, basis: "Down to your stop-loss 24,500.00. A gap past the stop can lose more." }, charges: { total: 120, estimated: true },
      risk: { risk_per_trade: 50000, allowed_lots: 11 }, errors: [], refusals: [], warnings: [], can_place: true, confirm_label: null, as_of: "2026-10-19T05:30:00+00:00",
    } });
  });
  await page.route(api("/watchlists/3$"), async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    seen.posts.push({ url: "/watchlists/3", body });
    return route.fulfill({ json: { id: 3, name: "My list", items: body.items, version: 2 } });
  });
  await page.goto(path);
  await page.addStyleTag({ content: [
    "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
    "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  ].join("\n") });
  return seen;
}

const tabs = (page: Page) => page.locator("[data-hub-tabs] [data-hub-tab]");

for (const [width, height] of [[1440, 900], [390, 844]] as const) {
  test.describe(`Symbol hub v2 at ${width}`, () => {
    test.use(width === 390 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });

    for (const theme of THEMES) {
      test(`the hub tabs and the futures chain look the same (${theme})`, async ({ page }) => {
        await open(page, theme, "/charts/NIFTY%2050?hub=futures");
        const row = page.locator("[data-hub-tabs]");
        await expect(row).toBeVisible();
        await expect(tabs(page)).toHaveText(["Chart", "Option chain", "Futures", "Overview"]);
        await expect(row).toHaveScreenshot(`hub-tabs-v2-${width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
        const chain = page.locator("[data-block='futures-chain']");
        await expect(chain.getByText(FUTURES.rollover.caption)).toBeVisible();
        await expect(chain).toHaveScreenshot(`futures-chain-v2-${width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
        if (width === 390) await expectPhoneLayout(page);
      });
    }
  });
}

test.describe("Symbol hub v2 flows", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("Option chain and Futures show only for a symbol with F&O", async ({ page }) => {
    await open(page, "light", "/charts/BANKBEES");
    await expect(tabs(page)).toHaveText(["Chart", "Overview"]);
    await expect(page.locator("[data-hub-tab='option-chain'], [data-hub-tab='futures']")).toHaveCount(0);
    await page.locator("[data-hub-tab='overview']").click();
    const overview = page.locator("[data-block='hub-overview']");
    await expect(overview.locator("[data-fact='fno']")).toHaveText("F&ONo");
    await expect(overview.locator("[data-fact='lot'], [data-fact='expiries']")).toHaveCount(0);
  });

  test("the symbol, the expiry and the timeframe carry across the tabs", async ({ page }) => {
    const seen = await open(page, "light", "/charts/NIFTY%2050?tf=15min");
    await expect(page.locator("[data-hub-tab='chart']")).toHaveAttribute("aria-current", "page");
    await expect(page.locator("[data-hub-tab='option-chain']")).toHaveAttribute("href", "/option-chain?underlying=NIFTY&tf=15min");
    await page.locator("[data-hub-tab='option-chain']").click();
    await expect(page.locator("tr[data-atm]")).toBeVisible();
    await expect(page.locator("[data-hub-tab='option-chain']")).toHaveAttribute("aria-current", "page");
    await page.getByLabel("Expiry").selectOption("2026-11-10");
    await expect(page).toHaveURL(/\/option-chain\?underlying=NIFTY&tf=15min&expiry=2026-11-10$/);
    await expect(page.locator("[data-hub-tab='futures']")).toHaveAttribute("href", "/charts/NIFTY%2050?hub=futures&expiry=2026-11-10&tf=15min");
    await page.locator("[data-hub-tab='futures']").click();
    await expect(page.locator("[data-block='futures-chain']")).toBeVisible();
    expect(seen.futures).toBeGreaterThan(0);
    // Back to the option chain from Futures: the same expiry is selected.
    await page.locator("[data-hub-tab='option-chain']").click();
    await expect(page.getByLabel("Expiry")).toHaveValue("2026-11-10");
    // And to the chart: the timeframe is still 15 minutes.
    await page.locator("[data-hub-tab='chart']").click();
    await expect(page.locator("[data-page='charts-v2'] [aria-pressed='true']", { hasText: "15m" }).first()).toBeVisible();
    expect(seen.hub).toContain("NIFTY 50");
  });

  test("each futures row: Chart, Buy / Sell (the PAPER ticket), Add to basket, Add to watchlist", async ({ page }) => {
    const seen = await open(page, "dark", "/charts/NIFTY%2050?hub=futures&tf=15min");
    const near = page.locator("[data-future-row='NIFTY26OCTFUT']");
    await expect(near.locator("[data-cell='ltp']")).toHaveText("24,560.00");
    await expect(near.locator("[data-cell='basis']")).toContainText("+60.00");
    await expect(page.locator("[data-future-row='NIFTY26DECFUT'] [data-cell='ltp']")).toHaveText("–");
    await expect(near.locator("[data-action='chart']")).toHaveAttribute("href", "/charts/NIFTY26OCTFUT?tf=15min&exchange=NFO");

    await near.locator("[data-action='buy']").click();
    const ticket = page.locator("[data-order-ticket]");
    await expect(ticket.getByRole("heading")).toHaveText("NIFTY 27 OCT FUT");
    await expect(ticket.locator("[data-practice-frame]")).toContainText("Practice mode (PAPER)");
    await expect(ticket.locator("[data-ticket='units']")).toHaveText("1 lot = 75 qty (lot size 75)");
    await ticket.locator("[data-ticket='review']").click();
    await expect(ticket.locator("[data-ticket='problem']")).toHaveText("A future needs a stop-loss");
    await ticket.locator("[data-ticket='stop']").fill("24500");
    await ticket.locator("[data-ticket='review']").click();
    await expect(ticket.locator("[data-line='margin']")).toContainText("SPAN");
    await ticket.locator("[data-ticket='place']").click();
    const placed = seen.posts.filter((p) => p.url.endsWith("/paper/fno/order"));
    expect(placed).toHaveLength(1);
    expect(placed[0].body).toMatchObject({ underlying: "NIFTY", expiry: "2026-10-27", right: "FUT", strike: null, side: "BUY", lots: 1, stop_loss: 24500 });
    expect(placed[0].body).not.toHaveProperty("mode");
    await ticket.locator("[data-ticket='done']").click();

    const badge = page.locator("[data-futures-basket]");
    await expect(badge).toHaveAttribute("aria-label", "Basket: 0 legs");
    await near.locator("[data-action='basket']").click();
    await page.locator("[data-action='basket-sell']").click();
    await expect(page.locator("[data-futures-note='done']")).toHaveText("Added to the basket: Sell 1 lot of NIFTY 27 OCT FUT.");
    await expect(badge).toHaveAttribute("aria-label", "Basket: 1 leg");

    await near.locator("[data-action='watchlist']").click();
    await page.locator("[data-watchlist='3']").click();
    await expect(page.locator("[data-futures-note='done']")).toHaveText("Added NIFTY 27 OCT FUT to My list.");
    expect(seen.posts.find((p) => p.url === "/watchlists/3")?.body).toMatchObject({ items: [{ symbol: "RELIANCE", exchange: "NSE" }, { symbol: "NIFTY26OCTFUT", exchange: "NFO" }] });
    expect(seen.posts.filter((p) => p.url.includes("/paper/fno/order"))).toHaveLength(1);     // only the one placed by hand
  });

  test("without the PAPER account the futures chain has Chart and Add to watchlist only", async ({ page }) => {
    await open(page, "light", "/charts/NIFTY%2050?hub=futures", { options: { features: { ui_v2: true, watchlists: true }, brokers: CONNECTED } });
    const near = page.locator("[data-future-row='NIFTY26OCTFUT']");
    await expect(near.locator("[data-action='chart']")).toBeVisible();
    await expect(near.locator("[data-action='watchlist']")).toBeVisible();
    await expect(near.locator("[data-action='buy'], [data-action='sell'], [data-action='basket']")).toHaveCount(0);
  });

  test("Overview: the snapshot of the symbol", async ({ page }) => {
    await open(page, "light", "/charts/NIFTY%2050?hub=overview");
    const overview = page.locator("[data-block='hub-overview']");
    await expect(overview.locator("[data-fact='ltp']")).toBeVisible();
    await expect(overview.locator("[data-fact='fno']")).toHaveText("F&OYes");
    await expect(overview.locator("[data-fact='lot']")).toHaveText("Lot size75");
    await expect(overview.locator("[data-fact='expiries']")).toContainText("Futures expiries");
  });
});
