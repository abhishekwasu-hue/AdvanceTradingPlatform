import { expect, test, type Page } from "@playwright/test";
import { api, madeUpBars, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M91 (owner order): a watchlist's "Screen" - his own saved screen chosen at the top of the list (matches on top, the
 * rest dimmed, "Matches only", "n of m match") and the same screen drawn on a stock's chart opened from that list (the
 * indicator line, a marker per bar where the whole screen held, the legend with ✓ / ✗ and each value). Flags `ui_v2`,
 * `watchlists`, `watchlist_screen`. Every reply is made-up data: no real account, price or id.
 * PROOF_DIR set: the proof screenshots (phone 390 and desktop 1440, dark) are written there (not baselines).
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const SYMBOLS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"];
const MATCHED = ["CCC", "FFF", "HHH"];
const LIST = { id: 1, name: "Swing", items: SYMBOLS.map((symbol) => ({ symbol, exchange: "NSE" })) };
const SCREEN = { id: 4, name: "My trend screen", base_tf: "1d", usable: true };

/** The overlay of a made-up daily chart: SMA(close, 20) as the screen's line, a marker where close > SMA and RSI held. */
function overlay(symbol: string) {
  const bars = madeUpBars("day").slice(-120);
  const close = bars.map((b) => b.close);
  const sma = close.map((_, i) => (i < 19 ? null : close.slice(i - 19, i + 1).reduce((a, b) => a + b, 0) / 20));
  const hits = bars.filter((b, i) => sma[i] !== null && b.close > (sma[i] as number) && i % 3 !== 0).map((b) => b.timestamp);
  const last = bars.length - 1;
  const matched = MATCHED.includes(symbol);
  const f = (v: number) => v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return {
    symbol, exchange: "NSE", base_tf: "1d", screen: { id: 4, name: SCREEN.name }, text: "close > SMA(close, 20) AND RSI(14) > 50",
    disclaimer: "Stocks that meet your own screen on the data shown; they are not recommendations.", available: true, matched,
    legend: [
      { id: "b1.c1", block: "b1", text: "close > SMA(close, 20)", state: "met", value: `close ${f(close[last])} > SMA(close, 20) ${f(sma[last] as number)}` },
      { id: "b2.c1", block: "b2", text: "RSI(14) > 50", state: matched ? "met" : "failed", value: `RSI(14) ${matched ? "58.40" : "44.10"} > 50` },
      { id: "b3.c1", block: "b3", text: "volume > 1.5 * SMA(volume, 20)", state: matched ? "met" : "failed", value: "volume 61,200 > 1.5 * SMA(volume, 20) 54,900" },
    ],
    hits: matched ? [...hits.filter((t) => t !== bars[last].timestamp), bars[last].timestamp] : hits.filter((t) => t !== bars[last].timestamp),
    as_of: { bar_start: bars[last].timestamp },
    bars: { t: bars.map((b) => b.timestamp), o: bars.map((b) => b.open), h: bars.map((b) => b.high), l: bars.map((b) => b.low), c: close, v: bars.map((b) => b.volume) },
    drawables: [{ id: "d1", row: "b1.c1", kind: "overlay", pane: "price", label: "close > SMA(close, 20)", failed: false,
      series: [{ name: "SMA(close, 20)", values: sma, style: "line" }], levels: [] }],
    matched_index: last,
  };
}

interface Calls { checks: { symbols: string[]; list_key: string }[]; puts: { key: string; body: Record<string, unknown> }[]; overlays: string[] }

async function open(page: Page, path: string, theme: "dark" | "light" = "dark"): Promise<Calls> {
  const calls: Calls = { checks: [], puts: [], overlays: [] };
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, watchlists: true, screener_v2: true, watchlist_screen: true }, brokers: CONNECTED });
  await mockCharts(page, { watchlists: [LIST] });
  let choice: Record<string, unknown> = { screen_id: 4, matches_only: false, alert: false, telegram: false };
  await page.route(api("/screener/watchlist-screen/screens$"), (r) => r.fulfill({ json: { screens: [SCREEN] } }));
  await page.route(api("/screener/watchlist-screen/settings(/.*)?$"), (r) => {
    const req = r.request();
    if (req.method() === "PUT") {
      choice = req.postDataJSON() as Record<string, unknown>;
      calls.puts.push({ key: decodeURIComponent(new URL(req.url()).pathname.split("/").pop() ?? ""), body: choice });
      return r.fulfill({ json: { list_key: "o:1", choice } });
    }
    return r.fulfill({ json: { lists: { "o:1": choice } } });
  });
  await page.route(api("/screener/watchlist-screen/check$"), (r) => {
    const body = r.request().postDataJSON() as { symbols: string[]; list_key: string };
    calls.checks.push({ symbols: body.symbols, list_key: body.list_key });
    const results = body.symbols.map((s) => ({ symbol: s, matched: MATCHED.includes(s), reason: null, marks: MATCHED.includes(s) ? ["met", "met", "met"] : ["met", "failed", "failed"] }));
    return r.fulfill({ json: { text: "x", screen: { id: 4, name: SCREEN.name }, base_tf: "1d", data_source: "lake:daily", scanned: results.length,
      matched: results.filter((x) => x.matched).map((x) => x.symbol), results, funnel: { universe: results.length, with_data: results.length, stages: [] },
      budget: { broker_calls: 0, refused: 0, per_minute: 100 }, bar: "2026-10-16T03:45:00Z", next_check_at: Date.parse("2026-10-19T15:30:20+05:30"),
      alerted: [], disclaimer: "" } });
  });
  await page.route(api("/screener/watchlist-screen/overlay$"), (r) => {
    const body = r.request().postDataJSON() as { symbol: string };
    calls.overlays.push(body.symbol);
    return r.fulfill({ json: overlay(body.symbol) });
  });
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  return calls;
}

const rows = (page: Page) => page.getByRole("list", { name: "Symbols in Swing" }).locator("[data-row]");
const proof = async (page: Page, name: string) => {
  if (process.env.PROOF_DIR) await page.screenshot({ path: `${process.env.PROOF_DIR}/${name}.png`, fullPage: false });
};

test.describe("M91 desktop 1440 dark", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the list's screen: matches on top, the rest dimmed, the count; the matched stock's chart draws the screen and its legend", async ({ page }) => {
    const calls = await open(page, "/charts/CCC?tf=day&wscreen=4");
    await expect(page.getByTestId("watch-screen-pick")).toHaveValue("4");                    // W1: remembered for this list
    await expect(page.getByTestId("watch-screen-count")).toContainText("3 of 8 match");
    await expect(rows(page)).toHaveText([/CCC/, /FFF/, /HHH/, /AAA/, /BBB/, /DDD/, /EEE/, /GGG/]);
    await expect(rows(page).locator("xpath=self::*[@data-match='no']")).toHaveCount(5);
    expect(calls.checks).toHaveLength(1);                                                     // W3: once; again only at the bar close
    expect(calls.checks[0]).toEqual({ symbols: SYMBOLS, list_key: "o:1" });
    const legend = page.getByTestId("screen-legend");                                          // C1
    await expect(legend).toContainText("My trend screen");
    await expect(legend).toContainText("Meets the screen");
    await expect(legend.locator("[data-state='met']")).toHaveCount(3);
    await expect(legend).toContainText("RSI(14) 58.40 > 50");
    await expect(page.getByTestId("screen-overlay-button")).toHaveAttribute("aria-pressed", "true");
    await page.waitForTimeout(300);
    await proof(page, "desktop-1440-dark-chart-overlay");
    // the toggle hides the overlay; nothing else changes
    await page.getByTestId("screen-overlay-button").click();
    await expect(legend).toHaveCount(0);
    await page.getByTestId("screen-overlay-button").click();
    await expect(page.getByTestId("screen-legend")).toBeVisible();
    // a dimmed row opens its chart with the screen too (it does not meet it: the legend says so)
    await rows(page).filter({ hasText: "AAA" }).getByRole("button", { name: /^AAA/ }).click();
    await expect(page).toHaveURL(/\/charts\/AAA\?wscreen=4$/);
    await expect(page.getByTestId("screen-legend-result")).toHaveText("Does not meet it");
    await expect(page.getByTestId("screen-legend").locator("[data-state='failed']")).toHaveCount(2);
  });

  test("Matches only shows the matches alone and is remembered; None takes the screen off and the chart draws nothing", async ({ page }) => {
    const calls = await open(page, "/charts/CCC?tf=day&wscreen=4");
    await page.getByTestId("watch-screen-only").click();
    await expect(rows(page)).toHaveText([/CCC/, /FFF/, /HHH/]);
    expect(calls.puts.at(-1)).toEqual({ key: "o:1", body: { screen_id: 4, matches_only: true, alert: false, telegram: false } });
    await page.getByTestId("watch-screen-alerts").click();                                      // W4: off until he turns it on
    await expect(page.getByTestId("watch-screen-alert")).not.toBeChecked();
    await expect(page.getByTestId("watch-screen-telegram")).toBeDisabled();
    await page.getByTestId("watch-screen-pick").selectOption("");
    await expect(rows(page)).toHaveText(SYMBOLS.map((s) => new RegExp(s)));                     // the list's own order back
    expect(calls.puts.at(-1)?.body.screen_id).toBeNull();
    await rows(page).filter({ hasText: "DDD" }).getByRole("button", { name: /^DDD/ }).click();
    await expect(page).toHaveURL(/\/charts\/DDD$/);
    await expect(page.getByTestId("screen-legend")).toHaveCount(0);
    await page.waitForTimeout(300);
    await proof(page, "desktop-1440-dark-before-no-screen");
  });
});

test.describe("M91 phone 390 dark", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("the Watchlist page with a screen chosen: matches on top, fits the phone", async ({ page }) => {
    await open(page, "/watchlist");
    await expect(page.getByTestId("watch-screen-count")).toContainText("3 of 8 match");
    await expect(rows(page)).toHaveText([/CCC/, /FFF/, /HHH/, /AAA/, /BBB/, /DDD/, /EEE/, /GGG/]);
    await expectPhoneLayout(page);
    await proof(page, "phone-390-dark-watchlist");
    await page.getByTestId("watch-screen-pick").selectOption("");
    await expect(rows(page)).toHaveText(SYMBOLS.map((x) => new RegExp(x)));
    await proof(page, "phone-390-dark-before-no-screen");
  });

  test("a matched stock's chart with the overlay and the legend, on the phone", async ({ page }) => {
    const calls = await open(page, "/charts/FFF?tf=day&wscreen=4");
    await expect(page.getByTestId("screen-legend")).toContainText("Meets the screen");
    expect(calls.overlays).toContain("FFF");
    await expectPhoneLayout(page);
    await page.getByTestId("screen-legend").scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollTo(0, 120));
    await page.waitForTimeout(300);
    await proof(page, "phone-390-dark-chart-overlay");
  });
});
