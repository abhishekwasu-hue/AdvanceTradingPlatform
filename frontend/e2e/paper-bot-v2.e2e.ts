import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * UX2 fix 4 ("Paper bot in three taps", docs/design/UX_AUDIT_FULL.md P18): the v2 Paper bot page at /deployments with
 * `ui_v2` on. Your bots as cards (Pause / Resume / Stop with a confirm), "Start a paper bot" (1 strategy, 2 symbol,
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
    pause_reason: "stopped from the Paper bot page", last_evaluated_at: null, open_positions: 0, instrument_kind: "OPTION",
    option_position: "BUY", expiry_rule: "NEAREST", strike_rule: "ATM", max_lots: 1, holding: "INTRADAY" },
];

interface Server { bots: typeof BOTS; sent: { method: string; url: string; body: unknown }[] }

async function open(page: Page, o: { theme?: "light" | "dark"; empty?: boolean; noBroker?: boolean } = {}): Promise<Server> {
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
  test.describe(`Paper bot ${size.name}`, () => {
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

test.describe("Paper bot behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("your bots: running first, a readable pause reason, stopped bots on request; one name, no developer words", async ({ page }) => {
    await open(page);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Paper bot");
    const rows = bots(page).locator("[data-bot]");
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText("Opening Range Breakout");
    await expect(rows.nth(0)).toContainText("NIFTY 50 options · during the day");
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
    await expect(bots(page)).toContainText("No paper bot yet.");
    await page.getByRole("button", { name: "Start a paper bot" }).click();
    await expect(form(page).getByLabel("Pick a strategy")).toBeFocused();
    await expect(form(page).getByLabel("Pick a strategy")).toHaveValue("orb_15m_5m");
    await expect(form(page)).toContainText("Waits for the first 15 minutes' range to break");
    await form(page).getByLabel("Pick a symbol").selectOption("NIFTY BANK");
    await expect(form(page)).toContainText("Trades a NIFTY BANK option at the nearest expiry, 1 lot");
    await form(page).getByRole("button", { name: "Start on paper" }).click();
    await expect(form(page)).toContainText("Started on paper: NIFTY BANK.");
    const post = server.sent.find((c) => c.method === "POST" && c.url === "/deployments");
    expect(post?.body).toEqual({ strategy_id: "orb_15m_5m", symbol: "NIFTY BANK", exchange: "NSE", timeframe: "1min", mode: "PAPER", holding: "INTRADAY",
      instrument_kind: "OPTION", option_position: "BUY", expiry_rule: "NEAREST", strike_rule: "ATM", strike_offset: 0, max_lots: 1 });
    await expect(bots(page).locator("[data-bot]")).toHaveCount(1);

    await form(page).getByLabel("Pick a strategy").selectOption("swing_ema_pullback_d");
    await expect(form(page).getByLabel("Pick a symbol")).toHaveValue("RELIANCE");
    await form(page).getByLabel("Pick a symbol").selectOption("__other__");
    await form(page).getByRole("button", { name: "Start on paper" }).click();
    await expect(form(page)).toContainText("Pick or type a symbol.");
    await form(page).getByLabel("Type the symbol").fill("tcs");
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

  test("More ways to trade: the full form, paper only - no PAPER / LIVE switch, no worker tiles, no second list", async ({ page }) => {
    const server = await open(page);
    await page.getByText("More ways to trade (options, futures, exits, filters)").click();
    const more = page.locator("[data-more-ways]");
    await expect(more.getByText("More ways to trade", { exact: true })).toBeVisible();
    await expect(more.getByRole("button", { name: "LIVE", exact: true })).toHaveCount(0);
    await expect(more.getByText("Trading worker")).toHaveCount(0);
    await expect(more.getByText(/Deployments \(/)).toHaveCount(0);
    await more.getByRole("button", { name: "Start on paper" }).click();
    await expect.poll(() => server.sent.filter((c) => c.method === "POST").length).toBe(1);
    expect((server.sent[0].body as { mode: string }).mode).toBe("PAPER");
  });

  test("without a broker: says to connect one first", async ({ page }) => {
    await open(page, { noBroker: true });
    await expect(form(page)).toContainText("A paper bot reads live prices from your broker.");
    await expect(form(page).getByRole("link", { name: "Connect your broker" })).toHaveAttribute("href", "/broker");
  });
});
