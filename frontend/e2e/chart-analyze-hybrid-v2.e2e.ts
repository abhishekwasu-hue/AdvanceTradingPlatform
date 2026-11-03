import { expect, test, type Page, type Route } from "@playwright/test";
import { fixtureHybrid, fixtureItem, fixtureStatus } from "../src/chartAnalyze/fixtures";
import type { AnalysisItem } from "../src/chartAnalyze/types";
import { mockCharts } from "./chartsMocks";
import { drawnChartPng } from "./drawnChart";
import { mockApi } from "./mockApi";

/**
 * Owner CA-Q1 (3 Nov): an Advanced analysis draws ATP's reading on ATP's own chart (candles, price axis, the reading's
 * lines and areas), and the drawn picture stays behind "The picture (for the download)". Mocked API, made-up data.
 */
const SHOTS = process.env.CA_Q1_SHOTS;
const PNG = drawnChartPng();

function hybridItem(): AnalysisItem {
  const h = fixtureHybrid(false);
  return { ...fixtureItem(8, null), mode: "hybrid", status: "done", result: null, hybrid: { ...h, pick: { symbol: "NIFTY 50", timeframe: "1h" },
    reading: { ...h.reading, symbol: "NIFTY 50", last_close: 24101, protected: 23980, levels: [{ kind: "swing high", price: 24260 }, { kind: "swing low", price: 23930 }],
      last_break: h.reading.last_break ? { ...h.reading.last_break, price: 24150 } : null,
      areas: [{ where: "above", low: 24300, high: 24340, distance: 199, state: "fresh", timeframe: "1h" },
        { where: "below", low: 23860, high: 23900, distance: 201, state: "tested", timeframe: "1d" }] } }, label: h.label, summary: { setup_status: null, trend: "up", symbol: "NIFTY 50" } };
}

async function open(page: Page) {
  await page.clock.install({ time: new Date("2026-10-20T10:30:00+05:30") });
  await page.addInitScript(() => {
    try { localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" })); localStorage.setItem("atp_install_dismissed", "1"); } catch { /* none */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, chart_analyze: true, chart_analyze_hybrid: true } });
  await mockCharts(page, { market: "closed" });
  const item = hybridItem();
  await page.route(/\/api(?:\/v1)?\/chart-analyze\/.*/, async (route: Route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api(?:\/v1)?\/chart-analyze/, "");
    if (path === "/status") return route.fulfill({ json: fixtureStatus({ modes: { image_only: true, hybrid: true } }) });
    if (path === "/items") return route.fulfill({ json: { items: [item] } });
    if (path === "/items/8") return route.fulfill({ json: item });
    if (/\/items\/8\/(image|reading\.png)$/.test(path)) return route.fulfill({ body: PNG, contentType: "image/png" });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  test.describe(`Advanced analysis on ATP's chart ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: SHOTS ? 2 : 1 });
    test("the reading is drawn on ATP's own chart; the picture stays for the download", async ({ page }) => {
      await open(page);
      await page.goto("/chart-analyze");
      await page.getByRole("region", { name: "Your analyses" }).getByRole("button", { name: /NIFTY 50/ }).click();
      const atp = page.locator("[data-figure='atp']");
      await expect(atp.locator("canvas").first()).toBeVisible({ timeout: 15_000 });
      await expect(atp.getByText("The picture (for the download)")).toBeVisible();
      await expect(atp.locator("img")).toBeHidden();                       // the picture is behind Details until opened
      const box = await atp.boundingBox();
      expect(box && box.width).toBeLessThanOrEqual(size.width);
      if (SHOTS) await atp.screenshot({ path: `${SHOTS}/q1-atp-chart-${size.width}.png` });
    });
  });
}
