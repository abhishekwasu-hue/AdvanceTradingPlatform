import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockCharts, type ChartsCalls } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Owner report 13 Nov ("the drawing toolkit on the chart - where did it go?"), on made-up answers only (no real account,
 * price or id): the Terminal's chart panel has the Charts page's drawing tools - the foldable rail at 1440, the one Draw
 * menu at 390 - on the same drawings: a trend line drawn in the Terminal is still there after a reload and shows on the
 * Charts page for the same symbol.
 * Owner 21 Nov: a selected drawing's bar (colour, line, extension, lock, duplicate, delete, close) at 1440 and 390, every
 * change asserted on the request it sends (`calls.drawingPuts` / `drawingLocks` / `drawingDeletes`).
 * DRAW_SHOTS=<dir>: also writes the 1440 and 390 dark screenshots for the owner; no baseline is kept. With it, the
 * comparison shots (docs/design/chart-drawing-pro/COMPARE.html) run too: a selected trend line at 1440 and 390, light
 * and dark, named `<DRAW_PHASE>-terminal-<width>-<theme>.png` (DRAW_PHASE "before" on a build without the bar).
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

async function open(page: Page, theme: "dark" | "light" = "dark") {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["terminal", "charts"]));
      localStorage.setItem("atp_chart_embed_hint:1", "seen");
    } catch { /* storage unavailable */ }
  }, theme);
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

/** Owner review 21 Nov: the selected drawing's bar never covers the chart's top-left row (the Open / High / Low / Close
 * readout while the crosshair is on the chart). */
async function expectBarClearOfReadout(page: Page, scope: Locator) {
  const readout = scope.locator("[data-chart-readout]").first();
  await expect(readout).toBeVisible();
  const toolbar = scope.locator("[data-drawing-selection-bar] [role='toolbar']");
  await expect.poll(async () => {
    const [r, b] = [(await readout.boundingBox())!, (await toolbar.boundingBox())!];
    return r.x < b.x + b.width && r.x + r.width > b.x && r.y < b.y + b.height && r.y + r.height > b.y;
  }).toBe(false);
}

/** Owner 21 Nov ("selected drawing i cant delete or move so make it like tradingview"): the selected drawing's bar. */
const barOf = (page: Page) => page.locator("[data-drawing-selection-bar]");
const anchorsOf = (put: { drawing: Record<string, unknown> }) => JSON.stringify(put.drawing.anchors);

/** A trend line from 30 % / 60 % to 70 % / 30 % of the chart, drawn with the rail; returns its two ends and middle. */
async function drawTrendLine(page: Page, calls: ChartsCalls) {
  const chart = chartOf(page);
  await chart.getByRole("group", { name: "Drawing tools" }).getByRole("button", { name: "Trend line" }).click();
  const box = (await chart.locator(".tv-lightweight-charts").first().boundingBox())!;
  const a = { x: box.x + box.width * 0.3, y: box.y + box.height * 0.6 };
  const b = { x: box.x + box.width * 0.7, y: box.y + box.height * 0.3 };
  await page.mouse.click(a.x, a.y);
  await page.mouse.click(b.x, b.y);
  await expect.poll(() => calls.drawingPosts.length).toBe(1);
  return { a, b, mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, box };
}

async function drag(page: Page, from: { x: number; y: number }, dx: number, dy: number) {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x + dx, from.y + dy, { steps: 8 });
  await page.mouse.up();
}

test.describe("Terminal drawing selection bar 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("click a trend line: the bar restyles it, lock stops a move, unlock lets it move, delete removes it", async ({ page }) => {
    const calls = await open(page);
    const { mid, box } = await drawTrendLine(page, calls);
    const bar = barOf(page);
    await expect(bar).toBeVisible();                                                    // a new drawing is selected
    await page.mouse.click(box.x + box.width * 0.15, box.y + box.height * 0.85);          // empty chart: deselect
    await expect(bar).toHaveCount(0);
    await page.mouse.click(mid.x, mid.y);
    await expect(bar).toBeVisible();
    const toolbar = bar.getByRole("toolbar", { name: "Trend line settings" });
    expect(await toolbar.getByRole("button").evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")))).toEqual([
      "Colour", "Line width and style", "Extend the line", "Lock", "Duplicate", "Delete", "Close"]);
    await page.mouse.move(mid.x + 20, mid.y + 60);                                       // crosshair on: the readout shows
    await expectBarClearOfReadout(page, chartOf(page));
    // Clear of the price axis: the bar ends left of the chart's right edge.
    const plotRight = box.x + box.width;
    expect((await toolbar.boundingBox())!.x + (await toolbar.boundingBox())!.width).toBeLessThan(plotRight - 40);

    await toolbar.getByRole("button", { name: "Colour" }).click();
    const swatch = bar.getByRole("dialog", { name: "Colour" }).locator("[data-swatch]").nth(1);
    const colour = await swatch.getAttribute("data-swatch");
    await swatch.click();
    await expect.poll(() => calls.drawingPuts.length).toBe(1);
    expect(calls.drawingPuts[0].drawing).toMatchObject({ kind: "trendline", style: { color: colour } });
    await expect(bar.getByRole("dialog")).toHaveCount(0);

    await toolbar.getByRole("button", { name: "Line width and style" }).click();
    await bar.getByRole("radio", { name: "Width 3" }).click();
    await expect.poll(() => calls.drawingPuts.length).toBe(2);
    expect(calls.drawingPuts[1].drawing).toMatchObject({ style: { color: colour, width: 3 } });
    await bar.getByRole("radio", { name: "Dashed" }).click();
    await expect.poll(() => calls.drawingPuts.length).toBe(3);
    expect(calls.drawingPuts[2].drawing).toMatchObject({ style: { color: colour, width: 3, line: "dashed" } });
    await page.keyboard.press("Escape");                                                // closes the panel, keeps the bar
    await expect(bar.getByRole("dialog")).toHaveCount(0);
    await expect(toolbar.getByRole("button", { name: "Line width and style" })).toBeFocused();

    await toolbar.getByRole("button", { name: "Extend the line" }).click();
    await bar.getByRole("radio", { name: "Extend right" }).click();
    await expect.poll(() => calls.drawingPuts.length).toBe(4);
    expect(calls.drawingPuts[3].drawing).toMatchObject({ style: { extend: "right" } });

    await toolbar.getByRole("button", { name: "Lock" }).click();
    await expect.poll(() => calls.drawingLocks).toEqual([{ id: 1, locked: true }]);
    await expect(toolbar.getByRole("button", { name: "Unlock" })).toHaveAttribute("aria-pressed", "true");
    await expect(toolbar.getByRole("button", { name: "Delete" })).toBeDisabled();
    await expect(toolbar.getByRole("button", { name: "Colour" })).toBeDisabled();
    await drag(page, mid, 60, 40);
    await expect(bar.locator("[data-drawing-bar-message]")).toHaveText("This drawing is locked. Unlock it to move it.");
    expect(calls.drawingPuts).toHaveLength(4);                                          // the move was refused

    await toolbar.getByRole("button", { name: "Unlock" }).click();
    await expect(toolbar.getByRole("button", { name: "Lock" })).toHaveAttribute("aria-pressed", "false");   // the answer came
    expect(calls.drawingLocks).toEqual([{ id: 1, locked: true }, { id: 1, locked: false }]);
    await expect(bar.locator("[data-drawing-bar-message]")).toHaveCount(0);
    await drag(page, mid, 60, 40);
    await expect.poll(() => calls.drawingPuts.length).toBe(5);
    expect(anchorsOf(calls.drawingPuts[4])).not.toEqual(anchorsOf(calls.drawingPuts[3]));
    expect(calls.drawingPuts[4].drawing).toMatchObject({ style: { color: colour, width: 3, line: "dashed", extend: "right" } });

    await expect(bar).toBeVisible();
    await toolbar.getByRole("button", { name: "Delete" }).click();
    await expect.poll(() => calls.drawingDeletes).toEqual([1]);
    await expect(bar).toHaveCount(0);
  });

  test("Duplicate saves a copy and selects it; Close and Escape put the bar away", async ({ page }) => {
    const calls = await open(page);
    const { mid } = await drawTrendLine(page, calls);
    const bar = barOf(page);
    await bar.getByRole("button", { name: "Duplicate" }).click();
    await expect.poll(() => calls.drawingPosts.length).toBe(2);
    expect(calls.drawingPosts[1]).toMatchObject({ symbol: "NIFTY 50", drawing: { kind: "trendline" } });
    await bar.getByRole("button", { name: "Close" }).click();
    await expect(bar).toHaveCount(0);
    await page.mouse.click(mid.x, mid.y);
    await expect(bar).toBeVisible();
    await bar.getByRole("button", { name: "Lock" }).focus();
    await page.keyboard.press("Escape");
    await expect(bar).toHaveCount(0);
  });
});

test.describe("Terminal drawing selection bar 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("tap a trend line: the bar docks at the chart's bottom with 44 px buttons; Delete removes it", async ({ page }) => {
    const calls = await open(page);
    const tab = page.getByRole("tab", { name: "Chart" });
    if (await tab.count()) await tab.first().click();
    const chart = chartOf(page);
    await chart.getByRole("button", { name: "Drawing tools" }).click();
    await page.getByRole("dialog", { name: "Drawing tools" }).getByRole("button", { name: "Trend line" }).click();
    await expect(page.getByRole("dialog", { name: "Drawing tools" })).toHaveCount(0);
    const box = (await chart.locator(".tv-lightweight-charts").first().boundingBox())!;
    const a = { x: box.x + box.width * 0.25, y: box.y + box.height * 0.55 };
    const b = { x: box.x + box.width * 0.65, y: box.y + box.height * 0.25 };
    await page.touchscreen.tap(a.x, a.y);
    await page.touchscreen.tap(b.x, b.y);
    await expect.poll(() => calls.drawingPosts.length).toBe(1);
    const bar = barOf(page);
    await expect(bar).toBeVisible();
    await page.touchscreen.tap(box.x + box.width * 0.8, box.y + box.height * 0.2);      // empty chart: deselect
    await expect(bar).toHaveCount(0);
    await page.touchscreen.tap((a.x + b.x) / 2, (a.y + b.y) / 2);
    await expect(bar).toBeVisible();
    const toolbar = bar.getByRole("toolbar", { name: "Trend line settings" });
    const tb = (await toolbar.boundingBox())!;
    expect(tb.y).toBeGreaterThan(box.y + box.height / 2);                                // docked low on the chart
    expect(tb.x).toBeGreaterThanOrEqual(0);
    expect(tb.x + tb.width).toBeLessThanOrEqual(390);
    expect(await toolbar.evaluate((e) => e.scrollWidth - e.clientWidth)).toBeLessThanOrEqual(0);     // every button in view
    for (const h of await toolbar.getByRole("button").evaluateAll((els) => els.map((e) => e.getBoundingClientRect().height))) expect(h).toBeGreaterThanOrEqual(44);
    await expectPhoneLayout(page);
    await toolbar.getByRole("button", { name: "Delete" }).tap();
    await expect.poll(() => calls.drawingDeletes).toEqual([1]);
    await expect(bar).toHaveCount(0);
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

// The comparison page's Terminal shots (only with DRAW_SHOTS): a trend line drawn and selected; where the bar exists it is
// restyled from the bar (the second swatch, width 3), as on the Charts page shots.
for (const [width, height, phone] of [[1440, 900, false], [390, 844, true]] as const) {
  test.describe(`Terminal drawing comparison shots ${width}`, () => {
    test.use(phone ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });
    for (const theme of ["light", "dark"] as const) {
      test(`selected trend line (${theme})`, async ({ page }) => {
        test.skip(!SHOTS, "only for the comparison page (DRAW_SHOTS)");
        const calls = await open(page, theme);
        const chart = chartOf(page);
        if (phone) {
          const tab = page.getByRole("tab", { name: "Chart" });
          if (await tab.count()) await tab.first().click();
          await chart.getByRole("button", { name: "Drawing tools" }).click();
          await page.getByRole("dialog", { name: "Drawing tools" }).getByRole("button", { name: "Trend line" }).click();
        } else {
          await chart.getByRole("group", { name: "Drawing tools" }).getByRole("button", { name: "Trend line" }).click();
        }
        const box = (await chart.locator(".tv-lightweight-charts").first().boundingBox())!;
        await page.mouse.click(box.x + box.width * 0.25, box.y + box.height * 0.55);
        await page.mouse.click(box.x + box.width * 0.65, box.y + box.height * 0.25);
        await expect.poll(() => calls.drawingPosts.length).toBe(1);
        const bar = barOf(page);
        if (await bar.count()) {
          await bar.getByRole("button", { name: "Colour" }).click();
          await bar.getByRole("dialog", { name: "Colour" }).locator("[data-swatch]").nth(1).click();
          await expect.poll(() => calls.drawingPuts.length).toBe(1);
          await bar.getByRole("button", { name: "Line width and style" }).click();
          await bar.getByRole("radio", { name: "Width 3" }).click();
          await expect.poll(() => calls.drawingPuts.length).toBe(2);
          await page.keyboard.press("Escape");
          await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
        }
        if (phone) await chart.scrollIntoViewIfNeeded();
        await page.mouse.move(0, 0);
        await page.waitForTimeout(300);
        await page.screenshot({ path: `${SHOTS}/${process.env.DRAW_PHASE ?? "after"}-terminal-${width}-${theme}.png`, animations: "disabled" });
      });
    }
  });
}
