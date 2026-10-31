import { expect, test, type Page } from "@playwright/test";
import { api, madeUpBars, mockCharts } from "./chartsMocks";
import { choose } from "./combobox";
import { mockApi } from "./mockApi";

/**
 * SB-UX SU7 / SU8 (owner order ATP_ORDER_CP_SB_UX, behind `ui_v2`): "Test a strategy" keeps its testing tools under
 * closed "Advanced" folds (premium model / IV / chain CSV / sample bars; the optimiser and the robustness checks); the
 * optimiser's search is fields per setting (From / To / Step) - never a JSON box; wide tables scroll inside their card;
 * the history names the strategy; the Library shows each strategy's settings as readable chips. Every reply is
 * made-up data: no real account, price or id.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const STRATEGY = {
  id: "demo_trend", name: "Made-up trend test", description: "A made-up strategy for the browser tests.", category: "indicator_based",
  timeframes: ["5min"], default_params: { ema_fast: 9, ema_slow: 21, rsi_period: 14, target_rr: [1.5, 2.0] },
};
type Bar = { timestamp: string; close: number };

function madeUpResult(candles: Bar[]) {
  const at = (i: number) => candles[Math.min(candles.length - 1, i)];
  const trades = [[20, 45, "LONG", 1], [70, 95, "SHORT", -1]].map(([from, to, direction, sign]) => ({
    symbol: "NIFTY 50", strategy_id: STRATEGY.id, direction, entry_time: at(from as number).timestamp, entry_price: at(from as number).close,
    quantity: 1, stop_loss: 0, target1: 0, target2: null, exit_time: at(to as number).timestamp, exit_price: at(to as number).close,
    exit_reason: "rule", pnl: 40 * (sign as number), charges: 1,
  }));
  return { strategy_id: STRATEGY.id, symbol: "NIFTY 50", total_trades: 2, winning_trades: 1, losing_trades: 1, win_rate: 50, net_pnl: 0,
    gross_profit: 40, gross_loss: -40, profit_factor: 1, max_drawdown: 40, avg_win: 40, avg_loss: -40, expectancy: 0, trades,
    equity_curve: [0, 40, 0], analytics: null, exit_rules: null, run_id: null, options: null };
}
const OPTIMISED = { metric: "net_pnl", split: 0.7, in_sample_bars: 420, out_of_sample_bars: 180, combinations: 2, best_confirmed_out_of_sample: true,
  robust_count: 1, note: "Made-up note.", best: null,
  results: [[9, 812.5, 240.1], [12, 640.2, -80.4]].map(([a, i, o]) => ({ params: { ema_fast: a }, in_sample: { net_pnl: i, trades: 14 },
    out_of_sample: { net_pnl: o, trades: 6 }, overfit_gap: 1.2, flags: [] })) };
const WALK = { folds: 4, profitable_windows: 3, consistency_pct: 75, mean_window_pnl: 120.5, note: "Made-up windows.",
  windows: [1, 2, 3, 4].map((w) => ({ window: w, from: "2026-01-01", to: "2026-02-01", bars: 150, trades: 5, net_pnl: 100 * w, win_rate: 55,
    profit_factor: 1.4, max_drawdown: 90, expectancy: 12 })) };
const MONTE = { monte_carlo: { runs: 1000, final_pnl: { p5: -120, p50: 40, p95: 260 }, max_drawdown: { p50: 80, p95: 190 },
  probability_of_loss_pct: 22, probability_dd_exceeds_original_pct: 31 } };

async function open(page: Page, theme: "light" | "dark", path: string, extra?: (page: Page) => Promise<void>) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try { localStorage.setItem("atp_appearance", JSON.stringify({ theme: t })); localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true }, brokers: CONNECTED });
  await mockCharts(page, {});
  const sent: { optimise: unknown[] } = { optimise: [] };
  await page.route(api("/strategies$"), (r) => r.fulfill({ json: [STRATEGY] }));
  await page.route(api("/backtests$"), (r) => r.fulfill({ json: [{ id: 7, created_at: "2026-10-18T10:00:00Z", strategy_id: "demo_trend", symbol: "NIFTY",
    base_timeframe: "5min", data_source: "broker", bars: 600, engine_version: 3, exit_rules: { trailing_stop_pct: 1.5 }, total_trades: 2, net_pnl: 0,
    win_rate: 50, max_drawdown: 40 }] }));
  // M55 T3: the page names symbol + timeframe and the server loads the bars itself (the same made-up bars here).
  await page.route(api("/backtest$"), (r) => {
    const b = r.request().postDataJSON() as { candles?: Bar[]; base_timeframe: string };
    return r.fulfill({ json: madeUpResult(b.candles ?? madeUpBars(b.base_timeframe)) });
  });
  await page.route(api("/backtest/optimize$"), (r) => { sent.optimise.push(r.request().postDataJSON()); return r.fulfill({ json: OPTIMISED }); });
  await page.route(api("/backtest/walk-forward"), (r) => r.fulfill({ json: WALK }));
  await page.route(api("/backtest/monte-carlo"), (r) => r.fulfill({ json: MONTE }));
  await page.route(api("/backtest/option-chain/coverage$"), (r) => r.fulfill({ json: [] }));
  if (extra) await extra(page);   // registered last, so it answers first
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  return sent;
}

const noPageScroll = async (page: Page) => {
  const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
  expect(r.scroll).toBeLessThanOrEqual(r.width);
};

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`SB-UX Advanced and copy ${size.width} ${theme}`, () => {
      test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

      test("Test a strategy: closed Advanced folds, the optimiser as fields, tables that scroll", async ({ page }) => {
        test.setTimeout(90_000);
        const sent = await open(page, theme, "/strategies/test");
        // The premium model / IV / chain CSV sit under the closed Advanced settings.
        await choose(page, page.locator("select").filter({ has: page.locator("option[value='OPTION']") }), "OPTION");
        const settings = page.locator("[data-advanced='settings']");
        await expect(settings).toBeVisible();
        await expect(settings).not.toHaveAttribute("open", "");
        await expect(settings.getByText("Premiums from")).toBeHidden();
        await settings.locator(":scope > summary").click();
        await expect(settings.getByText("Premiums from")).toBeVisible();
        await choose(page, page.locator("select").filter({ has: page.locator("option[value='OPTION']") }), "UNDERLYING");

        await page.getByRole("button", { name: "Run Backtest" }).click();
        await expect(page.getByText("Total Trades")).toBeVisible();
        const checks = page.locator("[data-advanced='checks']");
        await expect(checks).not.toHaveAttribute("open", "");
        await checks.locator(":scope > summary").click();

        const fields = page.locator("[data-optimiser-fields]");
        await expect(fields.locator("input.font-mono")).toHaveCount(0);
        await expect(page.getByText(/JSON/)).toHaveCount(0);
        await fields.getByRole("button", { name: "+ Add a setting to vary" }).click();
        await expect(fields.getByRole("combobox", { name: "Setting" })).toHaveAttribute("data-combobox", "ema_fast");
        await expect(fields.getByRole("button", { name: "Optimise" })).toBeDisabled();
        await expect(fields.locator("[data-optimiser-problems]")).toHaveText("Fast EMA: fill From, To and Step.");
        await fields.getByLabel("From").fill("12");
        await fields.getByLabel("To").fill("9");
        await fields.getByLabel("Step").fill("3");
        await expect(fields.locator("[data-optimiser-problems]")).toHaveText("Fast EMA: From must not be above To.");
        await fields.getByLabel("From").fill("9");
        await fields.getByLabel("To").fill("12");
        await expect(fields.locator("[data-optimiser-count]")).toHaveText("2 of 60 combinations");
        await fields.getByRole("button", { name: "Optimise" }).click();
        await expect(page.locator("[data-optimiser]")).toContainText("Fast EMA 9");
        expect(sent.optimise[0]).toMatchObject({ strategy_id: "demo_trend", param_grid: { ema_fast: [9, 12] }, metric: "net_pnl" });
        await expect(page.locator("[data-optimiser] thead")).toContainText("Net P&L, first 70%");

        await page.getByRole("button", { name: "Run Monte Carlo + walk-forward" }).click();
        await expect(page.locator("[data-robustness] table")).toBeVisible();
        // Every data table (the chart's own drawing table aside) scrolls inside its card.
        const unwrapped = await page.evaluate(() => [...document.querySelectorAll("main table")]
          .filter((t) => !t.closest(".tv-lightweight-charts") && !t.parentElement?.classList.contains("overflow-x-auto")).length);
        expect(unwrapped).toBe(0);
        await noPageScroll(page);

        // SU8: the history names the strategy and the exits in words.
        await expect(page.locator("td", { hasText: "Made-up trend test" })).toBeVisible();
        await expect(page.getByText("Trailing stop 1.5%")).toBeVisible();
        await expect(page.getByText("demo_trend")).toHaveCount(0);
      });

      test("The full builder under Advanced: plain indicator names, no parser jargon, no ids", async ({ page }) => {
        await open(page, theme, "/strategies/build?advanced=1", (p) => p.route(api("/custom-strategies$"), (r) => r.fulfill({ json: [{ id: 9,
          strategy_id: "custom:9", config: { name: "My made-up idea", timeframe: "15min", long_conditions: [], short_conditions: [],
          stop_loss_atr_mult: null, atr_period: 14, target_rr: null, min_rr: null } }] })));
        await expect(page.getByText("My made-up idea")).toBeVisible();
        await expect(page.getByText("(15-minute)")).toBeVisible();
        await expect(page.getByText(/custom:9|id custom/)).toHaveCount(0);
        await expect(page.getByText(/deterministic|credentials/i)).toHaveCount(0);
        await page.getByRole("button", { name: "+ Add condition" }).first().click();
        await expect(page.locator("option[value='PDH']").first()).toHaveText("Previous day high");
        await expect(page.locator("option[value='OR_HIGH']").first()).toHaveText("Opening range high");
      });

      test("Library: settings as readable chips, never JSON", async ({ page }) => {
        await open(page, theme, "/strategies");
        // M49 U3: at most three on the card (the chart is one of them); the rest under "More settings".
        const chips = page.locator("[data-library-card='demo_trend'] [data-main-settings] li");
        await expect(chips).toHaveText(["Fast EMA 9", "Slow EMA 21", "Chart 5-minute"]);
        await expect(page.locator("pre")).toHaveCount(0);
        await page.locator("[data-library-fold='more-settings'] summary:visible").click();
        await expect(page.getByText("RSI length").locator("visible=true")).toHaveCount(1);
        await expect(page.getByText("Targets (R)").locator("visible=true")).toHaveCount(1);
        await noPageScroll(page);
      });
    });
  }
}
