import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Settings redesign part C: "My trading defaults" at /settings/trading-defaults (ui_v2 on) and the pre-fill it gives the
 * Paper bot's "Start on paper" and its "More ways to trade" form. A pre-fill, never a hidden rule: every value taken is
 * shown, can be switched off or changed, and empty defaults leave the request byte-identical.
 * - Baselines (NEW): `trading-defaults-v2-{1440,390,360}-{light,dark}.png` (the page with some defaults) and
 *   `paper-bot-defaults-v2-{1440,390}-{light,dark}.png` (the Start card with defaults). Other baselines are untouched.
 * Every reply is made up (e2e/fixtures/setup_v2.json strategies, the defaults below): no real account or id.
 * After an intended change: `npx playwright test --project=phone e2e/trading-defaults-v2.e2e.ts --update-snapshots`.
 */

const S = JSON.parse(readFileSync(new URL("./fixtures/setup_v2.json", import.meta.url), "utf8")) as Record<string, unknown>;
const NOW = "2026-10-21T10:00:00+05:30";
const EMPTY = { instrument_kind: null, option_position: null, expiry_rule: null, strike_rule: null, strike_offset: null, premium_stop_pct: null,
  max_lots: null, option_strategy: null, spread_width: null, target_credit_pct: null, stop_credit_pct: null, entry_mode: null, exit_rules: null, ticket: null };
const SOME = { ...EMPTY, instrument_kind: "OPTION", option_strategy: "SINGLE", option_position: "BUY", expiry_rule: "NEXT", strike_rule: "OTM", strike_offset: 1,
  max_lots: 2, exit_rules: { trailing_stop_pct: 1.5, break_even_at_r: null, time_exit_minutes: null, time_exit_at: null },
  ticket: { lots: 2, product: "NRML", order_type: "LIMIT" } };
const TODAY_BODY = { strategy_id: "orb_15m_5m", symbol: "NIFTY BANK", exchange: "NSE", timeframe: "1min", mode: "PAPER", holding: "INTRADAY",
  instrument_kind: "OPTION", option_position: "BUY", expiry_rule: "NEAREST", strike_rule: "ATM", strike_offset: 0, max_lots: 1 };

interface Server { prefs: unknown | null; version: number; sent: { method: string; url: string; raw: string | null }[] }

async function open(page: Page, path: string, o: { theme?: "light" | "dark"; prefs?: unknown | null; features?: Record<string, boolean> } = {}): Promise<Server> {
  const server: Server = { prefs: o.prefs === undefined ? null : o.prefs, version: o.prefs ? 3 : 0, sent: [] };
  await page.clock.install({ time: new Date(NOW) });
  await page.addInitScript((theme) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, o.theme ?? "light");
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ...(F.features as Record<string, boolean>), ...(o.features ?? {}) }, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { me: true });
  await page.route(api("/me/prefs(/trading_defaults)?(\\?.*)?$"), (route) => {
    const r = route.request();
    if (r.method() === "GET") {
      // prefs null: the namespace was never saved (the server's empty defaults, version 0)
      return route.fulfill({ json: { prefs: { trading_defaults: server.prefs ?? EMPTY }, versions: { trading_defaults: server.version } } });
    }
    server.sent.push({ method: r.method(), url: r.url(), raw: r.postData() });
    server.prefs = r.postDataJSON();
    server.version += 1;
    return route.fulfill({ json: { namespace: "trading_defaults", value: server.prefs, version: server.version } });
  });
  await page.route(api("/strategies$"), (route) => route.fulfill({ json: S.strategies }));
  await page.route(api("/custom-strategies$"), (route) => route.fulfill({ json: [] }));
  await page.route(api("/broker/credentials$"), (route) => route.fulfill({ json: [{ broker_name: "upstox", account_label: "primary" }] }));
  await page.route(api("/broker/accounts$"), (route) => route.fulfill({ json: [] }));
  await page.route(api("/worker/status$"), (route) => route.fulfill({ json: { running: true, market_open: true, market_status: "Open" } }));
  await page.route(api("/deployments(\\?.*)?$"), (route) => {
    const r = route.request();
    if (r.method() !== "POST") return route.fulfill({ json: [] });
    server.sent.push({ method: "POST", url: "/deployments", raw: r.postData() });
    return route.fulfill({ json: { ...JSON.parse(r.postData() ?? "{}"), id: 61, status: "ACTIVE", open_positions: 0, last_evaluated_at: null } });
  });
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  return server;
}

const SHOT = { animations: "disabled" as const, fullPage: true, maxDiffPixelRatio: 0.02, timeout: 15_000 };
const startCard = (page: Page) => page.locator("[data-block='start']");
const posts = (s: Server) => s.sent.filter((c) => c.url === "/deployments");

for (const size of [{ name: "1440", width: 1440, height: 900, mobile: false }, { name: "390", width: 390, height: 844, mobile: true },
  { name: "360", width: 360, height: 780, mobile: true }]) {
  test.describe(`My trading defaults ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as const) {
      test(`the page looks the same (${theme})`, async ({ page }) => {
        await open(page, "/settings/trading-defaults", { theme, prefs: SOME });
        await expect(page.getByRole("heading", { level: 1 })).toHaveText("My trading defaults");
        await expect(page).toHaveScreenshot(`trading-defaults-v2-${size.name}-${theme}.png`, SHOT);
      });

      if (size.name !== "360") {
        test(`Start on paper with defaults looks the same (${theme})`, async ({ page }) => {
          await open(page, "/deployments", { theme, prefs: SOME });
          await expect(startCard(page).locator("[data-defaults]")).toBeVisible();
          await expect(startCard(page)).toHaveScreenshot(`paper-bot-defaults-v2-${size.name}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
        });
      }
    }

    if (size.mobile) {
      test("no sideways scroll, 44 px targets, nothing hidden behind the bottom bar", async ({ page }) => {
        await open(page, "/settings/trading-defaults", { prefs: SOME });
        await expectPhoneLayout(page);
      });
    }
  });
}

test.describe("My trading defaults behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("empty on the first visit; saving sends only the reviewed fields - never a mode, routing or LIVE field", async ({ page }) => {
    const server = await open(page, "/settings/trading-defaults");
    const main = page.locator("main");
    await expect(main.getByLabel("Trade as")).toHaveValue("");
    await expect(main.getByLabel("Time exit after (minutes)")).toHaveValue("");
    await expect(main.getByLabel("Flat at (HH:MM)")).toHaveValue("");
    await expect(main.getByRole("link", { name: "Open Risk limits" })).toHaveAttribute("href", "/risk-management");
    await expect(main.getByText("keeps asking for my Approve")).toHaveCount(0);       // the approval-only flag is off
    for (const word of [/\bLIVE\b/, /\bMode\b/, /Routing/, /deployment/i, /namespace/i]) await expect(main.getByText(word)).toHaveCount(0);

    await main.getByLabel("Trade as").selectOption("OPTION");
    await main.getByLabel("Expiry").selectOption("NEXT");
    await main.getByLabel("Lots for a new bot").fill("0");
    await main.getByRole("button", { name: "Save my defaults" }).click();
    await expect(main.locator("[data-note='defaults-note']")).toHaveText("Lots for a new bot: a whole number from 1 to 500.");
    expect(server.sent).toEqual([]);
    await main.getByLabel("Lots for a new bot").fill("2");
    await main.getByLabel("Trailing stop %").fill("1.5");
    await main.getByRole("button", { name: "Save my defaults" }).click();
    await expect(main.locator("[data-note='defaults-note']")).toContainText("Saved.");
    expect(server.sent).toHaveLength(1);
    expect(server.sent[0].url).toContain("/me/prefs/trading_defaults?version=0");
    const body = JSON.parse(server.sent[0].raw ?? "{}") as Record<string, unknown>;
    expect(body).toEqual({ ...EMPTY, instrument_kind: "OPTION", expiry_rule: "NEXT", max_lots: 2,
      exit_rules: { trailing_stop_pct: 1.5, break_even_at_r: null, time_exit_minutes: null, time_exit_at: null } });
    for (const k of ["mode", "broker_name", "routing_policy", "order_style", "holding"]) expect(body).not.toHaveProperty(k);

    await main.getByRole("button", { name: "Clear all" }).click();
    await expect(main.locator("[data-note='defaults-note']")).toContainText("Your defaults are cleared.");
    expect(JSON.parse(server.sent[1].raw ?? "{}")).toEqual(EMPTY);
  });

  test("the entry choice shows only while approval-only entries is on", async ({ page }) => {
    await open(page, "/settings/trading-defaults", { features: { approval_only_entries: true, paper_account: true } });
    await expect(page.locator("main").getByText("A new bot keeps asking for my Approve before each entry")).toBeVisible();
  });

  test("identity: with empty defaults Start on paper sends today's body, byte for byte", async ({ page }) => {
    const server = await open(page, "/deployments", { prefs: EMPTY });
    await expect(startCard(page).locator("[data-defaults]")).toHaveCount(0);
    await startCard(page).getByLabel("Pick a symbol").selectOption("NIFTY BANK");
    await expect(startCard(page)).toContainText("Trades a NIFTY BANK option at the nearest expiry, 1 lot");
    await startCard(page).getByRole("button", { name: "Start on paper" }).click();
    await expect.poll(() => posts(server).length).toBe(1);
    expect(posts(server)[0].raw).toBe(JSON.stringify(TODAY_BODY));
  });

  test("pre-fill: Start on paper takes the defaults, says so, and can start without them", async ({ page }) => {
    const server = await open(page, "/deployments", { prefs: SOME });
    const card = startCard(page);
    await card.getByLabel("Pick a symbol").selectOption("NIFTY BANK");
    await expect(card.locator("[data-defaults-line]")).toContainText("From your defaults: strike 1 step out of the money, next expiry, 2 lots, trailing stop 1.5%.");
    await expect(card).toContainText("Trades a NIFTY BANK option at the next expiry, 2 lots");
    await expect(card.getByRole("link", { name: "Change my defaults" })).toHaveAttribute("href", "/settings/trading-defaults");
    await card.getByRole("button", { name: "Start on paper" }).click();
    await expect.poll(() => posts(server).length).toBe(1);
    expect(JSON.parse(posts(server)[0].raw ?? "{}")).toEqual({ ...TODAY_BODY, expiry_rule: "NEXT", max_lots: 2, strike_rule: "OTM", strike_offset: 1,
      exit_rules: { trailing_stop_pct: 1.5 } });

    await card.getByLabel("Use my defaults for this bot").uncheck();
    await expect(card).toContainText("Starts with the usual settings instead.");
    await card.getByRole("button", { name: "Start on paper" }).click();
    await expect.poll(() => posts(server).length).toBe(2);
    expect(posts(server)[1].raw).toBe(JSON.stringify(TODAY_BODY));
  });

  test("pre-fill: More ways to trade marks each value taken, and a changed field loses its mark", async ({ page }) => {
    const server = await open(page, "/deployments", { prefs: SOME });
    await page.getByText("More ways to trade (options, futures, exits, filters)").click();
    const more = page.locator("[data-more-ways]");
    await expect(more.locator("[data-from-defaults]").first()).toBeVisible();
    for (const label of ["Expiry", "Max lots", "Trailing stop %", "Strike"]) {
      await expect(more.locator("label", { hasText: label }).first().locator("[data-from-defaults]")).toHaveText("from your defaults");
    }
    const lots = more.locator("label", { hasText: "Max lots" }).locator("xpath=..").locator("input");
    await expect(lots).toHaveValue("2");
    await lots.fill("3");
    await expect(more.locator("label", { hasText: "Max lots" }).locator("[data-from-defaults]")).toHaveCount(0);
    await more.getByRole("button", { name: "Start on paper" }).click();
    await expect.poll(() => posts(server).length).toBe(1);
    const body = JSON.parse(posts(server)[0].raw ?? "{}") as Record<string, unknown>;
    expect(body).toMatchObject({ mode: "PAPER", instrument_kind: "OPTION", expiry_rule: "NEXT", strike_rule: "OTM", strike_offset: 1, max_lots: 3,
      exit_rules: { trailing_stop_pct: 1.5 } });
    expect(body).not.toHaveProperty("entry_mode");
  });
});
