import { expect, test, type Page, type Route } from "@playwright/test";
import { choose, expectChoice, pickEntryStyle } from "./combobox";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * UX2 fix 4 ("Trading bot in three taps", docs/design/UX_AUDIT_FULL.md P18): the v2 Trading bot page at /deployments with
 * `ui_v2` on. Your bots as cards (Pause / Resume / Stop with a confirm), "Start a trading bot" (1 strategy, 2 symbol,
 * 3 Start on paper: always PAPER, one lot), the old form under "More ways to trade" without the PAPER / LIVE switch.
 * - Baselines `paper-bot-v2-{1440,390}-{light,dark}.png`.
 * Every reply is made-up data (e2e/fixtures/setup_v2.json strategies, the bots below): no real account or id.
 * After an intended change: `npx playwright test --project=phone e2e/paper-bot-v2.e2e.ts --update-snapshots`.
 */

const S = JSON.parse(readFileSync(new URL("./fixtures/setup_v2.json", import.meta.url), "utf8")) as Record<string, unknown>;
const NOW = "2026-10-21T10:00:00+05:30";

const base = {
  exchange: "NSE", broker_name: null, last_signal_at: null, last_error: null, consecutive_failures: 0, created_by: 7,
  created_at: "2026-10-12T04:00:00Z", updated_at: "2026-10-19T04:51:00Z", strike_offset: 0, premium_stop_pct: null, contract_rules: "",
};
const BOTS = [
  { ...base, id: 52, strategy_id: "orb_15m_5m", symbol: "NIFTY 50", timeframe: "1min", mode: "PAPER", status: "ACTIVE", pause_reason: null,
    last_evaluated_at: "2026-10-21T04:28:00Z", open_positions: 1, instrument_kind: "OPTION", option_position: "BUY", expiry_rule: "NEAREST",
    strike_rule: "ATM", max_lots: 1, holding: "INTRADAY" },
  { ...base, id: 51, strategy_id: "swing_ema_pullback_d", symbol: "RELIANCE", timeframe: "day", mode: "PAPER", status: "PAUSED",
    pause_reason: "3 losses in a row", last_evaluated_at: "2026-10-20T10:00:00Z", open_positions: 0, instrument_kind: "UNDERLYING",
    option_position: null, expiry_rule: null, strike_rule: null, max_lots: null, holding: "SWING" },
  { ...base, id: 40, strategy_id: "vwap_supertrend_5m", symbol: "NIFTY BANK", timeframe: "1min", mode: "PAPER", status: "STOPPED",
    pause_reason: "stopped from the Trading bot page", last_evaluated_at: null, open_positions: 0, instrument_kind: "OPTION",
    option_position: "BUY", expiry_rule: "NEAREST", strike_rule: "ATM", max_lots: 1, holding: "INTRADAY" },
];

interface Server { bots: typeof BOTS; sent: { method: string; url: string; body: unknown }[] }

async function open(page: Page, o: { theme?: "light" | "dark"; empty?: boolean; noBroker?: boolean; failDelete?: boolean; refuseStart?: string } = {}): Promise<Server> {
  const server: Server = { bots: o.empty ? [] : structuredClone(BOTS), sent: [] };
  await page.clock.install({ time: new Date(NOW) });
  await page.addInitScript((theme) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, o.theme ?? "light");
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: F.features as Record<string, boolean>, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { me: true });
  const sent = (route: Route) => {
    const r = route.request();
    let body: unknown = null;
    try { body = r.postDataJSON(); } catch { body = r.postData(); }
    server.sent.push({ method: r.method(), url: r.url().replace(/^https?:\/\/[^/]+\/api(?:\/v1)?/, ""), body });
    return body;
  };
  await page.route(api("/strategies$"), (route) => route.fulfill({ json: S.strategies }));
  await page.route(api("/custom-strategies$"), (route) => route.fulfill({ json: [] }));
  await page.route(api("/broker/credentials$"), (route) => route.fulfill({ json: o.noBroker ? [] : [{ broker_name: "upstox", account_label: "primary" }] }));
  await page.route(api("/broker/accounts$"), (route) => route.fulfill({ json: [] }));
  await page.route(api("/deployments(\\?.*)?$"), (route) => {
    if (route.request().method() !== "POST") {
      const all = new URL(route.request().url()).searchParams.get("include_stopped") === "true";
      return route.fulfill({ json: server.bots.filter((b) => all || b.status !== "STOPPED") });
    }
    const body = sent(route) as Record<string, unknown>;
    // REV-H1: the server's 409 names its reason (detail.code + a plain message).
    if (o.refuseStart) return route.fulfill({ status: 409, json: { detail: { code: o.refuseStart, message: "Server sentence." } } });
    const bot = { ...base, ...body, id: 60 + server.bots.length, status: "ACTIVE", pause_reason: null, last_evaluated_at: null, open_positions: 0 };
    server.bots.unshift(bot as (typeof BOTS)[number]);
    return route.fulfill({ json: bot });
  });
  await page.route(api("/deployments/\\d+/(pause|resume|stop)$"), (route) => {
    const body = sent(route) as { reason?: string } | null;
    const [, id, action] = /\/deployments\/(\d+)\/(\w+)$/.exec(route.request().url())!;
    const bot = server.bots.find((b) => b.id === Number(id))!;
    bot.status = action === "pause" ? "PAUSED" : action === "resume" ? "ACTIVE" : "STOPPED";
    bot.pause_reason = action === "resume" ? null : body?.reason ?? null;
    return route.fulfill({ json: bot });
  });
  // REV-H2: the existing DELETE removes a stopped bot (the server refuses one that is not stopped).
  await page.route(api("/deployments/\\d+$"), (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    sent(route);
    const id = Number(/\/deployments\/(\d+)$/.exec(route.request().url())![1]);
    const bot = server.bots.find((b) => b.id === id);
    if (o.failDelete || !bot || bot.status !== "STOPPED") return route.fulfill({ status: 409, json: { detail: "Stop the bot before removing it." } });
    server.bots = server.bots.filter((b) => b.id !== id);
    return route.fulfill({ status: 204, body: "" });
  });
  await page.route(api("/worker/status$"), (route) => route.fulfill({ json: { running: true, market_open: true, market_status: "Open" } }));
  await page.goto("/deployments");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
  return server;
}

const bots = (page: Page) => page.locator("[data-block='bots']");
const form = (page: Page) => page.locator("[data-block='start']");
const SHOT = { animations: "disabled" as const, fullPage: true, maxDiffPixelRatio: 0.02, timeout: 15_000 };

for (const size of [{ name: "1440", width: 1440, height: 900, mobile: false }, { name: "390", width: 390, height: 844, mobile: true }]) {
  test.describe(`Trading bot ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as const) {
      test(`looks the same (${theme})`, async ({ page }) => {
        await open(page, { theme });
        await expect(bots(page)).toContainText("Opening Range Breakout");
        await expect(page).toHaveScreenshot(`paper-bot-v2-${size.name}-${theme}.png`, SHOT);
      });
    }

    if (size.mobile) {
      test("no sideways scroll, 44 px targets, nothing hidden behind the bottom bar", async ({ page }) => {
        await open(page);
        await expectPhoneLayout(page);
      });
    }
  });
}

test.describe("Trading bot behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("your bots: running first, a readable pause reason, stopped bots on request; one name, no developer words", async ({ page }) => {
    await open(page);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Trading bot");
    const rows = bots(page).locator("[data-bot]");
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText("Opening Range Breakout");
    await expect(rows.nth(0).locator("[data-bot-what]")).toHaveText("NIFTY 50 optionsDuring the day");   // UX-11b C5: two chips
    await expect(rows.nth(0)).toContainText("Running");
    await expect(rows.nth(0)).toContainText("1 open trade");
    await expect(rows.nth(1)).toContainText("Paused: 3 losses in a row");
    await expect(rows.nth(1).getByRole("button", { name: "Resume" })).toBeVisible();
    await bots(page).getByRole("button", { name: "Show stopped bots (1)" }).click();
    await expect(rows).toHaveCount(3);
    const body = await page.locator("main").innerText();
    for (const word of ["Autopilot", "deployment", "worker", "Base candles", "instrument master", "LIVE", "Not found"]) expect(body).not.toContain(word);
  });

  test("start in three taps: strategy, symbol, Start on paper - always PAPER, one lot for an index", async ({ page }) => {
    const server = await open(page, { empty: true });
    await expect(bots(page)).toContainText("Start a trading bot below");
    await page.getByRole("button", { name: "Start a trading bot" }).click();
    await expect(form(page).getByLabel("Pick a strategy")).toBeFocused();
    await expectChoice(form(page).getByLabel("Pick a strategy"), "orb_15m_5m");
    await expect(form(page)).toContainText("Waits for the first 15 minutes' range to break");
    await choose(page, form(page).getByLabel("Pick a symbol"), "NIFTY BANK");
    await expect(form(page)).toContainText("Trades a NIFTY BANK option at the nearest expiry, 1 lot");
    await pickEntryStyle(page, form(page));
    await form(page).getByRole("button", { name: "Start on paper" }).click();
    await expect(form(page)).toContainText("Started on paper: NIFTY BANK.");
    const post = server.sent.find((c) => c.method === "POST" && c.url === "/deployments");
    expect(post?.body).toEqual({ strategy_id: "orb_15m_5m", symbol: "NIFTY BANK", exchange: "NSE", timeframe: "1min", mode: "PAPER", holding: "INTRADAY",
      instrument_kind: "OPTION", option_position: "BUY", expiry_rule: "NEAREST", strike_rule: "ATM", strike_offset: 0, max_lots: 1,
      order_style: "MARKET" });                                                         // M68: the entry style he picked
    await expect(bots(page).locator("[data-bot]")).toHaveCount(1);

    await choose(page, form(page).getByLabel("Pick a strategy"), "swing_ema_pullback_d");
    await expectChoice(form(page).getByLabel("Pick a symbol"), "RELIANCE");
    // CT-1 C6: the one SymbolPicker - an emptied field asks for a symbol; a typed one is kept when the field is left.
    await form(page).getByLabel("Pick a symbol").fill("");
    await form(page).getByLabel("Pick a symbol").press("Tab");                     // leaving the field keeps it empty
    await pickEntryStyle(page, form(page));
    await form(page).getByRole("button", { name: "Start on paper" }).click();
    await expect(form(page)).toContainText("Pick or type a symbol.");
    await form(page).getByLabel("Pick a symbol").fill("tcs");
    await form(page).getByLabel("Pick a symbol").press("Tab");
    await pickEntryStyle(page, form(page));
    await form(page).getByRole("button", { name: "Start on paper" }).click();
    await expect(form(page)).toContainText("Started on paper: TCS.");
    expect(server.sent.filter((c) => c.url === "/deployments").at(-1)?.body).toMatchObject({ symbol: "TCS", timeframe: "day", holding: "SWING", instrument_kind: "UNDERLYING", mode: "PAPER" });
  });

  test("pause, resume, and stop after a confirm", async ({ page }) => {
    const server = await open(page);
    const first = bots(page).locator("[data-bot='52']");
    await first.getByRole("button", { name: "Pause" }).click();
    await expect(bots(page)).toContainText("is paused. It takes no new trades until you resume it.");
    await expect(first).toContainText("Paused by you");
    await first.getByRole("button", { name: "Resume" }).click();
    await expect(first).toContainText("Running");
    await first.getByRole("button", { name: "Stop" }).click();
    await expect(first).toContainText("It takes no new trades. An open paper trade is still watched until it closes.");
    await first.getByRole("button", { name: "Keep it running" }).click();
    expect(server.sent.some((c) => c.url.endsWith("/stop"))).toBe(false);
    await first.getByRole("button", { name: "Stop" }).click();
    await first.getByRole("button", { name: "Yes, stop it" }).click();
    await expect(bots(page)).toContainText("is stopped.");
    expect(server.sent.map((c) => c.url)).toEqual(["/deployments/52/pause", "/deployments/52/resume", "/deployments/52/stop"]);
  });

  test("REV-H2: Remove on a stopped bot only, after a confirm; a running or paused bot has none", async ({ page }) => {
    const server = await open(page);
    await expect(bots(page).getByRole("button", { name: "Remove" })).toHaveCount(0);       // running and paused: no Remove
    await bots(page).getByRole("button", { name: /Show stopped bots/ }).click();
    const stopped = bots(page).locator("[data-bot='40']");
    await expect(bots(page).getByRole("button", { name: "Remove" })).toHaveCount(1);
    await stopped.getByRole("button", { name: "Remove" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("It leaves your list of bots.");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    expect(server.sent.some((c) => c.method === "DELETE")).toBe(false);
    await stopped.getByRole("button", { name: "Remove" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Remove bot" }).click();
    await expect(bots(page)).toContainText("is removed from your bots.");
    await expect(bots(page).locator("[data-bot='40']")).toHaveCount(0);
    expect(server.sent.filter((c) => c.method === "DELETE").map((c) => c.url)).toEqual(["/deployments/40"]);
  });

  test("REV-H2: a failed Remove says so and keeps the bot", async ({ page }) => {
    await open(page, { failDelete: true });
    await bots(page).getByRole("button", { name: /Show stopped bots/ }).click();
    await bots(page).locator("[data-bot='40']").getByRole("button", { name: "Remove" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Remove bot" }).click();
    await expect(bots(page)).toContainText("was not removed. Stop the bot before removing it.");
    await expect(bots(page).locator("[data-bot='40']")).toHaveCount(1);
  });

  test("REV-H1: a refused start shows the server's reason, not 'already runs' for every 409", async ({ page }) => {
    await open(page, { refuseStart: "no_instrument_master" });
    await choose(page, form(page).getByLabel("Pick a symbol"), "NIFTY BANK");
    await pickEntryStyle(page, form(page));
    await form(page).getByRole("button", { name: "Start on paper" }).click();
    await expect(form(page)).toContainText("The contracts for this symbol are not loaded yet");
    await expect(form(page)).not.toContainText("already runs");
  });

  test("More ways to trade: the full form, paper only - no PAPER / LIVE switch, no worker tiles, no second list", async ({ page }) => {
    const server = await open(page);
    await page.getByText("More ways to trade (options, futures, exits, filters)").click();
    const more = page.locator("[data-more-ways]");
    await expect(more.getByText("More ways to trade", { exact: true })).toBeVisible();
    await expect(more.getByRole("button", { name: "LIVE", exact: true })).toHaveCount(0);
    await expect(more.getByText("Trading worker")).toHaveCount(0);
    await expect(more.getByText(/Deployments \(/)).toHaveCount(0);
    await pickEntryStyle(page, more);
    await more.getByRole("button", { name: "Start on paper" }).click();
    await expect.poll(() => server.sent.filter((c) => c.method === "POST").length).toBe(1);
    expect((server.sent[0].body as { mode: string }).mode).toBe("PAPER");
  });

  test("without a broker: says to connect one first", async ({ page }) => {
    await open(page, { noBroker: true });
    await expect(form(page)).toContainText("A trading bot reads live prices from your broker.");
    await expect(form(page).getByRole("link", { name: "Connect your broker" })).toHaveAttribute("href", "/broker");
  });
});

/** UX-11 PR 5 (owner, 27 Oct): numbers first - every Trading bot card passes the text budget; the visible-word count is printed. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`v2 Trading bot text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every card fits the text budget", async ({ page }) => {
      await open(page, { theme: "light" });
      console.log(`[words] Trading bot ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      expect(await textBudgetProblems(page)).toEqual([]);
    });
  });
}
