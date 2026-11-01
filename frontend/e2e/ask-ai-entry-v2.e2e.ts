import { expect, test, type Page } from "@playwright/test";
import { api, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * CT-1 C10: ONE "Ask AI" entry in the shell's top bar. The page on screen hands it its symbol / timeframe, and each flag
 * gates what it gated before: `read_chart` the reading, `chart_analyze` the image analysis, `ai_copilot` the Copilot.
 * The Charts toolbar has no Read / Analyze buttons of its own any more (their code stays for the page outside the shell).
 */
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ALL = { ui_v2: true, chart_workspace: true, read_chart: true, chart_analyze: true, ai_copilot: true };

const READ = {
  instrument: "NSE:NIFTY", tf: "15m", bars: 400, as_of: "2026-10-19T05:00:00+00:00", last_bar: null, answer_key: null,
  note: "A reading of past prices in structure words - reference, not advice.",
  structure: { direction: "up", words: "Structure on the 15-minute chart: up.", last_event: null, protected: null },
  areas: [], pullback: { available: false, trend: null, phase: null, counter: null, as_of: null, words: "" }, commitment: null,
  lines: ["Structure on the 15-minute chart: up."],
};

async function open(page: Page, path: string, features: Record<string, boolean>) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts"]));
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features, brokers: CONNECTED });
  await mockCharts(page, {});
  const reads: string[] = [];
  await page.route(api("/charts/read"), (r) => { reads.push(new URL(r.request().url()).search); return r.fulfill({ json: READ }); });
  await page.goto(path);
  return reads;
}

test.describe("CT-1 C10 one Ask AI entry, 390 x 844", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("Charts: the toolbar has no Read / Analyze; Ask AI reads the chart and hides the reading", async ({ page }) => {
    const reads = await open(page, "/charts", ALL);
    await expect(page.locator("[data-page='charts-v2'] .tv-lightweight-charts canvas").first()).toBeVisible();
    await expect(page.getByTestId("read-chart-button")).toHaveCount(0);
    await expect(page.locator("[data-ask-ai-open]")).toHaveCount(1);
    await page.locator("[data-ask-ai-open]").click();
    const sheet = page.getByRole("dialog", { name: "Ask AI" });
    await expect(sheet.locator("[data-ask-context]")).toContainText("NIFTY 50");
    await expect(sheet.locator("[data-ask-option]")).toHaveCount(3);       // read, analyze, copilot
    await expect(sheet.getByRole("link", { name: /Ask the Copilot about NIFTY 50/ })).toHaveAttribute("href", "/copilot?symbol=NIFTY%2050");
    await expectPhoneLayout(page);
    await sheet.locator("[data-ask-option='read']").click();
    await expect(sheet).toHaveCount(0);
    await expect(page.getByTestId("read-chart")).toContainText("Structure on the 15-minute chart: up.");
    expect(reads.length).toBeGreaterThan(0);
    await page.getByTestId("read-chart-hide").click();
    await expect(page.getByTestId("read-chart")).toHaveCount(0);
  });

  test("each flag gates its option; with none on, Charts has no Ask AI", async ({ page }) => {
    await open(page, "/charts", { ui_v2: true, chart_workspace: true, chart_analyze: true });
    await page.locator("[data-ask-ai-open]").click();
    const sheet = page.getByRole("dialog", { name: "Ask AI" });
    await expect(sheet.locator("[data-ask-option]")).toHaveCount(1);
    await expect(sheet.locator("[data-ask-option='analyze']")).toHaveAttribute("href", "/chart-analyze");
    await page.keyboard.press("Escape");
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await open(page, "/charts", { ui_v2: true, chart_workspace: true });
    await expect(page.getByRole("toolbar", { name: "Chart controls" })).toBeVisible();
    await expect(page.locator("[data-ask-ai-open]")).toHaveCount(0);
  });

  test("Today: the Copilot and the image analysis, no page context", async ({ page }) => {
    await open(page, "/", ALL);
    await page.locator("[data-ask-ai-open]").click();
    const sheet = page.getByRole("dialog", { name: "Ask AI" });
    await expect(sheet.locator("[data-ask-context]")).toHaveCount(0);
    await expect(sheet.getByRole("link", { name: /Open the Copilot/ })).toHaveAttribute("href", "/copilot");
    await expect(sheet.locator("[data-ask-option='read']")).toHaveCount(0);
    await sheet.getByRole("link", { name: /Open the Copilot/ }).click();
    // M75 NAV-2: the Copilot opens in place over Today (the path stays; the panel is in the address).
    await expect(page.locator("[data-inplace-panel='copilot']")).toBeVisible();
    await expect(page).toHaveURL(/\/\?panel=copilot$/);
  });
});
