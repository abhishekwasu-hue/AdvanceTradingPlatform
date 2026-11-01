import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi, type MockCalls, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { T, mockTrading } from "./tradingMocks";

/**
 * F&O workspace 8b, slice 1: the ATP-only information features on the OC1 hand-made chain (e2e/fixtures/option_chain_v2.json)
 * with `ui_v2` and the PAPER account on. Every /api/paper/fno answer is made up here (round numbers, not market data).
 * - The setup-aware chain (8b-a): opened from one of the user's own signals (`?signal=action:12`), the strikes his own
 *   settings land on are ringed and named; nothing is picked, opened or placed by it.
 * - The ticket's confirm step (8b-c, 8b-d, 8b-h): the payoff in plain words, the risk line beside the user's own limits
 *   (a warning that never disables the Buy / Sell button) and Explain; the PAPER frame stays on.
 * - 8b slice 3: the panel's item now carries "Trade this setup" (the server's legs have their role); the baselines
 *   `fno8b-setup-chain-v2-*` were regenerated for it.
 * - Baselines (NEW): `fno8b-setup-chain-v2-<width>-<theme>.png` and `fno8b-ticket-review-v2-<width>-<theme>.png` at 1440,
 *   390 and 360, light and dark.
 * After an intended change: `npx playwright test --project=phone e2e/fno-insights-v2.e2e.ts --update-snapshots`.
 */
const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown> & { rows: unknown[] };
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, paper_account: true }, brokers: CONNECTED };
const THEMES = ["light", "dark"] as const;
const SIZES = [[1440, 900], [390, 844], [360, 780]] as const;
const CONTRACT = { underlying: "NIFTY", tradingsymbol: "NIFTY26N0324500CE", exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-11-03",
  strike: 24500, right: "CE", lot_size: 75, tick_size: 0.05, label: "NIFTY 03 NOV 24500 CE", freeze_quantity: null };
const BUDGET = { capital: 500000, per_trade: { amount: 5000, pct: 1 }, daily: { amount: 15000, pct: 3 }, source: "Your risk settings", blocks: false };
const SETUP = {
  source: "your_settings", signal: "action:12", underlying: "NIFTY", setup: "Made-up setup", side: "bull_put",
  reference: { text: "the current price", price: 24480 }, message: null,
  items: [{ id: 7, settings: "Bull put spread · main strike 1 strike out of the money · wing 2 strikes away · nearest expiry", expiry: "2026-11-03",
    legs: [{ label: "Main strike", right: "PE", strike: 24400, ratio: 1, role: "short" }, { label: "Wing", right: "PE", strike: 24200, ratio: 1, role: "long" }],
    note: null, lots: 1, stop_pct: 50 }],
};

function preview(body: Record<string, unknown>) {
  const lots = Number(body.lots ?? 1);
  const units = lots * 75;
  return {
    mode: "PAPER", practice: "Practice mode (PAPER): no real order is sent and no money moves.", contract: CONTRACT, label: CONTRACT.label,
    side: body.side, lots, lot_size: 75, units, order_type: body.order_type, product: body.product, price: 150, price_source: "live", fill_price: 150,
    fill_note: "Fills at the live price 150.00", stop_loss: body.stop_loss ?? null,
    margin: { value: 150 * units, basis: "Long options only: the premium paid.", estimated: true },
    max_loss: { value: 150 * units, basis: "The premium you pay, if the option expires worthless." }, charges: { total: 42.5, estimated: true },
    risk: { risk_per_trade: 50000, allowed_lots: 4 }, errors: [], refusals: [], warnings: [], can_place: true, confirm_label: null, as_of: "2026-10-27T05:00:00+00:00",
  };
}

interface Seen { posts: MockCalls["posts"]; gets: string[] }

async function open(page: Page, theme: "light" | "dark", path = "/option-chain"): Promise<Seen> {
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
  const calls = await mockApi(page, ON);
  const seen: Seen = { posts: calls.posts, gets: [] };
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
    if (request.method() === "GET") {
      seen.gets.push(url.pathname + url.search);
      if (url.pathname.endsWith("/contract")) return route.fulfill({ json: { ...CONTRACT, strike: Number(url.searchParams.get("strike")), right: url.searchParams.get("right") } });
      if (url.pathname.endsWith("/held")) return route.fulfill({ json: [] });
      if (url.pathname.endsWith("/risk-budget")) return route.fulfill({ json: BUDGET });
      if (url.pathname.endsWith("/setup-strikes")) return route.fulfill({ json: SETUP });
      return route.fulfill({ status: 404, json: { detail: "Not found" } });
    }
    const body = request.postDataJSON() as Record<string, unknown>;
    seen.posts.push({ url: url.pathname, method: request.method(), body });
    if (url.pathname.endsWith("/order/preview")) return route.fulfill({ json: preview(body) });
    return route.fulfill({ json: { ...preview(body), status: "PLACED", reasons: [], order_id: 9, trade_id: 41, filled: { price: 150, quantity: 75, lots: 1 } } });
  });
  await page.goto(path);
  await page.addStyleTag({ content: [
    "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
    "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  ].join("\n") });
  await expect(page.locator("tr[data-atm]")).toBeVisible();
  return seen;
}

const callCell = (page: Page) => page.locator("tr[data-strike='24500'] td:visible").first();
const orders = (seen: Seen) => seen.posts.filter((p) => p.url.includes("/paper/fno/order") || p.url.includes("/basket/"));

for (const [width, height] of SIZES) {
  test.describe(`F&O 8b slice 1 at ${width}`, () => {
    test.use(width < 1000 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });

    for (const theme of THEMES) {
      test(`the setup-aware chain looks the same (${theme})`, async ({ page }) => {
        await open(page, theme, "/option-chain?underlying=NIFTY&signal=action:12");
        const panel = page.locator("[data-setup-panel='ready']");
        await expect(panel).toBeVisible();
        await expect(page.locator("tr[data-strike='24400'] [data-setup-mark]")).toContainText("Main put");
        await panel.scrollIntoViewIfNeeded();
        await expect(page).toHaveScreenshot(`fno8b-setup-chain-v2-${width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });

      test(`the ticket's confirm step looks the same (${theme})`, async ({ page }) => {
        await open(page, theme);
        await callCell(page).click();
        await page.locator("[data-chain-actions] [data-action='buy']").click();
        const ticket = page.locator("[data-order-ticket]");
        // M68 PR-1: no order type is pre-selected either - pick Market where none is chosen yet
        if (await ticket.locator("[data-ticket='lots']").inputValue() === "") await ticket.locator("[data-ticket='lots']").fill("1");   // M68 PR-2: lots asked first
        if (await ticket.locator("[data-ticket='order-type'] [aria-checked='true']").count() === 0) await ticket.locator("[data-ticket='order-type'] [data-option='MARKET']").click();
        await ticket.locator("[data-ticket='product'] [data-option='NRML']").click();   // F1: no product saved - pick one before Review
        await ticket.locator("[data-ticket='review']").click();
        await expect(ticket.locator("[data-risk-checks='ticket']")).toBeVisible();
        await expect(ticket).toHaveScreenshot(`fno8b-ticket-review-v2-${width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }
  });
}

test.describe("F&O 8b slice 1 flows", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("from the user's own signal: his settings' strikes are marked, the choice stays his, nothing is placed", async ({ page }) => {
    const seen = await open(page, "light", "/option-chain?underlying=NIFTY&signal=action:12");
    await expectPhoneLayout(page);
    const panel = page.locator("[data-setup-panel='ready']");
    await expect(panel.locator("[data-setup='from-settings']")).toHaveText("From your settings");
    await expect(panel).toContainText("ATP does not pick strikes");
    await expect(panel.locator("[data-setup-item='7']")).toContainText("Main strike: 24,400 Put");
    expect(seen.gets.find((g) => g.includes("/setup-strikes"))).toMatch(/\/paper\/fno\/setup-strikes\?signal=action%3A12&spot=24480$/);
    await expect(page.locator("tr[data-strike='24400'] td[data-setup='put']").first()).toBeVisible();
    await expect(page.locator("tr[data-strike='24200'] [data-setup-mark]")).toContainText("Wing put");
    await expect(page.locator("td[data-setup='call']")).toHaveCount(0);
    expect(page.url()).toContain("signal=action%3A12");
    await panel.locator("[data-setup='clear']").tap();
    await expect(page.locator("[data-setup-panel]")).toHaveCount(0);
    await expect(page.locator("[data-setup-mark]")).toHaveCount(0);
    expect(page.url()).not.toContain("signal=");
    expect(orders(seen)).toEqual([]);                                   // marks only: no order, no basket
  });

  test("without a signal the chain reads no setup and marks nothing", async ({ page }) => {
    const seen = await open(page, "light");
    await expect(page.locator("[data-setup-panel]")).toHaveCount(0);
    await expect(page.locator("[data-setup-mark]")).toHaveCount(0);
    expect(seen.gets.filter((g) => g.includes("/setup-strikes"))).toEqual([]);
  });

  test("the confirm step: plain words, a risk line that warns but never blocks, Explain, the PAPER frame", async ({ page }) => {
    const seen = await open(page, "dark");
    await callCell(page).tap();
    await page.locator("[data-chain-actions] [data-action='buy']").tap();
    const ticket = page.locator("[data-order-ticket]");
    await expect(ticket.locator("[data-practice-frame]")).toContainText("PAPER");
    expect(seen.gets.filter((g) => g.endsWith("/risk-budget"))).toEqual([]);         // read only on the confirm step
    // M68 PR-1: no order type is pre-selected either - pick Market where none is chosen yet
    if (await ticket.locator("[data-ticket='lots']").inputValue() === "") await ticket.locator("[data-ticket='lots']").fill("1");   // M68 PR-2: lots asked first
    if (await ticket.locator("[data-ticket='order-type'] [aria-checked='true']").count() === 0) await ticket.locator("[data-ticket='order-type'] [data-option='MARKET']").tap();
    await ticket.locator("[data-ticket='product'] [data-option='NRML']").tap();      // F1: no product saved - pick one before Review
    await ticket.locator("[data-ticket='review']").tap();
    await expect(ticket.locator("[data-ticket='plain-words']")).toContainText(
      "If NIFTY closes at or below 24,500 at expiry you lose the premium, at most about ₹11,300; above 24,650 you gain, more the higher it closes.");
    // Owner 29 Oct: the max-loss line is a warning in the one "Before you place" box; its source note is in its Details.
    const line = ticket.locator("[data-risk-checks='ticket'] [data-risk-part='warn']").first();
    await expect(line).toContainText("is 2.3% of your capital");
    await expect(line).toContainText("That is more than your risk per trade of ₹5,000 (1%).");
    await expect(ticket.locator("[data-risk-checks='ticket']")).toContainText("It does not hold back the order or any exit.");
    await expect(ticket.locator("[data-ticket='place']")).toBeEnabled();
    await ticket.locator("[data-explain='ticket'] summary").tap();
    await expect(ticket.locator("[data-explain-structure='long_call']")).toContainText("Long call");
    await ticket.locator("[data-ticket='place']").tap();
    await expect(ticket.locator("[data-ticket='result']")).toContainText("Placed in practice mode");
    expect(seen.posts.filter((p) => p.url.endsWith("/paper/fno/order"))).toHaveLength(1);
  });

  test("My positions: the PAPER frame while every position shown is PAPER, and Exit now stays enabled", async ({ page }) => {
    await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
    await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
    await mockApi(page, { features: T.features as Record<string, boolean>, brokers: T.token_status as unknown[] });
    await mockTrading(page);
    const paperOnly = (T.positions as { mode: string }[]).filter((p) => p.mode === "PAPER");
    await page.route(/\/api(?:\/v1)?\/positions$/, (r) => r.fulfill({ json: paperOnly }));
    await page.goto("/positions");
    const frame = page.locator("[data-page='positions-v2'] [data-practice-frame]");
    await expect(frame).toHaveText("PAPER");                                         // UI-28 U1.1: one chip
    await expect(frame).toHaveAttribute("title", "Practice mode: no real order is sent and no money moves.");
    const exits = page.getByRole("button", { name: /Exit now/ });
    await expect(exits.first()).toBeEnabled();
  });
});

import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
/** UX-11 (owner, 27 Oct): numbers first - every card passes the text budget; the visible-word count is printed. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`fno-insights text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every card fits the text budget", async ({ page }) => {
      await open(page, "light");
      console.log(`[words] fno-insights ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      expect(await textBudgetProblems(page)).toEqual([]);
    });
  });
}
