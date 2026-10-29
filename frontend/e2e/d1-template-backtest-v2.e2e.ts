import { expect, test, type Page, type Route } from "@playwright/test";
import { choose, expectChoice } from "./combobox";
import { readFileSync } from "node:fs";
import { F, api } from "./dashboardMocks";
import { mockApi } from "./mockApi";

/**
 * Day-one fix 1 (docs/design/DAY_ONE_TRADER_TEST.md finding 1): the idea's own strategy can be backtested. Ideas ›
 * Breakout and retest › "Test it on past prices" opens Strategies › Test with the idea's template (A5) and its settings
 * picked. The next morning before his broker login (token expired) only stored Daily history exists, so the test runs
 * on Daily and the page says so in plain words; the run sends the template's own strategy id and `lake:daily`.
 * Flags: ui_v2 + strategy_templates + idea_library, and `backtest_stored_history` ONLY in this mocked feature list.
 * Every reply is made up (the bars are a generated walk). With D1F_SHOTS=<folder> each step is saved as
 * `<step>-<width>-<theme>.png`; no baseline is stored.
 */

const FEATURES: Record<string, boolean> = { ui_v2: true, strategy_templates: true, idea_library: true, backtest_stored_history: true };
const SHOTS = process.env.D1F_SHOTS;
const S = JSON.parse(readFileSync(new URL("./fixtures/setup_v2.json", import.meta.url), "utf8")) as Record<string, unknown>;
const IDEAS = JSON.parse(readFileSync(new URL("./fixtures/ideas_v2.json", import.meta.url), "utf8")) as { ideas: { id: string }[] };
const STORED_LINE = "Prices from ATP's stored end-of-day history (NSE's daily files), 23 Oct 2025 to 22 Oct 2026.";
const TF = [{ value: "5m", label: "5-minute" }, { value: "15m", label: "15-minute" }, { value: "30m", label: "30-minute" },
  { value: "1h", label: "1-hour" }, { value: "1d", label: "Daily" }];
const TEMPLATES = [{ id: "A5", version: 1, name: "Breakout and retest", family: "A", family_name: "Price action",
  idea: "After a level breaks, wait for the market to come back to it from the other side and reject it.", difficulty: "Beginner", style: "Both",
  timeframes: TF, default_timeframe: "5m", sides_default: "both", params: [
    { key: "retest_of", label: "What broke", help: "", type: "enum", default: "swing", choices: [{ value: "swing", label: "A swing level" }] },
    { key: "range_mode", label: "The range", help: "", type: "enum", default: "auto", choices: [{ value: "fixed", label: "Fixed" }, { value: "auto", label: "Auto" }] },
    { key: "vol_mult", label: "Volume expansion", help: "", type: "float", default: 0, min: 0, max: 20, step: 0.1 },
    { key: "retest_max_bars", label: "Retest within (bars)", help: "", type: "int", default: 10, min: 1, max: 100, step: 1 }],
  rules: [], available: true }];
const LABEL: Record<string, string> = { "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "1d": "day" };
const TOKEN = [{ broker_name: "upstox", account_label: "primary", token_status: "EXPIRED", token_expires_at: null, last_verified_at: null, needs_login: true,
  oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const SOURCES = { sources: [{ broker: "upstox", account_label: "primary", token_status: "EXPIRED", usable: false }], usable: false,
  timeframes: ["1m", "5m", "15m", "day"], max_symbols: 50, intraday_max_lookback_days: 30, daily_max_lookback_days: 730 };

function walk(n: number) {
  let p = 100;
  const start = Date.UTC(2025, 9, 22, 18, 30);
  return Array.from({ length: n }, (_, i) => {
    const o = p;
    p = Math.max(20, p * (1 + Math.sin(i / 7) * 0.012 + ((i * 37) % 11 - 5) / 900));
    const c = Math.round(p * 100) / 100;
    return { timestamp: new Date(start + Math.floor(i * 7 / 5) * 86_400_000).toISOString(), open: Math.round(o * 100) / 100,
      high: Math.round(Math.max(o, c) * 101) / 100, low: Math.round(Math.min(o, c) * 99) / 100, close: c, volume: 100_000 + (i % 9) * 5_000 };
  });
}
const body = (route: Route): unknown => { try { return route.request().postDataJSON(); } catch { return route.request().postData(); } };

async function mockWorld(page: Page) {
  const posts: { url: string; body: unknown }[] = [];
  await mockApi(page, { features: FEATURES, brokers: TOKEN });
  const me = { ...(F.me as Record<string, unknown>), role: "USER", scopes: ["trading:write", "alerts:write"] };
  await page.route(api("/auth/me$"), (r) => r.fulfill({ json: me }));
  await page.route(api("/system/features$"), (r) => r.fulfill({ json: FEATURES }));
  await page.route(api("/broker/token-status$"), (r) => r.fulfill({ json: TOKEN }));
  await page.route(api("/market-data/sources$"), (r) => r.fulfill({ json: SOURCES }));
  await page.route(api("/ideas(/[A-Za-z0-9]+)?$"), (r) => r.fulfill({ json: IDEAS }));
  await page.route(api("/strategies$"), (r) => r.fulfill({ json: S.strategies }));
  await page.route(api("/backtests$"), (r) => r.fulfill({ json: [] }));
  await page.route(api("/strategy-templates$"), (r) => r.fulfill({ json: { templates: TEMPLATES } }));
  await page.route(api("/strategy-templates/\\w+/adopt$"), (route) => {
    const b = body(route) as { timeframe?: string; params?: Record<string, unknown> };
    const tf = b.timeframe ?? "5m";
    const sid = `template:A5:1:${tf}:both:swing,,fixed,,1.5,,10,`;
    posts.push({ url: "/strategy-templates/A5/adopt", body: b });
    return route.fulfill({ json: { template_id: "A5", name: "Breakout and retest", strategy_id: sid,
      deployment: { strategy_id: sid, timeframe: LABEL[tf], holding: tf === "1d" ? "SWING" : "INTRADAY", mode: "PAPER" },
      settings: { timeframe: tf, sides: "both", params: b.params ?? {} }, notes: [] } });
  });
  await page.route(api("/backtest/bars$"), (route) => {
    const b = body(route) as { symbol: string; timeframe: string };
    posts.push({ url: "/backtest/bars", body: b });
    const candles = walk(250);
    return route.fulfill({ json: { symbol: b.symbol, timeframe: b.timeframe, source: "lake:daily", kind: "stored",
      words: "ATP's stored end-of-day history (NSE's daily files)", from_day: "23 Oct 2025", to_day: "22 Oct 2026", bars: candles.length, line: STORED_LINE, candles } });
  });
  await page.route(api("/backtest$"), (route) => {
    const b = body(route) as { strategy_id: string; symbol: string; base_timeframe: string; data_source?: string };
    posts.push({ url: "/backtest", body: { strategy_id: b.strategy_id, base_timeframe: b.base_timeframe, data_source: b.data_source } });
    const bars = walk(250);
    const trade = (i: number, pnl: number) => ({ symbol: b.symbol, strategy_id: b.strategy_id, direction: "LONG", entry_time: bars[i].timestamp,
      entry_price: bars[i].close, quantity: 10, stop_loss: bars[i].low, target1: null, target2: null, exit_time: bars[i + 6].timestamp,
      exit_price: bars[i + 6].close, exit_reason: "SIGNAL", pnl, charges: 12 });
    const trades = [trade(40, 180), trade(95, -120), trade(150, 240)];
    return route.fulfill({ json: { ...(S.backtest as object), strategy_id: b.strategy_id, symbol: b.symbol, total_trades: 3, winning_trades: 2, losing_trades: 1,
      win_rate: 66.7, net_pnl: 300, gross_profit: 420, gross_loss: -120, profit_factor: 3.5, max_drawdown: 120, avg_win: 210, avg_loss: -120, expectancy: 100,
      trades, equity_curve: [0, 180, 60, 300], analytics: null, run_id: null } });
  });
  return posts;
}

async function settle(page: Page) {
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`Day-one fix 1: test the idea's strategy ${size.width} ${theme}`, () => {
      test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

      test("Ideas -> Test it on past prices -> the template on stored Daily history", async ({ page }) => {
        const shot = async (step: string) => {
          if (SHOTS) await page.screenshot({ path: `${SHOTS}/${step}-${size.width}-${theme}.png`, fullPage: true, animations: "disabled" });
        };
        await page.addInitScript((t) => {
          try {
            localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
            localStorage.setItem("atp_install_dismissed", "1");
          } catch { /* storage unavailable */ }
        }, theme);
        await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
        const posts = await mockWorld(page);

        await page.goto("/ideas");
        await settle(page);
        const idea = page.locator("[data-idea='T5']");
        await idea.locator("summary").click();
        await expect(idea.locator("[data-adopt='A5']")).toHaveText("Adopt as a paper strategy");
        await expect(idea.locator("[data-test-idea='A5']")).toHaveText("Test it on past prices");
        await idea.locator("[data-test-idea='A5']").scrollIntoViewIfNeeded();
        await shot("01-idea");

        await idea.locator("[data-test-idea='A5']").click();
        await expect(page).toHaveURL(/\/strategies\/test\?template=A5&timeframe=15m/);
        await settle(page);
        await expectChoice(page.getByRole("combobox").first(), "template-pick:A5");
        await expectChoice(page.getByLabel("Chart timeframe"), "1d");
        const note = page.locator("[data-template-test-note]");
        await expect(note).toContainText("Tests the idea's own strategy with the settings from the idea.");
        await expect(note).toContainText("The idea is set to 15-minute bars. Without your broker's prices today only Daily history is stored, so this test runs on Daily.");
        await shot("02-test-page");

        await page.getByRole("button", { name: "Run Backtest" }).click();
        await expect(page.getByTestId("prices-from")).toHaveText(STORED_LINE);
        const adopt = posts.find((p) => p.url === "/strategy-templates/A5/adopt");
        expect(adopt?.body).toEqual({ timeframe: "1d", params: { retest_of: "swing", retest_max_bars: 10, vol_mult: 1.5, range_mode: "fixed" } });
        expect(posts.find((p) => p.url === "/backtest/bars")?.body).toMatchObject({ timeframe: "day" });
        expect(posts.find((p) => p.url === "/backtest")?.body).toEqual({ strategy_id: "template:A5:1:1d:both:swing,,fixed,,1.5,,10,", base_timeframe: "day",
          data_source: "lake:daily" });
        // Nothing starts: no deployment is created from the test page.
        expect(posts.some((p) => p.url.startsWith("/deployments"))).toBe(false);
        await page.getByTestId("prices-from").scrollIntoViewIfNeeded();
        await shot("03-template-backtest");

        // His own intraday pick says what it needs instead of failing later.
        await choose(page, page.getByLabel("Chart timeframe"), "15m");
        await expect(note).toContainText("15-minute bars need prices from your broker: log in on the Broker page, or pick Daily to test on ATP's stored history.");
      });
    });
  }
}
