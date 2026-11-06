import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { screenerTool } from "./screenerTools";

/**
 * FN2-j (flags `screener_history`, `screener_backtest`; behind `ui_v2`): "Look back" on the scan.
 * - "When did this screen hit": who matched on a date, then how the matches moved afterwards (fitness).
 * - "Backtest": the rolling simulation, only while `screener_backtest` is on.
 * - Flags off: no tool. Made-up data; the history routes are mocked here.
 */
const read = (path: string): unknown => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
const registry = (read("../src/screener/builderTexts.json") as { registry: unknown }).registry;
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
// The trader pack (as Past runs' spec reads it) without the two flags under test: "Look back" joins "More tools" only
// when that menu is already there.
const PACK = (() => {
  const src = readFileSync(new URL("../../backend/app/platform/trader_pack.py", import.meta.url), "utf8");
  const body = /PACK: Tuple\[str, \.\.\.\] = \(([\s\S]*?)\n\)/.exec(src)?.[1] ?? "";
  return [...body.replace(/#.*$/gm, "").matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
})();
const BASE: Record<string, boolean> = { ...Object.fromEntries(PACK.filter((f) => f !== "screener_history" && f !== "screener_backtest").map((f) => [f, true])),
  ui_v2: true, ai_copilot: true, screener_v2: true };
const BOTH: MockOptions = { features: { ...BASE, screener_history: true, screener_backtest: true }, brokers: CONNECTED };
const HISTORY_ONLY: MockOptions = { features: { ...BASE, screener_history: true }, brokers: CONNECTED };
const OFF: MockOptions = { features: BASE, brokers: CONNECTED };

const ASOF = { date: "2026-03-10", text: "RSI(14) > 60", universe: 50, matched: ["TCS", "INFY", "SBIN"], not_evaluated: {}, traded_that_day: 49,
  notes: [], data: "x", disclaimer: "Matches are symbols that passed the screen's filters on the data shown; they are not recommendations." };
const FITNESS = { text: "RSI(14) > 60", universe: "list:50", start: "2026-01-01", end: "2026-03-31", every: "weekly", dates: 13, symbols: 50,
  avg_passers: 6.5, turnover: 0.3, main_horizon: 5, confidence: "Low sample (24 observations over 13 dates): treat the figures as anecdotal.",
  horizons: [
    { horizon: 1, dates: 13, observations: 80, passers_mean: 0.0012, universe_mean: 0.0004, excess_mean: 0.0008, hit_rate: 0.55, t_stat: 0.9, low_sample: false },
    { horizon: 5, dates: 13, observations: 24, passers_mean: -0.004, universe_mean: 0.002, excess_mean: -0.006, hit_rate: 0.42, t_stat: -1.1, low_sample: true },
  ], deflation: { n_trials: 1, dsr: null, note: "Deflated for 1 screen fitness runs." }, notes: ["Made-up note."],
  disclaimer: "Screen fitness describes how a screen's past matches moved afterwards, on past data. It is information, not a recommendation." };
const metrics = (total: number, dd: number) => ({ total_return: total, cagr: null, volatility: 0.1, sharpe: 0.5, max_drawdown: dd, sessions: 60 });
const BACKTEST = { strategy: metrics(0.052, -0.031), benchmarks: { nifty_50: metrics(0.03, -0.04), equal_weight: metrics(0.041, -0.045) },
  excess_total_return: { nifty_50: 0.022, equal_weight: 0.011 }, final_equity: 1052000, capital: 1000000, costs: 4210.5, rebalances: 3, avg_holdings: 5.3,
  turnover_per_rebalance: 0.4, start: "2026-01-01", end: "2026-03-31", every: "monthly", symbols: 50,
  equity: [100, 101, 99, 102, 104, 103, 105].map((e, i) => ({ day: `2026-01-0${i + 1}`, equity: e * 10000 })),
  deflation: { n_trials: 1, dsr: null, note: "x" }, notes: [], fills: "Decided at the close, filled at the next open.",
  disclaimer: "A simulation on past data. It places nothing and does not predict future results." };

interface Calls { asof: Record<string, unknown>[]; fitness: Record<string, unknown>[]; backtest: Record<string, unknown>[] }

async function open(page: Page, options: MockOptions): Promise<Calls> {
  const calls: Calls = { asof: [], fitness: [], backtest: [] };
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["screener"]));
    } catch { /* intentional: storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, options);
  await page.route(/\/api(\/v1)?\/screener\//, async (route) => {
    const url = route.request().url();
    if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: registry } });
    if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: "", plan: { cost: 1, lookback: {} } } });
    const body = route.request().postDataJSON() as Record<string, unknown>;
    if (/\/history\/asof$/.test(url)) { calls.asof.push(body); return route.fulfill({ json: ASOF }); }
    if (/\/history\/fitness$/.test(url)) { calls.fitness.push(body); return route.fulfill({ json: FITNESS }); }
    if (/\/history\/backtest$/.test(url)) { calls.backtest.push(body); return route.fulfill({ json: BACKTEST }); }
    return route.fulfill({ status: 503, headers: { "X-Feature-Disabled": "screener_gallery" }, json: { detail: "disabled" } });
  });
  await page.goto("/screener");
  await expect(page.getByTestId("start-scan")).toBeVisible();
  await page.getByRole("button", { name: "Momentum" }).click();
  return calls;
}

async function openDialog(page: Page) {
  await screenerTool(page, "screen-history-open");
  const dialog = page.getByRole("dialog", { name: "Look back" });
  await expect(dialog.getByTestId("screen-history")).toBeVisible();
  return dialog;
}

async function walk(page: Page, calls: Calls, backtest: boolean) {
  const dialog = await openDialog(page);
  await dialog.getByTestId("history-asof-date").fill("2026-03-10");
  await dialog.getByTestId("history-asof-run").click();
  const asof = dialog.getByTestId("history-asof-result");
  await expect(asof).toContainText("3 matched of 49 that traded on 2026-03-10");
  await expect(asof).toContainText("INFY");
  expect(calls.asof[0]).toMatchObject({ date: "2026-03-10", universe: { symbols: expect.any(Array) } });
  expect(String(calls.asof[0].source)).toContain("RSI");
  await dialog.getByTestId("history-fitness-start").fill("2026-01-01");
  await dialog.getByTestId("history-fitness-end").fill("2026-03-31");
  await dialog.getByTestId("history-fitness-run").click();
  await expect(dialog.getByTestId("history-horizon-5")).toContainText("−0.40%");
  await expect(dialog.getByTestId("history-horizon-1")).toContainText("+0.12%");
  await expect(dialog.getByTestId("history-fitness-confidence")).toContainText("Low sample");
  expect(calls.fitness[0]).toMatchObject({ start: "2026-01-01", end: "2026-03-31", every: "weekly" });
  if (!backtest) return dialog;
  await dialog.getByTestId("history-backtest-start").fill("2026-01-01");
  await dialog.getByTestId("history-backtest-end").fill("2026-03-31");
  await dialog.getByTestId("history-backtest-run").click();
  const out = dialog.getByTestId("history-backtest-result");
  await expect(out).toContainText("+5.20%");
  await expect(out.getByTestId("history-bench-nifty_50")).toContainText("NIFTY 50");
  await expect(out.getByTestId("history-backtest-curve")).toBeVisible();
  expect(calls.backtest[0]).toMatchObject({ start: "2026-01-01", end: "2026-03-31", every: "monthly" });
  return dialog;
}

test.describe("Look back, 1440", () => {
  test.use({ viewport: { width: 1440, height: 1000 } });

  test("who matched on a date, how the matches moved, and the backtest", async ({ page }) => {
    const calls = await open(page, BOTH);
    await walk(page, calls, true);
    await expect(page.locator("body")).not.toContainText(/\b(buy|sell|target|stop-loss)\b/i);
  });

  test("with only screener_history on there is no Backtest section", async ({ page }) => {
    await open(page, HISTORY_ONLY);
    const dialog = await openDialog(page);
    await expect(dialog.getByTestId("history-asof")).toBeVisible();
    await expect(dialog.getByTestId("history-backtest")).toHaveCount(0);
  });

  test("flags off: no Look back tool", async ({ page }) => {
    await open(page, OFF);
    await page.getByTestId("screener-more").click();
    await expect(page.getByTestId("past-runs-open")).toBeVisible();
    await expect(page.getByTestId("screen-history-open")).toHaveCount(0);
  });

  test("a missing date is said in words and sends nothing", async ({ page }) => {
    const calls = await open(page, BOTH);
    const dialog = await openDialog(page);
    await dialog.getByTestId("history-asof-run").click();
    await expect(dialog.getByTestId("history-asof-problem")).toHaveText("Pick a date.");
    expect(calls.asof).toHaveLength(0);
  });
});

test.describe("Look back, 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("the phone layout rules hold with all three results shown", async ({ page }) => {
    const calls = await open(page, BOTH);
    await walk(page, calls, true);
    await expectPhoneLayout(page);
  });
});
