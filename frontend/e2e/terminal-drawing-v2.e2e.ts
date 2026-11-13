import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Owner report 13 Nov ("the drawing toolkit on the chart - where did it go?"), on made-up answers only (no real account,
 * price or id): the Terminal's chart panel has the Charts page's drawing tools - the foldable rail at 1440, the one Draw
 * menu at 390 - on the same drawings: a trend line drawn in the Terminal is still there after a reload and shows on the
 * Charts page for the same symbol.
 * DRAW_SHOTS=<dir>: also writes the 1440 and 390 dark screenshots for the owner; no baseline is kept.
 */

const api = (path: string) => new RegExp(`/api(?:/v1)?${path}`);
const SHOTS = process.env.DRAW_SHOTS;
const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as { expiries: unknown; live: Record<string, unknown> };
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON = { ui_v2: true, terminal: true, paper_account: true, watchlists: true };
const STATUS = {
  today: { mtm: 0, booked: 0, note: "" }, open: { count: 0, priced: 0, mtm: 0, as_of: "2026-10-27T05:00:00Z" },
  funds: { balance: 1_000_000, available: 1_000_000, source: "practice", note: "" }, margin: { used: 0, estimated: true, not_estimated: 0, note: "" },
  mode: { mode: "PAPER", live_allowed: false, message: "Practice mode: no real order is sent and no money moves." },
  feed: { state: "live", message: "Live prices from Upstox.", broker_name: "Upstox" },
  kill_switch: { engaged: false, scope: null, message: "New orders are allowed." }, as_of: "2026-10-27T05:00:00Z",
};

async function open(page: Page) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["terminal", "charts"]));
      localStorage.setItem("atp_chart_embed_hint:1", "seen");
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: ON, brokers: CONNECTED });
  const calls = await mockCharts(page);
  await page.route(api("/terminal/status$"), (r) => r.fulfill({ json: STATUS }));
  await page.route(api("/positions\\?mine=true$"), (r) => r.fulfill({ json: [] }));
  await page.route(api("/orders\\?mine=true"), (r) => r.fulfill({ json: [] }));
  await page.route(api("/market/strip$"), (r) => r.fulfill({ json: { indices: [{ key: "NIFTY 50", label: "NIFTY 50", ltp: 24420, prev_close: 24400 }] } }));
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, (route) => (
    new URL(route.request().url()).pathname.endsWith("/expiries") ? route.fulfill({ json: DATA.expiries }) : route.fulfill({ json: { ...DATA.live, market_open: true } })));
  await page.route(api("/option-chain/analyses"), (r) => r.fulfill({ status: 404, json: { detail: "Not found" } }));
  await page.route(api("/paper/fno/"), (r) => r.fulfill({ json: [] }));
  await page.route(api("/trading/incomplete$"), (r) => r.fulfill({ json: { items: [], as_of: "2026-10-27T05:00:00Z" } }));
  await page.goto("/terminal");
  await page.waitForLoadState("networkidle");
  return calls;
}

const chartOf = (page: Page) => page.locator("[data-panel-body='chart'] [data-embedded-chart]").first();

async function shot(page: Page, name: string, width: number) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}-${width}.png`, animations: "disabled" });
}

test.describe("Terminal drawing tools 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a trend line drawn in the Terminal stays after a reload and shows on the Charts page", async ({ page }) => {
    const calls = await open(page);
    const chart = chartOf(page);
    const rail = chart.getByRole("group", { name: "Drawing tools" });
    await expect(rail).toBeVisible();
    await expect(rail.getByRole("button", { name: "Trend line" })).toBeEnabled();
    await shot(page, "after-terminal", 1440);
    await rail.getByRole("button", { name: "Trend line" }).click();
    const plot = chart.locator(".tv-lightweight-charts").first();
    const box = (await plot.boundingBox())!;
    await page.mouse.click(box.x + box.width * 0.3, box.y + box.height * 0.6);
    await page.mouse.click(box.x + box.width * 0.7, box.y + box.height * 0.3);
    await expect.poll(() => calls.drawingPosts.length).toBe(1);
    expect(calls.drawingPosts[0]).toMatchObject({ symbol: "NIFTY 50", exchange: "NSE", drawing: { kind: "trendline" } });
    await expect(rail.getByRole("button", { name: "Delete all drawings" })).toBeEnabled();
    await shot(page, "after-terminal-drawn", 1440);

    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(chartOf(page).getByRole("group", { name: "Drawing tools" }).getByRole("button", { name: "Delete all drawings" })).toBeEnabled();

    // Fold and unfold the rail: the panel keeps its width for the chart.
    await chartOf(page).getByRole("button", { name: "Fold drawing tools" }).click();
    await expect(chartOf(page).locator("[data-drawing-rail]")).toHaveCount(0);
    await chartOf(page).getByRole("button", { name: "Show drawing tools" }).click();
    await expect(chartOf(page).locator("[data-drawing-rail]")).toBeVisible();

    await page.goto("/charts/NIFTY%2050");
    await page.waitForLoadState("networkidle");
    await expect(page.locator("[data-drawing-rail]").getByRole("button", { name: "Delete all drawings" })).toBeEnabled();
    expect(calls.drawingPosts).toHaveLength(1);
  });
});

test.describe("Terminal drawing tools 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("one Draw button opens the tools; no rail on a phone", async ({ page }) => {
    await open(page);
    const tab = page.getByRole("tab", { name: "Chart" });
    if (await tab.count()) await tab.first().click();
    const chart = chartOf(page);
    await expect(chart.locator("[data-drawing-rail]")).toBeHidden();
    await chart.getByRole("button", { name: "Drawing tools" }).click();
    const menu = page.getByRole("dialog", { name: "Drawing tools" });
    await expect(menu.getByRole("button", { name: "Trend line" })).toBeVisible();
    await expectPhoneLayout(page);
    await shot(page, "after-terminal-draw-menu", 390);
  });
});
