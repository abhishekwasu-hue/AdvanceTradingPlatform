import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";

/**
 * SB-UX SU1-SU6 (owner order ATP_ORDER_CP_SB_UX, `ui_v2`): Strategies › Build as one stepped flow. The owner's proof run:
 * template "Bull put spread" -> NIFTY 15m, long -> an entry block with his own value -> strikes, width and "Next weekly
 * on expiry day" -> Save and test -> the result card -> Start on paper -> the Trading bot form with the strategy picked.
 * Nothing starts: no deployment is created anywhere in the flow. Every reply is made up (the bars are a generated walk).
 * With SBF_SHOTS=<folder> each step is saved as `<step>-<width>-<theme>.png`; no baseline is stored.
 */
const SHOTS = process.env.SBF_SHOTS;
const S = JSON.parse(readFileSync(new URL("./fixtures/setup_v2.json", import.meta.url), "utf8")) as Record<string, unknown>;
const FEATURES = { ...(F.features as Record<string, boolean>), strategy_templates: true };
const TEMPLATES = [{ id: "A5", version: 1, name: "Breakout and retest", family: "A", family_name: "Price action",
  idea: "After a level breaks, wait for the market to come back to it from the other side and reject it.", difficulty: "Beginner", style: "Both",
  timeframes: [{ value: "15m", label: "15-minute" }, { value: "1d", label: "Daily" }], default_timeframe: "15m", sides_default: "both", params: [], rules: [], available: true }];

function walk(n: number) {
  let p = 24_500;
  const start = Date.UTC(2026, 8, 1, 3, 45);
  return Array.from({ length: n }, (_, i) => {
    const o = p;
    p = p * (1 + Math.sin(i / 9) * 0.0015 + ((i * 37) % 11 - 5) / 9000);
    const c = Math.round(p * 100) / 100;
    return { timestamp: new Date(start + i * 15 * 60_000).toISOString(), open: Math.round(o * 100) / 100,
      high: Math.round(Math.max(o, c) * 100.05) / 100, low: Math.round(Math.min(o, c) * 99.95) / 100, close: c, volume: 0 };
  });
}

interface Server { saved: Record<string, unknown>[]; backtests: Record<string, unknown>[]; deployments: unknown[] }

async function open(page: Page, theme: "light" | "dark"): Promise<Server> {
  const server: Server = { saved: [], backtests: [], deployments: [] };
  await page.clock.install({ time: new Date("2026-10-30T16:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: FEATURES, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { me: true });
  await page.route(api("/system/features$"), (r) => r.fulfill({ json: FEATURES }));
  await page.route(api("/strategies$"), (r) => r.fulfill({ json: S.strategies }));
  await page.route(api("/strategy-templates$"), (r) => r.fulfill({ json: { templates: TEMPLATES } }));
  await page.route(api("/custom-strategies$"), (route) => {
    const all = server.saved.map((config, i) => ({ id: 9 + i, strategy_id: `custom:${9 + i}`, config, live_version_id: null,
      created_at: "2026-10-30T10:00:00Z", updated_at: "2026-10-30T10:00:00Z" }));
    if (route.request().method() !== "POST") return route.fulfill({ json: all });
    const config = route.request().postDataJSON() as Record<string, unknown>;
    server.saved.push(config);
    return route.fulfill({ status: 201, json: { id: 8 + server.saved.length, strategy_id: `custom:${8 + server.saved.length}`, config, live_version_id: null,
      created_at: "2026-10-30T10:00:00Z", updated_at: "2026-10-30T10:00:00Z" } });
  });
  await page.route(api("/market-data/sources$"), (r) => r.fulfill({ json: {
    sources: [{ broker: "upstox", account_label: "primary", token_status: "VALID", usable: true }], usable: true, timeframes: ["1m", "5m", "15m", "day"],
    max_symbols: 50, intraday_max_lookback_days: 30, daily_max_lookback_days: 730 } }));
  await page.route(api("/market-data/candles$"), (r) => {
    const body = r.request().postDataJSON() as { symbols: string[]; timeframe: string; lookback_days: number };
    const bars = walk(400);
    return r.fulfill({ json: { source: { broker: "upstox", account_label: "primary" }, exchange: "NSE", timeframe: body.timeframe, base_interval: "1min",
      lookback_days: body.lookback_days, before: null, fetched_at: "2026-10-30T10:00:00Z", warnings: [], note: "",
      symbols: { [body.symbols[0]]: { bars, count: bars.length, first: bars[0].timestamp, last: bars.at(-1)!.timestamp, error: null, volume_source: null } } } });
  });
  await page.route(api("/backtest$"), (route) => {
    const b = route.request().postDataJSON() as Record<string, unknown> & { candles: { timestamp: string; close: number }[] };
    server.backtests.push({ strategy_id: b.strategy_id, symbol: b.symbol, base_timeframe: b.base_timeframe, data_source: b.data_source, options: b.options });
    const bars = b.candles;
    const trade = (i: number, pnl: number) => ({ symbol: b.symbol, strategy_id: b.strategy_id, direction: "LONG", entry_time: bars[i].timestamp,
      entry_price: 120, quantity: 75, stop_loss: 0, target1: 0, target2: null, exit_time: bars[i + 8].timestamp, exit_price: 120 - pnl / 75,
      exit_reason: "EXPIRY", pnl, charges: 140 });
    const trades = [trade(40, 3_150), trade(110, -4_200), trade(180, 2_850), trade(260, 3_600), trade(330, -1_950)];
    let eq = 500_000;
    const equity = [eq, ...trades.map((t) => (eq += t.pnl))];
    return route.fulfill({ json: { ...(S.backtest as object), strategy_id: b.strategy_id, symbol: b.symbol, total_trades: 5, winning_trades: 3, losing_trades: 2,
      win_rate: 60, net_pnl: 3_450, gross_profit: 9_600, gross_loss: -6_150, profit_factor: 1.56, max_drawdown: 4_200, avg_win: 3_200, avg_loss: -3_075,
      expectancy: 690, trades, equity_curve: equity, analytics: null, run_id: null, options: null } });
  });
  await page.route(api("/broker/credentials$"), (r) => r.fulfill({ json: [{ broker_name: "upstox", account_label: "primary" }] }));
  await page.route(api("/broker/accounts$"), (r) => r.fulfill({ json: [] }));
  await page.route(api("/deployments(\\?.*)?$"), (route) => {
    if (route.request().method() === "POST") server.deployments.push(route.request().postDataJSON());
    return route.fulfill({ json: [] });
  });
  await page.route(api("/worker/status$"), (r) => r.fulfill({ json: { running: true, market_open: false, market_status: "Closed" } }));
  return server;
}

/** UI-28: no horizontal scroll and every control of the flow at least 44 px tall on a phone (the shell has its own spec). */
async function expectFlowTargets(page: Page) {
  const report = await page.evaluate(() => {
    const root = document.querySelector("[data-strategy-flow]") ?? document.querySelector("main") ?? document.body;
    const small: string[] = [];
    for (const el of Array.from(root.querySelectorAll("button, a[href], input:not([type=radio]), textarea, label:has(input[type=radio])"))) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0 || el.closest("[data-page-footnote]")) continue;
      if (r.height < 44) small.push(`${Math.round(r.height)}px ${(el.textContent ?? "").trim().slice(0, 40)}`);
    }
    return { small, scroll: document.documentElement.scrollWidth, width: window.innerWidth };
  });
  expect(report.scroll).toBeLessThanOrEqual(report.width);
  expect(report.small, "touch targets under 44 px").toEqual([]);
}

async function settle(page: Page) {
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`SB-UX stepped builder ${size.width} ${theme}`, () => {
      test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

      test("bull put on NIFTY 15m, next weekly -> backtest result -> Start on paper", async ({ page }) => {
        test.setTimeout(90_000);
        const shot = async (step: string) => {
          if (size.phone) await expectFlowTargets(page);
          if (SHOTS) await page.screenshot({ path: `${SHOTS}/${step}-${size.width}-${theme}.png`, fullPage: true, animations: "disabled" });
        };
        const server = await open(page, theme);
        await page.goto("/strategies/build");
        await settle(page);
        await expect(page.locator("[data-strategy-flow]")).toBeVisible({ timeout: 15_000 });
        await expect(page.locator("[data-start-card='pullback-area']")).toBeDisabled();
        await expect(page.locator("[data-start-card='breakout-retest']")).toBeEnabled();
        await page.locator("[data-start-card='bull-put']").click();
        await shot("01-start");

        await page.locator("[data-flow-next]").click();
        await page.getByRole("button", { name: "NIFTY 50" }).click();
        await page.locator("[data-flow-timeframes]").getByRole("button", { name: "15m" }).click();
        await expect(page.locator("[data-flow-timeframes]").getByRole("button", { name: "1W (coming soon)" })).toBeDisabled();
        await expect(page.locator("[data-flow-side='long'] input")).toBeChecked();
        await shot("02-market");

        await page.locator("[data-flow-next]").click();
        await page.getByRole("button", { name: "+ Price vs moving average" }).click();
        await page.locator("[data-block-editor]").getByLabel("Length").fill("20");
        // SU7: the higher timeframe per operand is under a closed Advanced fold; only timeframes above 15m are offered.
        const htf = page.locator("[data-block-editor] [data-advanced='operand-timeframe']").last();
        await expect(htf).not.toHaveAttribute("open", "");
        await htf.locator("summary").click();
        await htf.getByRole("combobox", { name: "Read it on" }).click();
        await expect(page.locator("[data-combobox-list] [role='option']")).toHaveText(["Same as the strategy", "30-minute", "1-hour"]);
        await page.locator("[data-combobox-list] [role='option'][data-value='60min']").click();
        await page.locator("[data-block-done]").click();
        await expect(page.locator("[data-block-chip='set']")).toHaveText(/Close is above EMA \(20\) on 1-hour/);
        await expect(page.locator("[data-soon-blocks]")).toContainText("Pullback into an area");
        await shot("03-entry");

        await page.locator("[data-flow-next]").click();
        await expect(page.locator("[data-structure='BULL_PUT_SPREAD'] input")).toBeChecked();
        // DEF-0: nothing is picked for him - Next says what is still his to choose
        await page.locator("[data-flow-next]").click();
        await expect(page.locator("[data-step-problems]")).toContainText("Pick your strikes.");
        await page.locator("[data-strike='OTM']").click();
        await page.getByLabel("Strikes away").fill("2");
        await page.getByLabel("Spread width (strikes)").fill("2");
        await page.locator("[data-expiry='ROLL']").click();
        // SU7: the ATR length is under a closed Advanced fold, which opens by itself once a stop waits for it.
        const atr = page.locator("[data-advanced='exit-atr']");
        await expect(atr).not.toHaveAttribute("open", "");
        await page.getByLabel("Stop (x ATR)").fill("1.5");
        await expect(atr).toHaveAttribute("open", "");
        await expect(atr.getByLabel("ATR length")).toBeVisible();
        await page.getByLabel("Stop (x ATR)").fill("");
        await shot("04-exit");

        await page.locator("[data-flow-next]").click();
        await page.locator("[data-save-test]").click();
        await expect(page.locator("[data-result-numbers]")).toContainText("₹", { timeout: 15_000 });
        await expect(page.locator("[data-result-numbers]")).toContainText("% from the peak");
        expect(server.saved).toHaveLength(1);
        expect(server.saved[0]).toMatchObject({ timeframe: "15min", stop_loss_atr_mult: null, target_rr: null, min_rr: null });
        expect((server.saved[0] as { long_conditions: { right: { timeframe: string | null } }[] }).long_conditions[0].right.timeframe).toBe("60min");
        expect(server.backtests[0]).toMatchObject({ strategy_id: "custom:9", symbol: "NIFTY", base_timeframe: "15min", data_source: "broker:upstox",
          options: { option_strategy: "BULL_PUT_SPREAD", expiry_rule: "ROLL", strike_rule: "OTM", strike_offset: 2, spread_width: 2 } });
        await shot("05-result");

        await page.locator("[data-start-paper]").click();
        await expect(page).toHaveURL(/\/deployments$/);
        await settle(page);
        await expect(page.locator("[data-note='draft-saved']")).toContainText("from the strategy builder is picked below");
        await expect(page.locator("[data-tested-contract]")).toBeVisible();
        await expect(page.getByText("as a bull put spread at the nearest (the next on expiry day) expiry")).toBeVisible();
        await shot("06-paper");
        // Nothing was started: he presses Start on paper there himself.
        expect(server.deployments).toHaveLength(0);
      });
    });
  }
}
