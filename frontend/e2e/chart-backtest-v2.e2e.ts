import { expect, test, type Page } from "@playwright/test";
import { api, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Chart review PR 3 (docs/design/CHART_COLOUR_REVIEW.md, CC-5): "Test a strategy" (`/strategies/test`) under `ui_v2`
 * draws its trades on the v2 chart - the same chart as the Charts page (Auto / Reset / Latest, the readout), the
 * strategy's own lines where the v2 chart has them, and the others named under it. With the flag off the page keeps
 * ProChart (the legacy baselines are untouched). Baselines `chart-backtest-v2-<width>-<theme>.png` (the chart card
 * only) at 360, 390 and 1440, taken with the font override (CI has no Inter). Every reply is made-up data: no real
 * account, price or id. The made-up trades are built from the bars the page sends, so they always sit on the chart.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

const STRATEGY = {
  id: "demo_trend", name: "Made-up trend test", description: "A made-up strategy for the browser tests.", category: "indicator_based",
  timeframes: ["5min"], default_params: { ema_fast: 9, ema_slow: 21, rsi_period: 14, bb_period: 20, bb_k: 2 },
};

type Bar = { timestamp: string; close: number };

function madeUpResult(candles: Bar[]) {
  const at = (i: number) => candles[Math.min(candles.length - 1, i)];
  const trades = [[20, 45, "LONG", 1], [70, 95, "SHORT", -1], [120, 140, "LONG", 1]].map(([from, to, direction, sign]) => ({
    symbol: "NIFTY 50", strategy_id: STRATEGY.id, direction, entry_time: at(from as number).timestamp, entry_price: at(from as number).close,
    quantity: 1, stop_loss: 0, target1: 0, target2: null, exit_time: at(to as number).timestamp, exit_price: at(to as number).close,
    exit_reason: "rule", pnl: 40 * (sign as number), charges: 1,
  }));
  return {
    strategy_id: STRATEGY.id, symbol: "NIFTY 50", total_trades: 3, winning_trades: 2, losing_trades: 1, win_rate: 66.7, net_pnl: 40,
    gross_profit: 80, gross_loss: -40, profit_factor: 2, max_drawdown: 40, avg_win: 40, avg_loss: -40, expectancy: 13.3, trades,
    equity_curve: [0, 40, 0, 40], analytics: null, exit_rules: null, run_id: null, options: null,
  };
}

async function open(page: Page, theme: "light" | "dark", v2 = true) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: v2 ? { ui_v2: true } : undefined, brokers: CONNECTED });
  await mockCharts(page, {});
  await page.route(api("/strategies$"), (r) => r.fulfill({ json: [STRATEGY] }));
  await page.route(api("/backtests$"), (r) => r.fulfill({ json: [] }));
  await page.route(api("/backtest$"), (r) => {
    const body = r.request().postDataJSON() as { candles: Bar[] };
    return r.fulfill({ json: madeUpResult(body.candles) });
  });
  await page.goto("/strategies/test");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
}

async function run(page: Page) {
  const button = page.getByRole("button", { name: "Run Backtest" });
  await expect(button).toBeEnabled();
  await button.click();
  await expect(page.getByText("Total Trades")).toBeVisible();
}

const card = (page: Page) => page.locator("[data-backtest-chart]");

const SIZES = [{ width: 1440, height: 900, phone: false }, { width: 390, height: 844, phone: true }, { width: 360, height: 800, phone: true }];

for (const size of SIZES) {
  test.describe(`Test a strategy on the v2 chart ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as const) {
      test(`the trades on the v2 chart (${theme})`, async ({ page }) => {
        await open(page, theme);
        await run(page);
        await expect(card(page).locator(".tv-lightweight-charts canvas").first()).toBeVisible();
        await expect(card(page).getByRole("group", { name: "Chart view" })).toBeVisible();
        await expect(page.locator("[data-backtest-named]")).toHaveText("This strategy also reads Bollinger Bands 20 / 2. The chart does not draw it.");
        await expect(card(page).locator("[data-chart-legend]")).toContainText("EMA 9");
        if (size.phone) await expectPhoneLayout(page);
        await card(page).scrollIntoViewIfNeeded();
        await page.mouse.move(0, 0);
        await expect(card(page)).toHaveScreenshot(`chart-backtest-v2-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.01 });
      });
    }
  });
}

test.describe("Test a strategy: the v2 chart works as on the Charts page", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the readout follows the crosshair over the trades; a sideways drag brings Latest", async ({ page }) => {
    await open(page, "light");
    await run(page);
    const root = card(page).locator("[data-autoscale]");
    await root.evaluate((e) => e.scrollIntoView({ block: "center" }));
    const plot = (await card(page).locator(".tv-lightweight-charts table tr").first().locator("td").nth(1).boundingBox())!;
    await page.mouse.move(plot.x + plot.width / 2, plot.y + plot.height / 2);
    await expect(root).toHaveAttribute("data-crosshair", "on");
    const y = plot.y + plot.height / 3;
    await page.mouse.move(plot.x + plot.width * 0.2, y);
    await page.mouse.down();
    await page.mouse.move(plot.x + plot.width * 0.9, y, { steps: 10 });
    await page.mouse.up();
    await expect(root).toHaveAttribute("data-latest-visible", "no");
  });

  test("without ui_v2 the page keeps today's chart", async ({ page }) => {
    await open(page, "dark", false);
    await expect(page.getByRole("button", { name: "Run Backtest" })).toBeVisible();
    await expect(page.locator("[data-backtest-chart]")).toHaveCount(0);
  });
});
