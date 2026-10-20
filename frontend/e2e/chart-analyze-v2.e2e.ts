import { expect, test, type Page, type Route } from "@playwright/test";
import { deflateSync } from "node:zlib";
import { fixtureItem, fixtureStatus } from "../src/chartAnalyze/fixtures";
import type { SetupStatus } from "../src/chartAnalyze/types";
import { mockApi, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Analyze a chart (flag chart_analyze; docs/design/CHART_ANALYZE.md), in the v2 shell, against mocked API answers
 * built from made-up fixtures (src/chartAnalyze/fixtures.ts). The chart image is DRAWN here in code - a grid and
 * made-up candles - never a real chart (public repository).
 * - Baselines `chart-analyze-{390,360,1440}-{light,dark}.png`: the page with an Image-only result (full page).
 * - The flow: upload -> Analyze -> the result blocks; an unreadable link -> "Please upload a screenshot instead";
 *   "Alert me when this happens" -> an ordinary alert rule; the annotated PNG export opens (decoded in the browser).
 * - Flag off: no page, no Charts-page button. Phone: no sideways scroll, 44 px targets, nothing behind the bottom bar.
 * After an intended change: `npx playwright test --project=phone e2e/chart-analyze-v2.e2e.ts --update-snapshots`.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, chart_analyze: true }, brokers: CONNECTED };
const OFF: MockOptions = { features: { ui_v2: true }, brokers: CONNECTED };

// --- a drawn placeholder PNG (no real chart) ---------------------------------------------------------------------------

const CRC = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
const crc32 = (buf: Buffer) => { let c = 0xffffffff; for (const b of buf) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
function chunk(type: string, data: Buffer): Buffer {
  const head = Buffer.alloc(8); head.writeUInt32BE(data.length, 0); head.write(type, 4, "latin1");
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
  return Buffer.concat([head, data, crc]);
}
function drawnChartPng(width = 640, height = 360): Buffer {
  const row = width * 3 + 1;
  const px = Buffer.alloc(row * height, 0);
  const set = (x: number, y: number, rgb: [number, number, number]) => { if (x >= 0 && y >= 0 && x < width && y < height) px.set(rgb, y * row + 1 + x * 3); };
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) set(x, y, x % 64 === 0 || y % 60 === 0 ? [228, 231, 236] : [250, 250, 250]);
  let level = height / 2;
  for (let i = 0; i < 20; i++) {
    const x0 = 20 + i * 30, top = Math.round(level - 25 + ((i * 37) % 11)), bottom = Math.round(level + 12);
    const colour: [number, number, number] = i % 3 ? [22, 150, 92] : [214, 56, 56];
    for (let y = top - 8; y < bottom + 8; y++) set(x0 + 6, y, [60, 60, 60]);
    for (let y = top; y < bottom; y++) for (let x = x0; x < x0 + 12; x++) set(x, y, colour);
    level += i < 12 ? -6 : 7;
  }
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4); ihdr.set([8, 2, 0, 0, 0], 8);
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", ihdr), chunk("IDAT", deflateSync(px)), chunk("IEND", Buffer.alloc(0))]);
}
const PNG = drawnChartPng();

// --- the mocked chart-analyze API ---------------------------------------------------------------------------------------

interface Calls { adds: number; analyzes: number; alerts: unknown[] }

async function mockChartAnalyze(page: Page, setup: SetupStatus, { history = true, unreadable = false } = {}): Promise<Calls> {
  const calls: Calls = { adds: 0, analyzes: 0, alerts: [] };
  const past = history ? [fixtureItem(7, "none", "2026-10-19T08:15:00+00:00"), fixtureItem(6, null, "2026-10-18T11:05:00+00:00")] : [];
  let added = false;
  await page.route(/\/api(?:\/v1)?\/alerts\/rules$/, async (route: Route) => {
    calls.alerts.push(route.request().postDataJSON());
    await route.fulfill({ status: 201, json: { id: 99, ...(route.request().postDataJSON() as object) } });
  });
  await page.route(/\/api(?:\/v1)?\/chart-analyze\/.*/, async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api(?:\/v1)?\/chart-analyze/, "");
    const method = route.request().method();
    if (path === "/status") return route.fulfill({ json: fixtureStatus({ limits: { ...fixtureStatus().limits, daily_used: added ? 2 : 1 } }) });
    if (path === "/items" && method === "GET") return route.fulfill({ json: { items: added ? [fixtureItem(8, setup), ...past] : past } });
    if (path === "/items" && method === "POST") {
      calls.adds += 1;
      if (unreadable) {
        return route.fulfill({ status: 422, json: { detail: { message: "Please upload a screenshot instead", code: "link_unreadable",
          reason: "the link opens a web page, not an image (a login page or a live chart)" } } });
      }
      added = true;
      return route.fulfill({ status: 201, json: fixtureItem(8, null) });
    }
    if (/^\/items\/\d+\/image$/.test(path)) return route.fulfill({ body: PNG, contentType: "image/png" });
    if (path === "/items/8/analyze") { calls.analyzes += 1; return route.fulfill({ json: fixtureItem(8, setup) }); }
    const one = /^\/items\/(\d+)$/.exec(path);
    if (one && method === "GET") return route.fulfill({ json: fixtureItem(Number(one[1]), Number(one[1]) === 6 ? null : "none") });
    if (one && method === "DELETE") return route.fulfill({ json: { deleted: Number(one[1]) } });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  return calls;
}

async function open(page: Page, path: string, theme: "dark" | "light", options: MockOptions = ON) {
  await page.clock.install({ time: new Date("2026-10-20T10:30:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, options);
}

async function settle(page: Page) {
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
}

async function analyzeUpload(page: Page) {
  await page.getByTestId("file-input").setInputFiles({ name: "drawn-chart.png", mimeType: "image/png", buffer: PNG });
  await expect(page.getByAltText("The chart you picked")).toBeVisible();
  await page.getByRole("button", { name: "Analyze", exact: true }).click();
  await expect(page.getByTestId("analysis-result")).toBeVisible();
}

const SIZES = [{ width: 390, height: 844, phone: true }, { width: 360, height: 800, phone: true }, { width: 1440, height: 900, phone: false }];
const THEMES = ["light", "dark"] as const;

for (const size of SIZES) {
  test.describe(`Analyze a chart ${size.width} x ${size.height}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`the page with an Image-only result looks the same (${theme})`, async ({ page }) => {
        await open(page, "/chart-analyze", theme);
        await mockChartAnalyze(page, "forming");
        await page.goto("/chart-analyze");
        await analyzeUpload(page);
        await page.getByRole("button", { name: "Close this explanation" }).click().catch(() => undefined);
        await settle(page);
        await expect(page.getByTestId("image-only-label")).toHaveText("Image-only analysis: prices and volume are read from the picture and may be approximate");
        await expect(page).toHaveScreenshot(`chart-analyze-${size.width}-${theme}.png`, { fullPage: true, animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 20_000 });
      });
    }

    if (size.phone) {
      test("phone layout: no sideways scroll, 44 px targets, nothing behind the bottom bar (before and after a result)", async ({ page }) => {
        await open(page, "/chart-analyze", "dark");
        await mockChartAnalyze(page, "formed");
        await page.goto("/chart-analyze");
        await settle(page);
        await expectPhoneLayout(page);
        await analyzeUpload(page);
        await expectPhoneLayout(page);
        // Phone: original and annotated are two tabs
        await expect(page.getByRole("tab", { name: "Annotated" })).toBeVisible();
        await page.getByRole("tab", { name: "Original" }).click();
        await expect(page.getByAltText("Your chart as uploaded")).toBeVisible();
      });
    }
  });
}

test.describe("Analyze a chart: the flow", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const setup of ["formed", "forming", "none"] as const) {
    test(`a ${setup} setup shows every block, the label and the disclaimer, and no order button`, async ({ page }) => {
      await open(page, "/chart-analyze", "light");
      await mockChartAnalyze(page, setup);
      await page.goto("/chart-analyze");
      await analyzeUpload(page);
      const result = page.getByTestId("analysis-result");
      for (const title of ["The chart, annotated", "The story", "What the method sees", "Evidence", "Scenarios", "Higher timeframes", "Key levels"]) {
        await expect(result.getByRole("heading", { name: title, exact: true })).toBeVisible();
      }
      await expect(result.getByRole("heading", { name: "What to wait for" })).toHaveCount(setup === "formed" ? 0 : 1);
      await expect(page.getByTestId("disclaimer")).toContainText("not investment advice");
      await expect(page.getByTestId("setup-status")).toHaveText(setup === "formed" ? "Formed" : setup === "forming" ? /^Forming — waiting for/ : "No setup");
      await expect(page.getByRole("button", { name: /buy|sell|order|trade/i })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Open in Chart tab" })).toHaveCount(0);
      await expect(page.getByRole("radio", { name: /Hybrid/ })).toBeDisabled();
    });
  }

  test("an unreadable link says 'Please upload a screenshot instead'", async ({ page }) => {
    await open(page, "/chart-analyze", "light");
    const calls = await mockChartAnalyze(page, "formed", { unreadable: true });
    await page.goto("/chart-analyze");
    await page.getByLabel("Or paste a chart link").fill("https://www.tradingview.com/chart/AbCd1234/");
    await page.getByRole("button", { name: "Analyze", exact: true }).click();
    const alert = page.getByRole("alert");
    await expect(alert).toContainText("Please upload a screenshot instead");
    await expect(alert).toContainText("a login page or a live chart");
    expect(calls.adds).toBe(1);
    expect(calls.analyzes).toBe(0);
  });

  test("'Alert me when this happens' creates an ordinary price alert, with the approximate-price note", async ({ page }) => {
    await open(page, "/chart-analyze", "light");
    const calls = await mockChartAnalyze(page, "forming");
    await page.goto("/chart-analyze");
    await analyzeUpload(page);
    await page.getByRole("button", { name: "Alert me when this happens" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("Prices read from a picture are approximate");
    await expect(dialog).toContainText("close > 120.5");
    await dialog.getByRole("button", { name: "Save alert" }).click();
    await expect(page.getByText("Alert created for DEMO: close > 120.5")).toBeVisible();
    expect(calls.alerts).toEqual([expect.objectContaining({ kind: "instrument", symbol: "DEMO", condition: "close > 120.5", base_tf: "15m" })]);
  });

  test("'Set an alert on this area' uses the area's price band", async ({ page }) => {
    await open(page, "/chart-analyze", "light");
    const calls = await mockChartAnalyze(page, "formed");
    await page.goto("/chart-analyze");
    await analyzeUpload(page);
    await page.getByRole("button", { name: "Set an alert on this area" }).click();
    await expect(page.getByRole("dialog")).toContainText("close >= 98 and close <= 101");
    await page.getByRole("dialog").getByRole("button", { name: "Save alert" }).click();
    await expect.poll(() => calls.alerts.length).toBe(1);
  });

  test("the annotated PNG export opens: a real PNG with the image, the overlay and the caption band", async ({ page }) => {
    await open(page, "/chart-analyze", "light");
    await mockChartAnalyze(page, "formed");
    await page.goto("/chart-analyze");
    await analyzeUpload(page);
    const downloading = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download / share the result image" }).click();
    const download = await downloading;
    expect(download.suggestedFilename()).toBe("chart-analysis-8.png");
    const stream = await download.createReadStream();
    const parts: Buffer[] = [];
    for await (const part of stream) parts.push(part as Buffer);
    const bytes = Buffer.concat(parts);
    expect(bytes.subarray(0, 8)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
    const size = await page.evaluate(async (b64) => {
      const blob = await (await fetch(`data:image/png;base64,${b64}`)).blob();
      const bitmap = await createImageBitmap(blob);
      return { width: bitmap.width, height: bitmap.height };
    }, bytes.toString("base64"));
    expect(size.width).toBe(640);
    expect(size.height).toBeGreaterThan(360);                         // the image plus the label and disclaimer band
  });

  test("delete asks first, then removes the analysis", async ({ page }) => {
    await open(page, "/chart-analyze", "light");
    await mockChartAnalyze(page, "formed");
    await page.goto("/chart-analyze");
    await analyzeUpload(page);
    await page.getByRole("button", { name: "Delete", exact: true }).first().click();
    const dialog = page.getByRole("dialog", { name: "Delete this analysis?" });
    await expect(dialog).toContainText("cannot be brought back");
    await dialog.getByRole("button", { name: "Delete" }).click();
    await expect(page.getByTestId("analysis-result")).toHaveCount(0);
    await expect(page.getByText("Deleted: the image and its analysis are gone.")).toBeVisible();
  });

  test("history: a past analysis opens again", async ({ page }) => {
    await open(page, "/chart-analyze", "light");
    await mockChartAnalyze(page, "formed");
    await page.goto("/chart-analyze");
    const history = page.getByRole("region", { name: "Your analyses" });
    await history.getByRole("button", { name: /DEMO/ }).click();
    await expect(page.getByTestId("setup-status")).toHaveText("No setup");
  });

  test("the Charts page has the button while the flag is on, and nothing while it is off", async ({ page }) => {
    await open(page, "/charts", "light");
    await mockChartAnalyze(page, "formed");
    await page.goto("/charts");
    await page.getByRole("link", { name: "Analyze a chart" }).click();
    await expect(page).toHaveURL(/\/chart-analyze$/);
    await expect(page.getByTestId("chart-analyze-page")).toBeVisible();
  });

  test("flag off: no Charts-page button and the page is not there", async ({ page }) => {
    await open(page, "/charts", "light", OFF);
    await page.goto("/charts");
    await expect(page.getByRole("button", { name: "Open" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Analyze a chart" })).toHaveCount(0);
    await page.goto("/chart-analyze");
    await expect(page.getByText("Page not found")).toBeVisible();
    await expect(page.getByTestId("chart-analyze-page")).toHaveCount(0);
  });
});
