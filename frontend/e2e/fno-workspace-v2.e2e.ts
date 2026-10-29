import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { mockCharts } from "./chartsMocks";
import { expectPhoneLayout } from "./phoneLayout";
import { T, mockTrading } from "./tradingMocks";

/**
 * FNO-4 (owner order ATP_SESSION_FNO_WORKSPACE.md sections 2, 8 and 9), behind `ui_v2`:
 * - F&O contracts in a watchlist: the contract's name, price, change, OI and days left; an expired contract moves to
 *   "Expired" with one-tap "Roll to next expiry", which only saves the list (the same strike in the next expiry, in place).
 *   Baselines `fno4-watchlist-v2-1440-<theme>.png` and the phone sheet `fno4-watchlist-v2-<360|390>-<theme>.png`.
 * - My positions with the PAPER account: Add (the one order ticket, PAPER, same contract and side), Adjust (the chain of
 *   that expiry), Exit now (today's confirm and endpoint, never blocked), and the legs of one basket together with their
 *   combined P&L. Baselines `fno4-positions-v2-<360|390|1440>-<theme>.png`.
 * Every reply is made-up data (no real account, price, order or id). After an intended change:
 * `npx playwright test --project=phone e2e/fno-workspace-v2.e2e.ts --update-snapshots`, check the new images, commit.
 */

const THEMES = ["light", "dark"] as const;
const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };
const api = (path: string) => new RegExp(`/api(?:/v1)?${path}`);
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

// --- made-up contracts (the clock is 19 Oct 2026, 11:00 IST) ---------------------------------------------------------------
const LIVE_CE = "NIFTY 24800 CE 27 OCT 26";
const FUT = "NIFTY FUT 27 OCT 26";
const OLD_PE = "NIFTY 24500 PE 14 OCT 26";
const NEXT_PE = "NIFTY 24500 PE 27 OCT 26";
const contract = (symbol: string, extra: Record<string, unknown>) => ({
  symbol, exchange: "NFO", known: true, listed: true, underlying: "NIFTY", lot_size: 75, spot_symbol: "NIFTY 50", spot_exchange: "NSE",
  expired: false, days_left: 8, roll: null, roll_note: null, expiry: "2026-10-27", ...extra,
});
const FACTS: Record<string, unknown> = {
  [LIVE_CE]: contract(LIVE_CE, { right: "CE", strike: 24800, label: "NIFTY 27 OCT 24800 CE" }),
  [FUT]: contract(FUT, { right: "FUT", strike: null, label: "NIFTY 27 OCT FUT" }),
  [NEXT_PE]: contract(NEXT_PE, { right: "PE", strike: 24500, label: "NIFTY 27 OCT 24500 PE" }),
  [OLD_PE]: contract(OLD_PE, { right: "PE", strike: 24500, label: "NIFTY 14 OCT 24500 PE", expiry: "2026-10-14", expired: true, days_left: null, listed: false,
    roll: { symbol: NEXT_PE, exchange: "NFO", underlying: "NIFTY", right: "PE", strike: 24500, expiry: "2026-10-27", lot_size: 75, label: "NIFTY 27 OCT 24500 PE" } }),
  "NIFTY 24900 CE 27 OCT 26": contract("NIFTY 24900 CE 27 OCT 26", { right: "CE", strike: 24900, label: "NIFTY 27 OCT 24900 CE" }),
  "NIFTY 25000 CE 27 OCT 26": contract("NIFTY 25000 CE 27 OCT 26", { right: "CE", strike: 25000, label: "NIFTY 27 OCT 25000 CE" }),
};

async function contractsRoute(page: Page, lookups: string[][]) {
  await page.route(api("/paper/fno/contracts$"), (r) => {
    const items = (r.request().postDataJSON() as { items: { symbol: string; exchange: string }[] }).items;
    lookups.push(items.map((i) => i.symbol));
    return r.fulfill({ json: { items: items.map((i) => FACTS[i.symbol] ?? { symbol: i.symbol, exchange: i.exchange, known: false }) } });
  });
}

async function prepare(page: Page, theme: "light" | "dark") {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      if (sessionStorage.getItem("e2e_seeded")) return;
      sessionStorage.setItem("e2e_seeded", "1");
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts", "positions"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
}

const FONT = "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }";

// --- the watchlist ------------------------------------------------------------------------------------------------------------

const LIST = { id: 1, name: "F&O", items: [{ symbol: LIVE_CE, exchange: "NFO" }, { symbol: OLD_PE, exchange: "NFO" }, { symbol: "NIFTY 50", exchange: "NSE" },
  { symbol: FUT, exchange: "NFO" }] };

const OI: Record<string, number> = { [LIVE_CE]: 1_250_000, [FUT]: 9_870_000 };

async function openCharts(page: Page, theme: "light" | "dark") {
  await prepare(page, theme);
  await mockApi(page, { features: { ui_v2: true, chart_workspace: true, watchlists: true }, brokers: CONNECTED });
  const calls = await mockCharts(page, { watchlists: [LIST] });
  const lookups: string[][] = [];
  await contractsRoute(page, lookups);
  // The batch quote with OI for the contracts (a made-up figure).
  await page.route(api("/market-data/quotes$"), (r) => {
    const items = (r.request().postDataJSON() as { items: { symbol: string; exchange: string }[] }).items;
    calls.quotes.push(items);
    return r.fulfill({ json: { source: { broker: "upstox", account_label: "primary" }, fetched_at: "2026-10-19T05:30:00Z", broker_calls: 1, missing: 0, warnings: [],
      items: items.map(({ symbol, exchange }, i) => ({ symbol, exchange, ltp: 100 + i * 37.5, prev_close: 98 + i * 37, change: 2 + i * 0.5, change_pct: 1.2 + i * 0.3,
        timestamp: "2026-10-19T05:29:58Z", age_seconds: 2, stale: false, stale_reason: null, source: "tick", missing: false, error: null,
        oi: OI[symbol] ?? null })) } });
  });
  await page.goto("/charts");
  await page.addStyleTag({ content: FONT });
  await page.waitForLoadState("networkidle");
  return { calls, lookups };
}

test.describe("FNO-4 watchlist contract rows, 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`contract rows and the Expired block look the same (${theme})`, async ({ page }) => {
      await openCharts(page, theme);
      const panel = page.locator("[data-watchlist]");
      await expect(panel.getByText("NIFTY 27 OCT 24800 CE")).toBeVisible();
      await expect(panel.getByText("8 days left · OI 12.5 L")).toBeVisible();
      await expect(panel.locator("[data-expired]")).toContainText("Expired (1)");
      await page.mouse.move(0, 0);
      await expect(page).toHaveScreenshot(`fno4-watchlist-v2-1440-${theme}.png`, SHOT);
    });
  }

  test("Roll to next expiry saves the list with the next contract in place - and nothing else", async ({ page }) => {
    const { calls, lookups } = await openCharts(page, "light");
    const panel = page.locator("[data-watchlist]");
    await expect.poll(() => lookups[0]).toEqual([LIVE_CE, OLD_PE, FUT]);            // the contract rows only (waits for the first lookup)
    // The rows still trading, then Expired; the expired one gets no price.
    await expect(panel.getByRole("list", { name: "Symbols in F&O" }).locator("[data-row]")).toHaveCount(3);
    // The expired row shows no price (the poll keeps the list's rows, so a later lookup never leaves a gap).
    await expect(panel.locator("[data-expired-row]")).not.toContainText(/\d+\.\d{2}/);
    const posts: string[] = [];
    page.on("request", (req) => { if (req.method() !== "GET") posts.push(`${req.method()} ${new URL(req.url()).pathname}`); });
    await panel.locator("[data-expired-row]").getByRole("button", { name: "Roll to next expiry" }).click();
    await expect.poll(() => calls.watchlistWrites.length).toBe(1);
    const body = calls.watchlistWrites[0].body as { items: { symbol: string }[]; version: number };
    expect(body.items.map((i) => i.symbol)).toEqual([LIVE_CE, NEXT_PE, "NIFTY 50", FUT]);
    expect(body.version).toBe(1);
    await expect(panel.getByText("Rolled NIFTY 14 OCT 24500 PE to NIFTY 27 OCT 24500 PE in F&O. Nothing was traded.")).toBeVisible();
    await expect(panel.locator("[data-expired]")).toHaveCount(0);
    await expect(panel.getByRole("list", { name: "Symbols in F&O" }).locator("[data-row]").nth(1)).toContainText("NIFTY 27 OCT 24500 PE");
    // The only writes: the list save (and the lookup, which reads). No order, no ticket, no basket.
    expect(posts.filter((p) => !/\/watchlists\/1$|\/paper\/fno\/contracts$|\/market-data\/quotes$/.test(p))).toEqual([]);
  });

  test("with ui_v2's lookup refused, contract rows show as before (symbol and exchange)", async ({ page }) => {
    await prepare(page, "light");
    await mockApi(page, { features: { ui_v2: true, chart_workspace: true, watchlists: true }, brokers: CONNECTED });
    await mockCharts(page, { watchlists: [LIST] });
    await page.route(api("/paper/fno/contracts$"), (r) => r.fulfill({ status: 404, json: { detail: "Not found" } }));
    await page.goto("/charts");
    await page.waitForLoadState("networkidle");
    const rows = page.getByRole("list", { name: "Symbols in F&O" }).locator("[data-row]");
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(1)).toContainText(OLD_PE);
    await expect(page.locator("[data-expired]")).toHaveCount(0);
  });
});

for (const size of [{ width: 360, height: 800 }, { width: 390, height: 844 }]) {
  test.describe(`FNO-4 watchlist sheet ${size.width}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
    for (const theme of THEMES) {
      test(`the sheet with contract rows looks the same (${theme})`, async ({ page }) => {
        await openCharts(page, theme);
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.getByRole("button", { name: "Watchlist", exact: true }).click();
        const sheet = page.getByRole("dialog", { name: "Watchlist" });
        await expect(sheet.getByText("NIFTY 27 OCT 24800 CE")).toBeVisible();
        await expect(sheet.getByRole("button", { name: "Roll to next expiry" })).toBeVisible();
        const box = await sheet.getByRole("button", { name: "Roll to next expiry" }).boundingBox();
        expect(box?.height ?? 0).toBeGreaterThanOrEqual(43.5);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(size.width);
        await expect(page).toHaveScreenshot(`fno4-watchlist-v2-${size.width}-${theme}.png`, SHOT);
      });
    }
  });
}

// --- positions ----------------------------------------------------------------------------------------------------------------

const leg = (id: number, symbol: string, direction: "LONG" | "SHORT", entry: number) => ({
  id, mode: "PAPER", symbol, strategy_id: "manual_fno", direction, entry_time: "2026-10-19T04:30:00", entry_price: entry, quantity: 75, stop_loss: 1,
  target1: null, target2: null, exit_time: null, exit_price: null, exit_reason: null, pnl: null, charges: 0, lot_size: 75, exchange: "NFO",
  instrument_kind: "OPTION", option_position: direction === "LONG" ? "BUY" : "WRITE", underlying_symbol: "NIFTY 50", expiry: "2026-10-27",
});
const POSITIONS = [
  { ...(T.positions as Record<string, unknown>[])[0], symbol: LIVE_CE },
  leg(301, "NIFTY 24900 CE 27 OCT 26", "SHORT", 80),
  leg(302, "NIFTY 25000 CE 27 OCT 26", "LONG", 45),
  ...(T.positions as Record<string, unknown>[]).slice(1),
];
const EXPOSURE = (() => {
  const e = T.exposure as { by_symbol: Record<string, unknown>[]; priced_symbols: string[] };
  const extra = [
    { symbol: LIVE_CE, positions: 1, quantity: 150, notional: 150 * 131.4, pct_of_capital: 2, unrealised_pnl: 0, risk_at_stop: 0, strategies: ["orb_nifty"] },
    { symbol: "NIFTY 24900 CE 27 OCT 26", positions: 1, quantity: 75, notional: 75 * 72.5, pct_of_capital: 1, unrealised_pnl: 0, risk_at_stop: 0, strategies: ["manual_fno"] },
    { symbol: "NIFTY 25000 CE 27 OCT 26", positions: 1, quantity: 75, notional: 75 * 41, pct_of_capital: 1, unrealised_pnl: 0, risk_at_stop: 0, strategies: ["manual_fno"] },
  ];
  return { ...e, by_symbol: [...e.by_symbol.filter((r) => r.symbol !== "NIFTY 24800 CE"), ...extra],
    priced_symbols: [...e.priced_symbols.filter((s) => s !== "NIFTY 24800 CE"), ...extra.map((r) => r.symbol)] };
})();

async function openPositions(page: Page, theme: "light" | "dark") {
  await prepare(page, theme);
  await mockApi(page, { features: { ...(T.features as Record<string, boolean>), paper_account: true }, brokers: T.token_status as unknown[] });
  const writes = await mockTrading(page, { paperAccount: true });
  await page.route(api("/positions$"), (r) => r.fulfill({ json: POSITIONS }));
  await page.route(api("/portfolio/exposure"), (r) => r.fulfill({ json: EXPOSURE }));
  const lookups: string[][] = [];
  await contractsRoute(page, lookups);
  await page.route(api("/paper/fno/positions/groups$"), (r) => r.fulfill({ json: { groups: [{ key: "basket:made-up", kind: "basket", name: "Basket", trade_ids: [301, 302] }] } }));
  await page.goto("/positions");
  await page.addStyleTag({ content: FONT });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  return { writes, lookups };
}

const SIZES = [{ width: 360, height: 800, phone: true }, { width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }];
for (const size of SIZES) {
  test.describe(`FNO-4 positions ${size.width}`, () => {
    test.use(size.phone ? { viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size });
    for (const theme of THEMES) {
      test(`Add / Adjust and the basket group look the same (${theme})`, async ({ page }) => {
        await openPositions(page, theme);
        const block = page.locator("[data-group='basket']");
        await expect(block).toContainText("Basket · 2 legs");
        await expect(block.locator("[data-col='group-pnl']")).toContainText("₹263");   // whole rupees; (80 - 72.5) x 75 + (41 - 45) x 75
        await block.scrollIntoViewIfNeeded();
        await page.mouse.move(0, 0);
        await expect(page.locator("[data-block='positions']")).toHaveScreenshot(`fno4-positions-v2-${size.width}-${theme}.png`, SHOT);
      });
    }
    if (size.phone) {
      test("no sideways scroll, 44 px targets", async ({ page }) => {
        await openPositions(page, "dark");
        await expectPhoneLayout(page);
      });
    }
  });
}

test.describe("FNO-4 positions actions, 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("Add opens the one ticket in practice mode on the same contract and side; nothing is sent until the user reviews", async ({ page }) => {
    const { writes } = await openPositions(page, "light");
    const short = page.locator("[data-position='301']");
    await short.getByTestId("fno-add").click();
    const ticket = page.locator("[data-order-ticket]");
    await expect(ticket.locator("[data-practice-frame]")).toHaveText("PAPER");
    await expect(ticket).toContainText("NIFTY 27 OCT 24900 CE");
    await expect(ticket.locator("[data-ticket='side'] [aria-checked='true']")).toHaveAttribute("data-option", "SELL");
    expect(writes.calls.filter((c) => /\/paper\/fno\/order/.test(c.url))).toEqual([]);
  });

  test("Adjust opens the chain of that expiry; a basket leg exits on its own (TT-8: Exit this leg, confirmed)", async ({ page }) => {
    const { writes } = await openPositions(page, "light");
    const leg = page.locator("[data-position='302']");
    await expect(leg.getByTestId("fno-adjust")).toHaveAttribute("href", "/option-chain?underlying=NIFTY&expiry=2026-10-27");
    await expect(leg.getByTestId("exit-leg")).toBeEnabled();
    await leg.getByTestId("exit-leg").click();
    await page.getByTestId("group-exit-confirm").click();
    await expect.poll(() => writes.calls.map((c) => `${c.method} ${c.url}`)).toContain("POST /paper/fno/positions/302/exit-leg");
    // A stock position has no F&O actions; a LIVE one neither.
    await expect(page.locator("[data-position='202']").getByTestId("fno-add")).toHaveCount(0);
    await expect(page.locator("[data-position='203']").getByTestId("fno-add")).toHaveCount(0);
  });

  test("without the PAPER account: no Add / Adjust, no lookup, no groups - the page is as before", async ({ page }) => {
    await prepare(page, "light");
    await mockApi(page, { features: T.features as Record<string, boolean>, brokers: T.token_status as unknown[] });
    await mockTrading(page);
    const lookups: string[][] = [];
    await contractsRoute(page, lookups);
    await page.goto("/positions");
    await page.waitForLoadState("networkidle");
    await expect(page.getByTestId("exit-now").first()).toBeVisible();
    await expect(page.getByTestId("fno-add")).toHaveCount(0);
    await expect(page.locator("[data-group]")).toHaveCount(0);
    expect(lookups).toEqual([]);
  });
});
