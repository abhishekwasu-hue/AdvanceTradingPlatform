import { expect, test, type Page } from "@playwright/test";
import { api, madeUpBars, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";

/**
 * SFC P1 (owner APPROVED 19 Nov, docs/plans/strategy-first-chart-PLAN.md; flag `strategy_chart`): his own strategy from
 * the chart's Strategy button. SPEC acceptance tests of this phase:
 *  T1 - only that strategy's indicators, markers on its signal bars;
 *  T3 - deselect gives the prior state exactly (no study pane, no marker, no legend);
 *  T4 - another timeframe: no stale markers, the legend offers the strategy's own;
 *  T5 - a reload gives the identical markers.
 * The server's answer is made up here (the server's own evaluation is tested in backend/tests/test_sfc_p1.py).
 */
const SHOTS = process.env.SFC_SHOTS;
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  is_default: true, capabilities: {} }];

function reply(tf: string, id = "custom:7") {
  const bars = madeUpBars("5min");
  const at = (k: number) => bars[bars.length - k].timestamp;
  if (id === "custom:9") {
    return {
      strategy_id: id, name: "Range breakout", timeframe: tf, available: true, reason: null, state: "waiting", last_signal: { t: at(20), side: "short" },
      series: [], levels: [], markers: [{ id: "m1", t: at(20), kind: "entry", side: "short" }, { id: "m2", t: at(15), kind: "exit", side: "short", rule: "Target 1", pnl: 10 }],
      pa: [{ id: "p1", kind: "box", low: bars[bars.length - 30].low, high: bars[bars.length - 30].high + 20, label: "range high", from: at(40), to: at(20),
        level: bars[bars.length - 30].high + 20 }],
      position: null, pending: null, why: {}, trades: [], gaps: [],
    };
  }
  const last = bars[bars.length - 12];
  return {
    strategy_id: "custom:7", name: "EMA cross + RSI", timeframe: tf,
    levels: [{ id: "l1", value: 45, pane: "own:RSI", label: "RSI(14) 45" }],
    position: { side: "long", entry_time: at(12), entry: last.close, stop: last.close - 25, target: last.close + 50 }, pending: null, pa: [], available: true, reason: null, state: "in_trade",
    last_signal: { t: at(12), side: "long" },
    series: [
      { id: "s1", label: "EMA(close, 9)", pane: "price", points: bars.map((b) => ({ t: b.timestamp, v: b.close + 15 })) },
      { id: "s2", label: "EMA(close, 21)", pane: "price", points: bars.map((b) => ({ t: b.timestamp, v: b.close - 15 })) },
      { id: "s3", label: "RSI(14)", pane: "own:RSI", points: bars.map((b, k) => ({ t: b.timestamp, v: 50 + 20 * Math.sin(k / 7) })) },
    ],
    markers: [
      { id: "m1", t: at(60), kind: "entry", side: "long" }, { id: "m2", t: at(48), kind: "exit", side: "long", rule: "Target 1", pnl: 40 },
      { id: "m3", t: at(40), kind: "entry", side: "short" }, { id: "m4", t: at(30), kind: "skipped", side: "long" },
      { id: "m5", t: at(26), kind: "exit", side: "short", rule: "Stop Loss", pnl: -20 }, { id: "m6", t: at(12), kind: "entry", side: "long" },
    ],
    why: {}, trades: [], gaps: [],
  };
}

async function open(page: Page) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts"]));
    } catch { /* intentional: storage unavailable in this browser */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, chart_workspace: true, strategy_chart: true }, brokers: CONNECTED });
  await mockCharts(page, { prefs: { timeframe: "5min" } });
  const plots: unknown[] = [];
  await page.route(api("/strategies/v2/chart-list$"), (r) => r.fulfill({ json: { strategies: [
    { id: "custom:7", name: "EMA cross + RSI", timeframe: "5m", drawable: true, reason: null },
    { id: "custom:8", name: "Two timeframes", timeframe: null, drawable: false, reason: "Built with two timeframes in one condition" },
    { id: "custom:9", name: "Range breakout", timeframe: "5m", drawable: true, reason: null },
  ] } }));
  await page.route(api("/strategies/v2/plot$"), (r) => {
    const body = r.request().postDataJSON() as { strategy_id: string };
    plots.push(body);
    return r.fulfill({ json: reply("5m", body.strategy_id) });
  });
  await page.goto("/charts?symbol=NIFTY%2050&tf=5min");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  return plots;
}

const chart = (page: Page) => page.locator("[data-chart-theme]").first();
const canvases = (page: Page) => chart(page).locator("canvas").count();

test.describe("SFC P1 + P2: his own strategies on the chart (1440)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("T1 + T3 + T5: only its indicators and its markers; deselect is the prior state; a reload is identical", async ({ page }) => {
    const plots = await open(page);
    await expect(chart(page)).toHaveAttribute("data-markers", "0");
    const before = await canvases(page);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/sfc-p1-before.png` });

    await page.getByTestId("strategy-button").click();
    const picker = page.locator("[data-strategy-picker]");
    await expect(picker.locator("[data-strategy-option='custom:8']")).toBeDisabled();       // no exact form yet: never drawn
    await picker.locator("[data-strategy-option='custom:7']").click();
    await expect(page.locator("[data-strategy-legend]")).toContainText("EMA cross + RSI");
    await expect(page.locator("[data-strategy-legend]")).toContainText("In trade");
    await expect(page.locator("[data-strategy-legend]")).toContainText("EMA(close, 9) · EMA(close, 21) · RSI(14)");
    await expect(chart(page)).toHaveAttribute("data-markers", "6");                       // T1: every marker of its own
    await expect.poll(() => canvases(page)).toBeGreaterThan(before);                       // RSI in its own pane
    expect(plots[0]).toMatchObject({ strategy_id: "custom:7", symbol: "NIFTY 50", exchange: "NSE" });
    await expect(page.locator("[data-strategy-position]")).toContainText("Long open");            // P2: the open trade
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/sfc-p1-selected.png` });

    // T3: deselect - no marker, no legend, the same panes as before
    await page.locator("[data-strategy-clear]").click();
    await expect(page.locator("[data-strategy-legend]")).toHaveCount(0);
    await expect(chart(page)).toHaveAttribute("data-markers", "0");
    await expect.poll(() => canvases(page)).toBe(before);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/sfc-p1-deselected.png` });

    // T5: select again, reload, choose again - identical markers (the same server answer for the same bars)
    await page.getByTestId("strategy-button").click();
    await page.locator("[data-strategy-option='custom:7']").click();
    await expect(chart(page)).toHaveAttribute("data-markers", "6");
    await page.reload();
    await page.waitForLoadState("networkidle");
    await page.getByTestId("strategy-button").click();
    await page.locator("[data-strategy-option='custom:7']").click();
    await expect(chart(page)).toHaveAttribute("data-markers", "6");
  });

  test("T4: another timeframe shows no stale marker; the legend offers the strategy's own", async ({ page }) => {
    await open(page);
    await page.getByTestId("strategy-button").click();
    await page.locator("[data-strategy-option='custom:7']").click();
    await expect(chart(page)).toHaveAttribute("data-markers", "6");
    await page.getByRole("group", { name: "Timeframe" }).getByRole("button", { name: "15 min", exact: true }).click();
    await expect(page.locator("[data-strategy-switch-tf]")).toContainText("Built for");
    await expect(chart(page)).toHaveAttribute("data-markers", "0");
    await page.locator("[data-strategy-switch-tf]").click();
    await expect(chart(page)).toHaveAttribute("data-markers", "6");
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/sfc-p1-timeframe.png` });
  });

  test("T6: two strategies are two colours and two legends; removing one keeps the other", async ({ page }) => {
    await open(page);
    await page.getByTestId("strategy-button").click();
    await page.locator("[data-strategy-option='custom:7']").click();
    await expect(chart(page)).toHaveAttribute("data-markers", "6");
    await page.getByTestId("strategy-button").click();
    await expect(page.locator("[data-strategy-option='custom:7']")).toHaveAttribute("aria-selected", "true");
    await page.locator("[data-strategy-option='custom:9']").click();
    await expect(page.locator("[data-strategy-legend]")).toHaveCount(2);
    await expect(chart(page)).toHaveAttribute("data-markers", "8");                        // both strategies' markers
    const colours = await page.locator("[data-strategy-colour]").evaluateAll((els) => els.map((e) => e.getAttribute("data-strategy-colour")));
    expect(colours).toHaveLength(2);
    expect(colours[0]).not.toEqual(colours[1]);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/sfc-p2-two-strategies.png` });

    await page.locator("[data-strategy-legend='custom:7'] [data-strategy-clear]").click();
    await expect(page.locator("[data-strategy-legend]")).toHaveCount(1);
    await expect(page.locator("[data-strategy-legend='custom:9']")).toBeVisible();
    await expect(chart(page)).toHaveAttribute("data-markers", "2");
    expect(await page.locator("[data-strategy-colour]").getAttribute("data-strategy-colour")).toEqual(colours[1]);   // never recoloured
  });
});

test.describe("SFC P2 on a phone (390)", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("two strategies: the legends wrap, nothing overflows sideways", async ({ page }) => {
    await open(page);
    for (const id of ["custom:7", "custom:9"]) {
      await page.getByRole("button", { name: "More chart controls" }).click();      // a phone: the Strategy row is under More
      await page.getByRole("dialog", { name: "More chart controls" }).getByRole("button", { name: /Your strategies on the chart/ }).click();
      await page.locator(`[data-strategy-option='${id}']`).click();
    }
    await expect(page.locator("[data-strategy-legend]")).toHaveCount(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/sfc-p2-phone.png`, fullPage: false });
  });
});
