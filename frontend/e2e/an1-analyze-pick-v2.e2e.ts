import { expect, test, type Page, type Route } from "@playwright/test";
import { fixtureStatus } from "../src/chartAnalyze/fixtures";
import { mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * AN-1 (owner 5 Nov, "Default yes, but 1. Give Both options as it is"): Analyze a chart from a symbol picked by search
 * (no picture: ATP draws its own chart; both modes stay), and "Analyze this chart" in the shell's Ask AI on Charts.
 * Mocked API only. AN1_SHOTS=<dir> writes the proof shots (no baselines).
 */
const SHOTS = process.env.AN1_SHOTS;
const CONNECTED = [{ broker: "upstox", account_label: "primary", name: "Upstox", status: "connected", message: "", data_only: true, renew: false }];
const FLAGS = { ui_v2: true, chart_workspace: true, chart_analyze: true, read_chart: true, ai_copilot: true };

async function open(page: Page, path: string, posts: { body: string }[] = []) {
  await page.clock.install({ time: new Date("2026-11-05T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts", "chart-analyze"]));
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: FLAGS, brokers: CONNECTED });
  await mockCharts(page, {});
  await page.route(/\/api(?:\/v1)?\/chart-analyze\/.*/, async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^\/api(?:\/v1)?\/chart-analyze/, "");
    if (p === "/status") {
      return route.fulfill({ json: { ...fixtureStatus({ modes: { image_only: true, hybrid: true } }), hybrid: { on: true, label: "Advanced analysis (code)",
        ai_part: false, note: "", message: null, timeframes: [{ id: "5m", label: "5-minute" }, { id: "15m", label: "15-minute" }, { id: "1h", label: "1-hour" },
          { id: "1d", label: "Daily" }] } } });
    }
    if (p === "/items" && route.request().method() === "POST") {
      posts.push({ body: route.request().postData() ?? "" });
      return route.fulfill({ status: 422, json: { detail: { message: "made-up stop after the request", code: "test" } } });
    }
    if (p === "/items") return route.fulfill({ json: { items: [] } });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.goto(path);
}

test.describe("AN-1 pick a symbol, 390 x 844", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: SHOTS ? 2 : 1 });

  test("Charts: Ask AI offers Analyze this chart with the chart's symbol and timeframe", async ({ page }) => {
    await open(page, "/charts");
    await expect(page.locator("[data-page='charts-v2'] .tv-lightweight-charts canvas").first()).toBeVisible();
    await page.locator("[data-ask-ai-open]").click();
    const sheet = page.getByRole("dialog", { name: "Ask AI" });
    const option = sheet.locator("[data-ask-option='analyze_this']");
    await expect(option).toContainText("Analyze this chart");
    await expect(option).toHaveAttribute("href", /\/chart-analyze\?symbol=NIFTY(\+|%20)50&tf=/);
    await expectPhoneLayout(page);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/an1-charts-askai-390-dark.png` });
  });

  test("Analyze a chart: the picked symbol and timeframe arrive filled; Analyze sends them without a picture", async ({ page }) => {
    const posts: { body: string }[] = [];
    await open(page, "/chart-analyze?symbol=NIFTY%2050&tf=15m", posts);
    const pick = page.getByTestId("atp-pick");
    await expect(pick).toBeVisible();
    await expect(page.getByTestId("atp-pick-note")).toContainText("ATP draws its own chart of NIFTY 50");
    await expect(page.getByTestId("mode-picker")).toBeVisible();                 // both modes stay
    await expectPhoneLayout(page);
    if (SHOTS) {
      await pick.scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${SHOTS}/an1-analyze-pick-390-dark.png` });
    }
    await page.getByRole("button", { name: "Analyze", exact: true }).click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0].body).toContain('name="symbol"');
    expect(posts[0].body).toContain("NIFTY 50");
    expect(posts[0].body).toContain('name="timeframe"');
    expect(posts[0].body).not.toContain('name="file"');
    expect(posts[0].body).not.toContain('name="link"');
  });
});

test.describe("ADV-3 AI study on ATP's reading, 390 x 844", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: SHOTS ? 2 : 1 });

  test("Advanced analysis shows the AI's whole study: state, setup, evidence, scenarios, what to watch", async ({ page }) => {
    const { fixtureHybrid, fixtureItem } = await import("../src/chartAnalyze/fixtures");
    const h = fixtureHybrid(true);
    const item = { ...fixtureItem(9, null), mode: "hybrid", status: "done", result: null, hybrid: h, label: h.label, summary: { setup_status: null, trend: "up", symbol: h.reading.symbol } };
    await open(page, "/chart-analyze");
    await page.unroute(/\/api(?:\/v1)?\/chart-analyze\/.*/);
    await page.route(/\/api(?:\/v1)?\/chart-analyze\/.*/, async (route: Route) => {
      const p = new URL(route.request().url()).pathname.replace(/^\/api(?:\/v1)?\/chart-analyze/, "");
      if (p === "/status") return route.fulfill({ json: fixtureStatus({ modes: { image_only: true, hybrid: true } }) });
      if (p === "/items") return route.fulfill({ json: { items: [item] } });
      if (p === "/items/9") return route.fulfill({ json: item });
      return route.fulfill({ status: 404, json: { detail: "Not found" } });
    });
    await page.reload();
    await page.getByRole("region", { name: "Your analyses" }).getByRole("button", { name: new RegExp(h.reading.symbol) }).click();
    const study = page.locator("[data-block='hy-ai']");
    await expect(study).toContainText("AI study of this structure");
    await expect(study.locator("[data-ai-setup]")).toBeVisible();
    await expect(study.locator("[data-ai-scenarios]")).toContainText("Main scenario");
    await expectPhoneLayout(page);
    if (SHOTS) { await study.scrollIntoViewIfNeeded(); await study.screenshot({ path: `${SHOTS}/adv3-ai-study-390-dark.png` }); }
  });
});
