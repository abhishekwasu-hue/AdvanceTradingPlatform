import { expect, test, type Page, type Route } from "@playwright/test";
import { deflateSync } from "node:zlib";
import { fixtureItem, fixtureSharedResult, fixtureStatus, fixtureStrikes } from "../src/chartAnalyze/fixtures";
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
 * - PR-A4: "Share with my organisation" (flag chart_analyze_share) and the structure-only copy a link opens
 *   (`/chart-analyze?shared=<link>`; baselines `chart-analyze-shared-{390,360,1440}-{light,dark}.png`); history filters.
 * After an intended change: `npx playwright test --project=phone e2e/chart-analyze-v2.e2e.ts --update-snapshots`.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, chart_analyze: true, chart_analyze_strikes: true }, brokers: CONNECTED };
const SHARE_ON: MockOptions = { ...ON, features: { ...ON.features, chart_analyze_share: true } };
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

interface Calls { adds: number; analyzes: number; alerts: unknown[]; shares: string[] }
const SHARED_TOKEN = "made-up-link.0000";

async function mockChartAnalyze(page: Page, setup: SetupStatus, { history = true, unreadable = false, imageGone = false, linkProblem = "" } = {}): Promise<Calls> {
  const calls: Calls = { adds: 0, analyzes: 0, alerts: [], shares: [] };
  let link: { token: string; expires_at: string } | null = null;
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
    if (/^\/items\/\d+\/image$/.test(path)) {
      if (imageGone) return route.fulfill({ status: 410, json: { detail: { message: "The chart image is no longer stored.", code: "image_gone" } } });
      return route.fulfill({ body: PNG, contentType: "image/png" });
    }
    if (/^\/items\/\d+\/strikes$/.test(path)) {
      const typed = url.searchParams.get("symbol");
      if (setup === "none") return route.fulfill({ json: { ...fixtureStrikes(), side: "none", items: [], message: "No setup is formed or forming, so there are no strikes to work out." } });
      return route.fulfill({ json: typed ? { ...fixtureStrikes(), symbol: typed.toUpperCase() } : fixtureStrikes() });
    }
    if (path === "/items/8/share") {
      calls.shares.push(method);
      link = method === "POST" ? { token: SHARED_TOKEN, expires_at: "2026-10-27T05:00:00+00:00" } : null;
      return route.fulfill({ json: { ...fixtureItem(8, setup), share: link } });
    }
    if (path.startsWith("/shared/")) {
      if (linkProblem === "link_expired") return route.fulfill({ status: 410, json: { detail: { message: "expired", code: "link_expired" } } });
      if (linkProblem) return route.fulfill({ status: 404, json: { detail: { message: "gone", code: "link_gone" } } });
      return route.fulfill({ json: { result: fixtureSharedResult(), mode: "image_only", expires_at: "2026-10-27T05:00:00+00:00", is_yours: false,
        disclaimer: fixtureSharedResult().disclaimer } });
    }
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

    for (const theme of THEMES) {
      test(`the shared copy (structure only) looks the same (${theme})`, async ({ page }) => {
        await open(page, "/chart-analyze", theme, SHARE_ON);
        await mockChartAnalyze(page, "forming");
        await page.goto(`/chart-analyze?shared=${SHARED_TOKEN}`);
        await expect(page.getByTestId("structure-sketch")).toBeVisible();
        await settle(page);
        await expect(page).toHaveScreenshot(`chart-analyze-shared-${size.width}-${theme}.png`, { fullPage: true, animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 20_000 });
      });
    }

    if (size.phone) {
      test("phone layout of the shared copy and of the share dialog", async ({ page }) => {
        await open(page, "/chart-analyze", "light", SHARE_ON);
        await mockChartAnalyze(page, "forming");
        await page.goto(`/chart-analyze?shared=${SHARED_TOKEN}`);
        await expect(page.getByTestId("structure-sketch")).toBeVisible();
        await settle(page);
        await expectPhoneLayout(page);
        await page.goto("/chart-analyze");
        await analyzeUpload(page);
        await page.getByTestId("share-button").click();
        await page.getByRole("dialog").getByRole("button", { name: "Create link" }).click();
        await expect(page.getByTestId("share-link")).toBeVisible();
        // The goto above dropped the font style tag of the first settle(): without it the dialog renders in whatever
        // "Inter" / sans-serif the machine has (installed here, missing on the CI runner), so the baseline only matched
        // one machine. Settle again so this screenshot uses the same fixed font as every other baseline.
        await settle(page);
        // The link holds the test server's address (it differs between runs), so it is masked.
        await expect(page).toHaveScreenshot(`chart-analyze-share-dialog-${size.width}-light.png`, { mask: [page.getByTestId("share-link")], animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 20_000 });
      });

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

  test("option strikes: flagged 'From your settings', worked out from the user's own setting", async ({ page }) => {
    await open(page, "/chart-analyze", "light");
    await mockChartAnalyze(page, "forming");
    await page.goto("/chart-analyze");
    await analyzeUpload(page);
    const block = page.getByRole("region", { name: /Option strikes/ });
    await expect(block.getByTestId("from-settings")).toHaveText("From your settings");
    await expect(block).toContainText("The analysis does not pick strikes");
    await expect(block.getByTestId("strikes-item")).toContainText("Bull put spread · main strike 1 strike out of the money");
    await expect(block.getByRole("cell", { name: "≈ 115" })).toBeVisible();
    await expect(block.getByRole("button", { name: /buy|sell|order|trade/i })).toHaveCount(0);
  });

  test("option strikes: hidden, and never asked for, while chart_analyze_strikes is off", async ({ page }) => {
    await open(page, "/chart-analyze", "light", { ...ON, features: { ui_v2: true, chart_analyze: true } });
    const asked: string[] = [];
    page.on("request", (r) => { if (new URL(r.url()).pathname.endsWith("/strikes")) asked.push(r.url()); });
    await mockChartAnalyze(page, "forming");
    await page.goto("/chart-analyze");
    await analyzeUpload(page);
    await expect(page.getByTestId("analysis-result")).toBeVisible();
    await expect(page.getByRole("region", { name: /Option strikes/ })).toHaveCount(0);
    expect(asked).toEqual([]);
  });

  test("Download PDF prints the result alone: the blocks, the label and the disclaimer, without the buttons", async ({ page }) => {
    await open(page, "/chart-analyze", "light");
    await mockChartAnalyze(page, "formed");
    await page.goto("/chart-analyze");
    await analyzeUpload(page);
    // Catch the print frame as it is added and record what it would print instead of opening the dialog.
    await page.evaluate(() => {
      const w = window as unknown as { printed?: { text: string; theme: string; title: string } };
      new MutationObserver((records) => {
        for (const r of records) for (const n of Array.from(r.addedNodes)) {
          if (!(n instanceof HTMLIFrameElement) || !n.contentWindow) continue;
          const frame = n;
          (frame.contentWindow as Window).print = () => {
            const doc = frame.contentDocument as Document;
            w.printed = { text: doc.body.innerText, theme: doc.documentElement.dataset.theme ?? "", title: doc.title };
          };
        }
      }).observe(document.body, { childList: true });
    });
    await page.getByRole("button", { name: "Download PDF" }).click();
    const printed = await page.waitForFunction(() => (window as unknown as { printed?: unknown }).printed).then((h) => h.jsonValue()) as
      { text: string; theme: string; title: string };
    expect(printed.theme).toBe("light");
    expect(printed.title).toMatch(/^Chart analysis - DEMO 15m - /);
    for (const text of ["What the method sees", "Evidence", "Scenarios", "Key levels", "Option strikes", "From your settings", "not investment advice",
      "Image-only analysis"]) expect(printed.text, text).toContain(text);
    for (const text of ["Download PDF", "Download / share the result image", "Set an alert on this area"]) expect(printed.text, text).not.toContain(text);
  });

  test("a chart whose stored image is gone (410) says so, also on Download", async ({ page }) => {
    await open(page, "/chart-analyze", "light");
    await mockChartAnalyze(page, "formed", { imageGone: true });
    await page.goto("/chart-analyze");
    await page.getByRole("region", { name: "Your analyses" }).getByRole("button", { name: /DEMO/ }).click();
    await expect(page.getByText(/no longer stored/).first()).toBeVisible();
    await page.getByRole("button", { name: "Download / share the result image" }).click();
    await expect(page.getByRole("alert")).toContainText("no longer stored");
    await expect(page.getByTestId("disclaimer")).toBeVisible();
  });

  test("engine not installed: the note and the disabled Analyze agree", async ({ page }) => {
    await open(page, "/chart-analyze", "light");
    await mockChartAnalyze(page, "formed");
    await page.route(/\/api(?:\/v1)?\/chart-analyze\/status$/, (route) =>
      route.fulfill({ json: fixtureStatus({ engine: { installed: false, message: "Analysis engine not installed" } }) }));
    await page.goto("/chart-analyze");
    await expect(page.getByRole("status").filter({ hasText: "Analysis engine not installed" })).toContainText("cannot be analysed");
    await expect(page.getByRole("button", { name: "Analyze", exact: true })).toBeDisabled();
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

  test("share: the owner makes an organisation-only link, copies it and stops it; the dialog says what is left out", async ({ page }) => {
    await open(page, "/chart-analyze", "light", SHARE_ON);
    const calls = await mockChartAnalyze(page, "forming");
    await page.goto("/chart-analyze");
    await analyzeUpload(page);
    await page.getByRole("button", { name: "Share with my organisation" }).click();
    const dialog = page.getByRole("dialog", { name: "Share with my organisation" });
    await expect(dialog).toContainText("the chart's name, its prices, its dates and your picture are left out");
    await dialog.getByRole("button", { name: "Create link" }).click();
    await expect(dialog.getByTestId("share-link")).toHaveValue(new RegExp(`/chart-analyze\\?shared=${SHARED_TOKEN.replace(".", "\\.")}$`));
    await expect(dialog).toContainText("Open until");
    await dialog.getByRole("button", { name: "Stop sharing" }).click();
    await expect(dialog).toContainText("Not shared");
    expect(calls.shares).toEqual(["POST", "DELETE"]);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Share with my organisation" })).toBeVisible();
  });

  test("share: the shared copy shows the structure only - no name, no price, no picture, no buttons in the result", async ({ page }) => {
    await open(page, "/chart-analyze", "light", SHARE_ON);
    await mockChartAnalyze(page, "forming");
    await page.goto(`/chart-analyze?shared=${SHARED_TOKEN}`);
    const result = page.getByTestId("analysis-result");
    await expect(result).toHaveAttribute("data-shared", "true");
    await expect(page.getByTestId("shared-note")).toContainText("left out");
    await expect(result.locator("img")).toHaveCount(0);
    await expect(result.getByRole("button")).toHaveCount(0);
    const text = await result.innerText();
    expect(text).not.toMatch(/DEMO|NSE|15m|≈|\d{2,}/);
    for (const title of ["The structure, drawn", "The story", "What the method sees", "Evidence", "Scenarios", "What to wait for", "Higher timeframes", "Key levels"]) {
      await expect(result.getByRole("heading", { name: title, exact: true })).toBeVisible();
    }
    await expect(page.getByTestId("disclaimer")).toContainText("not investment advice");
    await page.getByRole("button", { name: "Your analyses" }).click();
    await expect(page).toHaveURL(/\/chart-analyze$/);
    await expect(page.getByTestId("chart-analyze-page")).toBeVisible();
  });

  for (const problem of ["link_gone", "link_expired"] as const) {
    test(`share: a ${problem === "link_gone" ? "stopped" : "expired"} link says so plainly`, async ({ page }) => {
      await open(page, "/chart-analyze", "light", SHARE_ON);
      await mockChartAnalyze(page, "forming", { linkProblem: problem });
      await page.goto(`/chart-analyze?shared=${SHARED_TOKEN}`);
      await expect(page.getByTestId("shared-problem")).toContainText(problem === "link_gone" ? "was stopped" : "has expired");
    });
  }

  test("share: no Share button while chart_analyze_share is off", async ({ page }) => {
    await open(page, "/chart-analyze", "light");
    await mockChartAnalyze(page, "forming");
    await page.goto("/chart-analyze");
    await analyzeUpload(page);
    await expect(page.getByTestId("analysis-result")).toBeVisible();
    await expect(page.getByTestId("share-button")).toHaveCount(0);
  });

  test("history: the filters show the user's analyses by what the method saw", async ({ page }) => {
    await open(page, "/chart-analyze", "light");
    await mockChartAnalyze(page, "formed");
    await page.goto("/chart-analyze");
    const history = page.getByRole("region", { name: "Your analyses" });
    await expect(history).toContainText("Each is kept for 30 days");
    await history.getByRole("button", { name: /^Not analysed/ }).click();
    await expect(history.getByRole("listitem")).toHaveCount(1);
    await history.getByRole("button", { name: /^Formed/ }).click();
    await expect(history).toContainText("None of your analyses match this filter.");
    await history.getByRole("button", { name: /^All/ }).click();
    await expect(history.getByRole("listitem")).toHaveCount(2);
  });

  test("flag off: no Charts-page button and the page is not there", async ({ page }) => {
    await open(page, "/charts", "light", OFF);
    await page.goto("/charts");
    await expect(page.getByRole("toolbar", { name: "Chart controls" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Analyze a chart" })).toHaveCount(0);
    await page.goto("/chart-analyze");
    await expect(page.getByText("Page not found")).toBeVisible();
    await expect(page.getByTestId("chart-analyze-page")).toHaveCount(0);
  });
});
