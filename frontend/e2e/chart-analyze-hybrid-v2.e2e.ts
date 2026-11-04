import { expect, test, type Page, type Route } from "@playwright/test";
import { fixtureHybrid, fixtureHybridOld, fixtureItem, fixtureStatus } from "../src/chartAnalyze/fixtures";
import type { AnalysisItem, HybridResult } from "../src/chartAnalyze/types";
import { mockCharts } from "./chartsMocks";
import { drawnChartPng } from "./drawnChart";
import { mockApi } from "./mockApi";

/**
 * Owner CA-Q1 (3 Nov): an Advanced analysis draws ATP's reading on ATP's own chart (candles, price axis, the reading's
 * lines and areas), and the drawn picture stays behind "The picture and the download". ADV-2 C+D (docs/design/
 * ADV2_SPEC.md): the result reads like the approved sample (state chip, structure facts, areas with %, scenarios, what
 * to watch); a result stored before ADV-2 shows only the sections it has, never a placeholder. Mocked API, made-up data.
 * CA_Q1_SHOTS / ADV2_SHOTS: folders for the screenshots (deviceScaleFactor 2).
 */
const SHOTS = process.env.CA_Q1_SHOTS;
const ADV2 = process.env.ADV2_SHOTS;
const PNG = drawnChartPng();

/** The made-up result moved to made-up NIFTY 50 1-hour prices. `adv2`: with the ADV-2 fields; else as stored before. */
function niftyHybrid(adv2: boolean): HybridResult {
  const h = adv2 ? fixtureHybrid(false) : fixtureHybridOld(false);
  const reading = { ...h.reading, symbol: "NIFTY 50", last_close: 24101, protected: 23980, levels: [{ kind: "swing high" as const, price: 24260 }, { kind: "swing low" as const, price: 23930 }],
    last_break: h.reading.last_break ? { ...h.reading.last_break, price: 24150 } : null,
    // the checklist's facts carry the same prices as the structure above (owner review of the proof sheet)
    checklist: h.reading.checklist.map((c) => (c.name === "Protected level" ? { ...c, reason: "23,980.00" }
      : c.name === "Last break of structure" ? { ...c, reason: "Swing break at 24,150.00" } : c)),
    table: h.reading.table.map((row) => (row.tf === "1h" ? { ...row, protected: 23980, last_break: row.last_break ? { ...row.last_break, price: 24150 } : null }
      : row.tf === "15m" ? { ...row, protected: 24140, last_break: row.last_break ? { ...row.last_break, price: 24122 } : null } : row)),
    areas: [{ where: "above" as const, low: 24300, high: 24340, distance: 199, state: "fresh", timeframe: "1h", ...(adv2 ? { distance_pct: 0.83 } : {}) },
      { where: "below" as const, low: 23860, high: 23900, distance: 201, state: "tested", timeframe: "1d", ...(adv2 ? { distance_pct: 0.83 } : {}) }] };
  if (adv2) {
    Object.assign(reading, {
      bars_read: 480, source: "stored+broker", protected_distance_pct: 0.5,
      swings: [{ label: "LL", price: 23930, time: null }, { label: "HH", price: 24260, time: null }, { label: "HL", price: 23980, time: null }],
      state: { phase: "pullback", text: "Up · pullback running" },
      higher_degree: { trend: "sideways", text: "Daily range between 23,930.00 and 24,620.00" },
      scenarios: { main: "The pullback reaches 23,860.00 – 23,900.00; a handover there and a commitment candle continue the up structure toward 24,260.00.",
        other: "A 1-hour close below 23,980.00 ends the up structure (change of character); next area 23,860.00 – 23,900.00." },
      watch: [{ when: "Price at 23,860.00 – 23,900.00", meaning: "handover · commitment" }, { when: "1-hour close below 23,980.00", meaning: "structure changes" },
        { when: "Close above 24,260.00", meaning: "new HH" }],
    });
  }
  return { ...h, pick: { symbol: "NIFTY 50", timeframe: "1h" }, reading };
}

function hybridItem(adv2: boolean): AnalysisItem {
  const h = niftyHybrid(adv2);
  return { ...fixtureItem(8, null), mode: "hybrid", status: "done", result: null, hybrid: h, label: h.label, summary: { setup_status: null, trend: "up", symbol: "NIFTY 50" } };
}

async function open(page: Page, adv2 = false) {
  await page.clock.install({ time: new Date("2026-10-20T10:30:00+05:30") });
  await page.addInitScript(() => {
    try { localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" })); localStorage.setItem("atp_install_dismissed", "1"); } catch { /* none */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, chart_analyze: true, chart_analyze_hybrid: true } });
  await mockCharts(page, { market: "closed" });
  const item = hybridItem(adv2);
  await page.route(/\/api(?:\/v1)?\/chart-analyze\/.*/, async (route: Route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api(?:\/v1)?\/chart-analyze/, "");
    if (path === "/status") return route.fulfill({ json: fixtureStatus({ modes: { image_only: true, hybrid: true } }) });
    if (path === "/items") return route.fulfill({ json: { items: [item] } });
    if (path === "/items/8") return route.fulfill({ json: item });
    if (/\/items\/8\/(image|reading\.png)$/.test(path)) return route.fulfill({ body: PNG, contentType: "image/png" });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.goto("/chart-analyze");
  await page.getByRole("region", { name: "Your analyses" }).getByRole("button", { name: /NIFTY 50/ }).click();
  await expect(page.locator("[data-figure='atp'] canvas").first()).toBeVisible({ timeout: 15_000 });
}

async function noHorizontalScroll(page: Page) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over).toBeLessThanOrEqual(0);
}

for (const size of [{ width: 390, height: 844, phone: true }, { width: 1440, height: 900, phone: false }]) {
  test.describe(`Advanced analysis on ATP's chart ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: SHOTS || ADV2 ? 2 : 1 });

    test("the reading is drawn on ATP's own chart; the picture and the download stay behind Details", async ({ page }) => {
      await open(page);
      const atp = page.locator("[data-figure='atp']");
      const details = page.locator("[data-details='chart-analyze.reading-picture']");
      await expect(details.getByText("The picture and the download")).toBeVisible();
      await expect(details.locator("img")).toBeHidden();                   // the picture is behind Details until opened
      await expect(page.getByRole("button", { name: "Download image" })).toBeHidden();
      await details.locator("summary").click();
      await expect(details.locator("img")).toBeVisible();
      await expect(details.getByTestId("side-by-side-note")).toHaveText(/^ATP draws its reading on its own chart of NIFTY 50 · 1-hour/);
      await expect(page.getByRole("button", { name: "Download image" })).toBeVisible();
      const box = await atp.boundingBox();
      expect(box && box.width).toBeLessThanOrEqual(size.width);
      if (SHOTS) await atp.screenshot({ path: `${SHOTS}/q1-atp-chart-${size.width}.png` });
    });

    test("ADV-2: state chip, structure facts, areas with %, scenarios and what to watch", async ({ page }) => {
      await open(page, true);
      const result = page.getByTestId("hybrid-result");
      if (ADV2) await result.screenshot({ path: `${ADV2}/new-fields-${size.width}.png`, animations: "disabled" });
      await expect(page.getByTestId("hybrid-state")).toHaveText("Up · pullback running");
      await expect(page.getByTestId("hybrid-close")).toContainText("Close 24,101.00 · 480 1-hour candles read");
      const facts = page.getByTestId("hybrid-structure");
      await expect(facts.locator("[data-fact='swings']")).toContainText("LL 23,930.00");
      await expect(facts.locator("[data-fact='protected']")).toContainText("Protected low");
      await expect(facts.locator("[data-fact='protected']")).toContainText("0.50% below");
      await expect(page.locator("[data-testid='hybrid-table'] [data-tf]")).toHaveCount(2);   // Daily + 15-minute; Weekly not ready
      await expect(page.getByTestId("hybrid-areas").locator("[data-area]")).toHaveCount(2);
      await expect(page.getByTestId("hybrid-areas")).toContainText("0.83%");
      await expect(page.getByTestId("hybrid-scenarios")).toContainText("Main:");
      await expect(page.locator("[data-watch]")).toHaveCount(3);
      await expect(page.getByText(/data not ready|Not read yet|Not formed yet/i)).toHaveCount(0);
      const help = page.getByRole("button", { name: "Help: Higher degree" });
      const hb = await help.boundingBox();
      expect(hb && hb.width).toBeGreaterThan(0);
      await help.click();
      await expect(page.getByText("Daily range between 23,930.00 and 24,620.00")).toBeVisible();
      await noHorizontalScroll(page);
    });

    test("ADV-2: an old stored result shows only the sections it has", async ({ page }) => {
      await open(page, false);
      const result = page.getByTestId("hybrid-result");
      if (ADV2) await result.screenshot({ path: `${ADV2}/old-result-${size.width}.png`, animations: "disabled" });
      await expect(page.getByTestId("hybrid-state")).toHaveCount(0);
      await expect(page.locator("[data-block='hy-scenarios'], [data-block='hy-watch']")).toHaveCount(0);
      await expect(page.locator("[data-fact='trend']")).toContainText("Up (weak)");
      await expect(page.getByTestId("hybrid-areas")).toContainText("199.00 points");
      await expect(page.locator("[data-present='false']")).toHaveCount(0);
      await expect(page.getByText(/data not ready|Not read yet|Not formed yet/i)).toHaveCount(0);
      await noHorizontalScroll(page);
    });
  });
}
