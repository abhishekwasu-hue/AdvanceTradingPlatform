import { expect, test, type Locator, type Page } from "@playwright/test";
import { choose, expectChoiceLabels } from "./combobox";
import { readFileSync } from "node:fs";
import { mockApi, type MockCalls, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * FNO-1: trading from the option chain in PAPER (owner order sections 3 and 6) - the action sheet of a call / put cell
 * and the ONE order ticket, on the OC1 hand-made chain (e2e/fixtures/option_chain_v2.json) with `ui_v2` and the PAPER
 * account on. Every /api/paper/fno answer is made up here (round numbers, not market data).
 * - Baselines (NEW, only the sheet and the ticket): `chain-actions-v2-<width>-<theme>.png` and
 *   `order-ticket-v2-<width>-<theme>.png` at 1440 and 390, light and dark. The chain page's own baselines are untouched.
 * - Each action of the sheet, the ticket's lots / units, order type, price helper, confirm step (margin and max loss
 *   before placing), B / S keys, the "P" marker with Exit (the existing "Exit now"), the basket badge; without the PAPER
 *   account a row still opens its details (OC1, unchanged).
 * After an intended change: `npx playwright test --project=phone e2e/chain-ticket-v2.e2e.ts --update-snapshots`.
 */
const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown> & { rows: unknown[] };
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, paper_account: true, watchlists: true, screener_v2: true }, brokers: CONNECTED };
const THEMES = ["light", "dark"] as const;
const CONTRACT = { underlying: "NIFTY", tradingsymbol: "NIFTY26N0324500CE", exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-11-03",
  strike: 24500, right: "CE", lot_size: 75, tick_size: 0.05, label: "NIFTY 03 NOV 24500 CE", freeze_quantity: null };
const HELD = { trade_id: 41, tradingsymbol: "NIFTY26N0324500CE", strike: 24500, right: "CE", side: "BUY", quantity: 75, lots: 1, lot_size: 75,
  entry_price: 140, mark_price: 150, pnl: 750, stop_loss: 0.05 };

function preview(body: Record<string, unknown>) {
  const lots = Number(body.lots ?? 1);
  const units = lots * 75;
  const fill = body.side === "SELL" ? 149.5 : 150.5;
  return {
    mode: "PAPER", practice: "Practice mode (PAPER): no real order is sent and no money moves.", contract: CONTRACT, label: CONTRACT.label,
    side: body.side, lots, lot_size: 75, units, order_type: body.order_type, product: body.product, price: 150, price_source: "live", fill_price: fill,
    fill_note: `Fills at the live price ${fill.toFixed(2)}`, stop_loss: body.stop_loss ?? null,
    margin: { value: fill * units, basis: "Long options only: the premium paid.", estimated: true },
    max_loss: { value: fill * units, basis: "The premium you pay, if the option expires worthless." }, charges: { total: 42.5, estimated: true },
    risk: { risk_per_trade: 50000, allowed_lots: 4 },
    errors: [], refusals: lots > 4 ? ["Your risk per trade (₹50,000) allows 4 lot(s) at this stop-loss. Lower the lots or set a closer stop-loss."] : [],
    warnings: [], can_place: lots <= 4, confirm_label: null, as_of: "2026-10-27T05:00:00+00:00",
  };
}

interface Seen { posts: MockCalls["posts"]; held: number }

/** F1 / M68 PR-1 / PR-2: with no lots, product and order type saved nothing is pre-filled; enter 1 lot (when none is
 * entered yet), pick Market (when no type is picked yet) and NRML so Review can be tapped. */
async function pickProduct(ticket: Locator): Promise<void> {
  if (await ticket.locator("[data-ticket='lots']").inputValue() === "") await ticket.locator("[data-ticket='lots']").fill("1");
  if (await ticket.locator("[data-ticket='order-type'] [aria-checked='true']").count() === 0) {
    await ticket.locator("[data-ticket='order-type'] [data-option='MARKET']").click();
  }
  await ticket.locator("[data-ticket='product'] [data-option='NRML']").click();
}

async function open(page: Page, theme: "light" | "dark", opts: { options?: MockOptions; held?: unknown[]; tradingDefaults?: unknown;
  /** M68 PR-1: the Trade settings saves ("Save as my default") are recorded here; the read answers Not set. */
  tradeSettings?: Record<string, unknown>[] } = {}): Promise<Seen> {
  await page.clock.install({ time: new Date("2026-10-27T10:30:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["option-chain"]));
      sessionStorage.removeItem("atp_fno_basket");
    } catch { /* storage unavailable */ }
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get: () => "visible" });
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get: () => false });
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  const calls = await mockApi(page, opts.options ?? ON);
  const seen: Seen = { posts: calls.posts, held: 0 };
  let held = opts.held ?? [];
  const sessions = { NSE: { is_open: true, reason: "made up", next_open: null } };
  await page.route(/\/api(?:\/v1)?\/system\/status$/, (r) => r.fulfill({ json: { maintenance_mode: false, maintenance_message: null, disabled_brokers: [], sessions } }));
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/expiries")) return route.fulfill({ json: DATA.expiries });
    return route.fulfill({ json: { ...DATA.live, expiry: url.searchParams.get("expiry") ?? DATA.live.expiry, market_open: true } });
  });
  await page.route(/\/api(?:\/v1)?\/paper\/fno\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/contract")) return route.fulfill({ json: { ...CONTRACT, strike: Number(url.searchParams.get("strike")), right: url.searchParams.get("right") } });
    if (url.pathname.endsWith("/held")) { seen.held += 1; return route.fulfill({ json: held }); }
    // 8b-d: the risk line's budget (not set up here: the line is left out, the ticket is unchanged).
    if (url.pathname.endsWith("/risk-budget")) return route.fulfill({ status: 404, json: { detail: "Not found" } });
    // 8b slice 4: the events calendar (not set up here: the line says it could not be read).
    if (url.pathname.endsWith("/event-risk")) return route.fulfill({ status: 404, json: { detail: "Not found" } });
    const body = request.postDataJSON() as Record<string, unknown>;
    seen.posts.push({ url: url.pathname, method: request.method(), body });
    if (url.pathname.endsWith("/order/preview")) return route.fulfill({ json: preview(body) });
    held = [HELD];
    return route.fulfill({ json: { ...preview(body), status: "PLACED", reasons: [], order_id: 9, trade_id: 41, filled: { price: 150.5, quantity: 75 * Number(body.lots), lots: Number(body.lots) } } });
  });
  await page.route(/\/api(?:\/v1)?\/positions\/41\/exit-now$/, async (route) => {
    seen.posts.push({ url: "/positions/41/exit-now", method: "POST", body: null });
    held = [];
    return route.fulfill({ json: { closed: [{ closed: true, exit_reason: "Exit now (web)", exit_price: 150, pnl: 750 }], warnings: [] } });
  });
  await page.route(/\/api(?:\/v1)?\/watchlists$/, (r) => r.fulfill({ json: [{ id: 3, name: "My list", items: [{ symbol: "RELIANCE", exchange: "NSE" }], version: 2 }] }));
  await page.route(/\/api(?:\/v1)?\/watchlists\/3$/, async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    seen.posts.push({ url: "/watchlists/3", method: "PUT", body });
    return route.fulfill({ json: { id: 3, name: "My list", items: body.items, version: 3 } });
  });
  await page.route(/\/api(?:\/v1)?\/alerts\/rules$/, async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    seen.posts.push({ url: "/alerts/rules", method: "POST", body });
    return route.fulfill({ status: 201, json: { id: 5, ...body, status: "active" } });
  });
  // Settings part C: the user's own ticket defaults (left out: the prefs read is a 404, the ticket starts as today).
  if (opts.tradingDefaults) {
    await page.route(/\/api(?:\/v1)?\/me\/prefs$/, (r) => r.fulfill({ json: { prefs: { trading_defaults: opts.tradingDefaults }, versions: { trading_defaults: 1 } } }));
  }
  if (opts.tradeSettings) {
    const saves = opts.tradeSettings;
    await page.route(/\/api(?:\/v1)?\/me\/trade-settings$/, async (route) => {
      const empty = { admin: false, page: "/settings/trade-settings", sections: [] };
      if (route.request().method() === "PUT") saves.push((route.request().postDataJSON() as { values: Record<string, unknown> }).values);
      return route.fulfill({ json: empty });
    });
  }
  await page.goto("/option-chain");
  await page.addStyleTag({ content: [
    "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
    "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  ].join("\n") });
  await expect(page.locator("tr[data-atm]")).toBeVisible();
  return seen;
}

/** The ATM row's first visible call cell (a tap there opens the call's actions). */
const callCell = (page: Page) => page.locator("tr[data-strike='24500'] td:visible").first();

for (const [width, height] of [[1440, 900], [390, 844]] as const) {
  test.describe(`Chain ticket v2 at ${width}`, () => {
    test.use(width === 390 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });

    for (const theme of THEMES) {
      test(`the action sheet and the ticket look the same (${theme})`, async ({ page }) => {
        await open(page, theme);
        await callCell(page).click();
        const sheet = page.locator("[data-chain-actions]");
        await expect(sheet).toBeVisible();
        await expect(sheet.getByRole("heading")).toContainText("NIFTY 03 NOV 24500");
        await expect(sheet.locator("[data-action='watchlist']")).toBeEnabled();
        await expect(sheet).toHaveScreenshot(`chain-actions-v2-${width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
        await sheet.locator("[data-action='buy']").click();
        const ticket = page.locator("[data-order-ticket]");
        await expect(ticket).toBeVisible();
        await expect(ticket.locator("[data-ticket='units']")).toHaveText("Lot size 75");      // M68 PR-2: lots asked first
        await expect(ticket).toHaveScreenshot(`order-ticket-v2-${width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }
  });
}

test.describe("Chain ticket v2 flows", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("tap a call -> Buy -> 2 lots, Limit at the bid -> the confirm step shows margin and max loss -> one PAPER order -> P", async ({ page }) => {
    const seen = await open(page, "light");
    await expectPhoneLayout(page);
    await callCell(page).tap();
    const sheet = page.locator("[data-chain-actions]");
    await expect(sheet).toHaveAttribute("data-right", "CE");
    await expect(sheet.locator("[data-practice-frame]")).toHaveText("PAPER");
    await sheet.locator("[data-action='buy']").tap();
    const ticket = page.locator("[data-order-ticket]");
    await expect(ticket.locator("[data-practice-frame]")).toContainText("PAPER");
    await ticket.locator("[data-ticket='lots-more']").tap();                                    // M68 PR-2: from empty to 1
    await expect(ticket.locator("[data-ticket='units']")).toHaveText("1 lot = 75 qty (lot size 75)");
    await ticket.locator("[data-ticket='lots-more']").tap();
    await expect(ticket.locator("[data-ticket='units']")).toHaveText("2 lots = 150 qty (lot size 75)");
    await ticket.getByRole("radio", { name: "Limit" }).tap();
    await ticket.locator("[data-helper='Bid']").tap();
    await expect(ticket.locator("[data-ticket='price']")).toHaveValue("149.50");
    expect(seen.posts.filter((p) => p.url.includes("/paper/fno/order"))).toEqual([]);   // nothing sent before Review
    await pickProduct(ticket);
    await ticket.locator("[data-ticket='review']").tap();
    const confirm = ticket.locator("[data-ticket='confirm']");
    await expect(confirm.locator("[data-line='margin']")).toContainText("₹22,575.00");
    await expect(confirm.locator("[data-line='max-loss']")).toContainText("₹22,575.00");
    expect(seen.posts.filter((p) => p.url.endsWith("/order"))).toEqual([]);             // still nothing placed
    await ticket.locator("[data-ticket='place']").tap();
    await expect(ticket.locator("[data-ticket='result']")).toHaveText("Placed in practice mode: Bought 2 lots (150 qty) of NIFTY 03 NOV 24500 CE at 150.50.");
    const placed = seen.posts.filter((p) => p.url.endsWith("/paper/fno/order"));
    expect(placed).toHaveLength(1);
    expect(placed[0].body).toMatchObject({ underlying: "NIFTY", expiry: "2026-11-03", strike: 24500, right: "CE", side: "BUY", lots: 2, order_type: "LIMIT", price: 149.5 });
    expect(placed[0].body).not.toHaveProperty("mode");
    await ticket.locator("[data-ticket='done']").tap();
    await expect(page.locator("tr[data-strike='24500'] [data-held='call']")).toBeVisible();
  });

  test("Settings part C: the user's ticket defaults start the ticket, marked, and can be changed", async ({ page }) => {
    const seen = await open(page, "light", { tradingDefaults: { ticket: { lots: 2, product: "NRML", order_type: "MARKET" } } });
    await callCell(page).tap();
    await page.locator("[data-chain-actions] [data-action='buy']").tap();
    const ticket = page.locator("[data-order-ticket]");
    await expect(ticket.locator("[data-ticket='lots']")).toHaveValue("2");
    await expect(ticket.locator("[data-ticket='advanced-summary']")).toHaveText("NRML · no stop-loss");     // UX-9
    await ticket.locator("[data-ticket='advanced'] summary").tap();
    await expect(ticket.locator("[data-ticket='product'] [data-option='NRML']")).toHaveAttribute("aria-checked", "true");
    await expect(ticket.locator("[data-from-defaults='lots']")).toHaveText("from your defaults");
    await expect(ticket.locator("[data-from-defaults='type-product']")).toHaveText("Order type and product from your defaults. Tap to change.");
    await ticket.locator("[data-ticket='product'] [data-option='MIS']").tap();
    await expect(ticket.locator("[data-from-defaults='type-product']")).toHaveText("Order type from your defaults. Tap to change.");
    await ticket.locator("[data-ticket='review']").tap();
    const asked = () => seen.posts.filter((p) => p.url.endsWith("/order/preview"));
    await expect.poll(() => asked().length).toBe(1);
    expect(asked()[0].body).toMatchObject({ side: "BUY", lots: 2, product: "MIS", order_type: "MARKET" });
  });

  test("a refused ticket says why in plain words and cannot be placed", async ({ page }) => {
    await open(page, "light");
    await callCell(page).tap();
    await page.locator("[data-chain-actions] [data-action='sell']").tap();
    const ticket = page.locator("[data-order-ticket]");
    await pickProduct(ticket);
    // DEF-0 D7: no stop is forced on a sold option - the trader sets his own. M68 TS-3: "More options" is still open from
    // the product he just picked there (its "Save as my default" sits next to it).
    await expect(ticket.locator("[data-ticket='advanced']")).toHaveAttribute("open", "");
    await ticket.locator("[data-ticket='stop']").fill("200");
    await ticket.locator("[data-ticket='lots']").fill("6");
    await ticket.locator("[data-ticket='review']").tap();
    await expect(ticket.locator("[data-ticket='refusals']")).toContainText("allows 4 lot(s) at this stop-loss");
    await expect(ticket.locator("[data-ticket='place']")).toBeDisabled();
  });

  test("Add to basket, Add to watchlist, Price alert, Chart of this strike", async ({ page }) => {
    const seen = await open(page, "dark");
    const badge = page.locator("[data-strip='basket']");
    await expect(badge).toHaveAttribute("aria-label", "Basket: 0 legs");
    await callCell(page).tap();
    const sheet = page.locator("[data-chain-actions]");
    await sheet.locator("[data-action='basket']").tap();
    await sheet.locator("[data-action='basket-sell']").tap();
    await expect(sheet.locator("[data-action-note='done']")).toHaveText("Added to the basket: Sell NIFTY 03 NOV 24500 CE. Enter its lots in the basket.");   // M68 PR-2
    await expect(badge).toHaveAttribute("aria-label", "Basket: 1 leg");
    await sheet.locator("[data-action='watchlist']").tap();
    await sheet.locator("[data-watchlist='3']").tap();
    await expect(sheet.locator("[data-action-note='done']")).toHaveText("Added NIFTY 03 NOV 24500 CE to My list.");
    expect(seen.posts.find((p) => p.url === "/watchlists/3")?.body).toMatchObject({ items: [{ symbol: "RELIANCE", exchange: "NSE" }, { symbol: "NIFTY26N0324500CE", exchange: "NFO" }], version: 2 });
    await sheet.locator("[data-action='alert']").tap();
    await sheet.locator("[data-alert-level]").fill("180");
    await sheet.locator("[data-action='alert-save']").tap();
    await expect(sheet.locator("[data-action-note='done']")).toContainText("at or above 180.00");
    expect(seen.posts.find((p) => p.url === "/alerts/rules")?.body).toMatchObject({ kind: "instrument", symbol: "NIFTY26N0324500CE", exchange: "NFO", condition: "close >= 180" });
    await expect(sheet.locator("[data-action='chart']")).toHaveAttribute("href", "/charts/NIFTY26N0324500CE?exchange=NFO");
    expect(seen.posts.filter((p) => p.url.includes("/paper/fno/order"))).toEqual([]);   // none of these places an order
  });

  test("a held strike shows P; Exit uses Exit now (confirmed) and is never disabled", async ({ page }) => {
    const seen = await open(page, "light", { held: [HELD] });
    await expect(page.locator("tr[data-strike='24500'] [data-held='call']")).toBeVisible();
    await callCell(page).tap();
    const held = page.locator("[data-chain-actions] [data-held]");
    await expect(held).toContainText("Bought 1 lot at 140.00");
    await expect(held).toContainText("P&L +₹750");
    const exit = held.locator("[data-action='held-exit']");
    await expect(exit).toBeEnabled();
    page.once("dialog", (d) => d.accept());
    await exit.tap();
    await expect(page.locator("[data-chain-trade-note='done']")).toContainText("Closed");
    expect(seen.posts.some((p) => p.url === "/positions/41/exit-now")).toBe(true);
    await expect(page.locator("[data-held='call']")).toHaveCount(0);
  });

  test("tap ATM brings the ATM row back to the middle; OI change in %", async ({ page }) => {
    await open(page, "light");
    const box = page.locator("[data-chain-scroll]");
    await box.evaluate((e) => { e.scrollTop = 0; });
    await page.locator("[data-strip='atm']").tap();
    await expect.poll(() => box.evaluate((e) => e.scrollTop)).toBeGreaterThan(0);
    await page.locator("[data-strip='change']").getByRole("radio", { name: "%" }).tap();
    await expect(page.locator("[data-strip='change']").getByRole("radio", { name: "%" })).toHaveAttribute("aria-checked", "true");
  });

  test("without the PAPER account the chain is OC1's: a tap opens the row's details, nothing to trade", async ({ page }) => {
    await open(page, "light", { options: { features: { ui_v2: true }, brokers: CONNECTED } });
    await callCell(page).tap();
    await expect(page.locator("[data-row-details]")).toBeVisible();
    await expect(page.locator("[data-chain-actions]")).toHaveCount(0);
    await expect(page.locator("[data-block='chain-bottom-strip']")).toHaveCount(0);
  });
});

test.describe("Chain ticket v2 keys", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("B and S choose the side on the desktop ticket", async ({ page }) => {
    await open(page, "light");
    await callCell(page).click();
    await page.locator("[data-chain-actions] [data-action='buy']").click();
    const side = page.locator("[data-order-ticket] [data-ticket='side']");
    await expect(side.getByRole("radio", { name: "Buy" })).toHaveAttribute("aria-checked", "true");
    await page.keyboard.press("s");
    await expect(side.getByRole("radio", { name: "Sell" })).toHaveAttribute("aria-checked", "true");
    await expect(page.locator("[data-ticket='review']")).toHaveText("Review Sell order");
    await page.keyboard.press("b");
    await expect(side.getByRole("radio", { name: "Buy" })).toHaveAttribute("aria-checked", "true");
    // Typing in a field never switches the side.
    // F1: with no product saved, More options already opens on the product; open it only when it is closed.
    if (!(await page.locator("[data-ticket='stop']").isVisible())) await page.locator("[data-ticket='advanced'] summary").click();
    await page.locator("[data-ticket='stop']").fill("s");
    await expect(side.getByRole("radio", { name: "Buy" })).toHaveAttribute("aria-checked", "true");
  });
});

/** M45 MA-6: "Run on" for the user's practice accounts (`ui_v2` + `multi_account`): one PAPER order per account, a row
 * per account (placed / skipped with the reason), and one exit for every account. */
test.describe("Chain ticket v2 multi-account (M45 MA-6)", () => {
  test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });

  test("Run on Primary + Family places one order per account and one exit closes them all", async ({ page }) => {
    const posts: { url: string; body: unknown }[] = [];
    const practice = [
      { id: 1, label: "Main", capital: 500000, role: "PRIMARY", group_name: null, enabled: true, sizing_mode: "MULTIPLIER", sizing_value: 1 },
      { id: 2, label: "Family", capital: 200000, role: "SECONDARY", group_name: "Family", enabled: true, sizing_mode: "MULTIPLIER", sizing_value: 2 },
      { id: 3, label: "Small", capital: 1000, role: "SECONDARY", group_name: "Family", enabled: true, sizing_mode: "MULTIPLIER", sizing_value: 1 },
    ];
    await open(page, "light", { options: { ...ON, features: { ...ON.features, multi_account: true } } });
    // Registered after the page's own mocks so they answer first (the ticket reads them when it opens).
    await page.route(/\/api(?:\/v1)?\/accounts\/mine\/paper$/, (r) => r.fulfill({ json: practice }));
    await page.route(/\/api(?:\/v1)?\/accounts\/mine\/paper\/order$/, (r) => {
      posts.push({ url: "order", body: r.request().postDataJSON() });
      return r.fulfill({ json: { why: "override PRIMARY+Family", override: "PRIMARY+Family", results: [
        { account_id: 3, label: "Small", status: "SKIPPED", lots: 0, trade_id: null, reasons: ["capital 1,000 is below the estimated margin 11,288 for 1 lot(s)"] },
        { account_id: 1, label: "Main", status: "PLACED", lots: 1, trade_id: 41, reasons: [] },
        { account_id: 2, label: "Family", status: "PLACED", lots: 2, trade_id: 42, reasons: [] },
      ] } });
    });
    await page.route(/\/api(?:\/v1)?\/accounts\/mine\/paper\/exit$/, (r) => {
      posts.push({ url: "exit", body: r.request().postDataJSON() });
      return r.fulfill({ json: { ref: "x", results: [
        { trade_id: 41, account_id: 1, label: "Main", status: "CLOSED", reasons: [] },
        { trade_id: 42, account_id: 2, label: "Family", status: "CLOSED", reasons: [] },
      ] } });
    });
    await callCell(page).click();
    await page.locator("[data-chain-actions] [data-action='buy']").click();
    const ticket = page.locator("[data-order-ticket]");
    const runOn = ticket.locator("[data-ticket='run-on']");
    await expectChoiceLabels(page, runOn, ["This order only", "Primary account", "Primary + Family", "All accounts"]);
    await choose(page, runOn, "PRIMARY+Family");
    await pickProduct(ticket);
    await ticket.locator("[data-ticket='review']").click();
    await ticket.locator("[data-ticket='place']").click();
    const result = ticket.locator("[data-ticket='multi-result']");
    await expect(result).toContainText("Placed on 2 of 3 accounts");
    await expect(result.locator("[data-multi-row='SKIPPED']")).toContainText("below the estimated margin");
    expect(posts[0]).toMatchObject({ url: "order", body: { run_on: "PRIMARY+Family", order: { underlying: "NIFTY", strike: 24500, lots: 1 } } });
    await ticket.locator("[data-ticket='exit-all']").click();
    // UI-28 U3.4: the in-app confirm first, naming the accounts it closes.
    const ask = page.getByRole("dialog", { name: "Exit on every account?" });
    await expect(ask).toContainText("This closes the position on 2 accounts at the current price.");
    await ask.locator("[data-confirm]").click();
    await expect(ticket.locator("[data-ticket='multi-exit']")).toHaveText("Exit sent: 2 closed.");
    expect((posts[1].body as { ref: string }).ref).toBe((posts[0].body as { order: { client_ref: string } }).order.client_ref);
  });

  test("without multi_account the ticket has no Run on", async ({ page }) => {
    await open(page, "light");
    await callCell(page).click();
    await page.locator("[data-chain-actions] [data-action='buy']").click();
    await expect(page.locator("[data-order-ticket] [data-ticket='review']")).toBeVisible();
    await expect(page.locator("[data-ticket='run-on']")).toHaveCount(0);
  });
});

/** M68 PR-1 / PR-2 (TS-3, TS-7): a new user (nothing saved) - the ticket pre-fills no lots, no order type and no product;
 * it asks for each one, focused, with "Save as my default"; only then is ONE PAPER order placed. Baselines (NEW):
 * `order-ticket-asks-v2-<width>-light.png` at 1440 and 390. */
for (const [width, height] of [[1440, 900], [390, 844]] as const) {
  test.describe(`M68 ticket asks at ${width}`, () => {
    test.use(width === 390 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });

    test("a new user places a PAPER order only after choosing the lots, the order type and the product", async ({ page }) => {
      const saves: Record<string, unknown>[] = [];
      const seen = await open(page, "light", { tradeSettings: saves });
      await callCell(page).click();
      await page.locator("[data-chain-actions] [data-action='buy']").click();
      const ticket = page.locator("[data-order-ticket]");
      // M68 PR-2 (TS1-020): the lots are asked first - empty and focused, nothing pre-filled
      const lots = ticket.locator("[data-ticket='lots']");
      await expect(lots).toHaveValue("");
      await expect(lots).toBeFocused();
      await expect(ticket.locator("[data-ticket='lots-unset']")).toHaveText("Enter the lots (none saved in your Trade settings).");
      await expect(ticket.locator("[data-ticket='review']")).toBeDisabled();
      if (width === 390) await expectPhoneLayout(page);
      await expect(ticket).toHaveScreenshot(`order-ticket-asks-lots-v2-${width}-light.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      await lots.fill("1");
      const type = ticket.locator("[data-ticket='order-type']");
      await expect(type).toHaveAttribute("data-asking", "yes");
      await expect(type.locator("[data-option='MARKET']")).toBeFocused();
      await expect(ticket.locator("[data-ticket='order-type-unset']")).toHaveText("Pick how the order is sent: Market, Limit, SL or SL-M.");
      await expect(ticket.locator("[data-ticket='review']")).toBeDisabled();
      await expect(ticket.locator("[aria-checked='true'][data-option='MARKET'], [aria-checked='true'][data-option='MIS'], [aria-checked='true'][data-option='NRML']")).toHaveCount(0);
      await ticket.locator("[data-save-default='position_size.lots'] input").check();          // his lots, saved for next time
      await expect(ticket.locator("[data-save-default='position_size.lots'] [data-save-default-note]")).toHaveText("Saved");
      if (width === 390) await expectPhoneLayout(page);
      await expect(ticket).toHaveScreenshot(`order-ticket-asks-v2-${width}-light.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });

      await type.locator("[data-option='MARKET']").click();
      const product = ticket.locator("[data-ticket='product']");
      await expect(product).toHaveAttribute("data-asking", "yes");
      await expect(product.locator("[data-option='MIS']")).toBeFocused();
      await expect(ticket.locator("[data-ticket='review']")).toBeDisabled();
      expect(seen.posts.filter((p) => p.url.includes("/paper/fno/order"))).toEqual([]);         // nothing sent while it asks
      await product.locator("[data-option='NRML']").click();
      await ticket.locator("[data-save-default='order_defaults.product'] input").check();
      await expect(ticket.locator("[data-save-default='order_defaults.product'] [data-save-default-note]")).toHaveText("Saved");
      expect(saves).toEqual([{ "position_size.lots": 1 }, { "order_defaults.product": "NRML" }]);
      await ticket.locator("[data-ticket='review']").click();
      await ticket.locator("[data-ticket='place']").click();
      await expect(ticket.locator("[data-ticket='result']")).toContainText("Placed in practice mode");
      const placed = seen.posts.filter((p) => p.url.endsWith("/paper/fno/order"));
      expect(placed).toHaveLength(1);
      expect(placed[0].body).toMatchObject({ order_type: "MARKET", product: "NRML", lots: 1 });
    });
  });
}
