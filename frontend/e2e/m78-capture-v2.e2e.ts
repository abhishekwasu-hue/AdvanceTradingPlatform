import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { madeUpBars, mockChartReading, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { mockCopilotNumbers } from "./copilotMocks";

/**
 * M78 (band label legibility): capture-only, like m75w1-capture-v2: the before / after shots of
 * docs/design/M78_COMPARE.html - the Copilot answer chart and the Charts tab reading, 390 and 1440, light and dark - and
 * one phone shot with a band's price tooltip open (F5). The Copilot answer's area carries its timeframe and state, as the
 * server now sends them (F3). No toHaveScreenshot, so nothing here becomes a baseline; never runs in CI. By hand:
 * `M78_OUT=<folder> npx playwright test --project=phone e2e/m78-capture-v2.e2e.ts` against a build.
 */
test.skip(!!process.env.CI, "capture-only: run by hand for the M78 comparison page, never in CI");
const OUT = process.env.M78_OUT ?? "test-results/m78";
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as {
  home: Record<string, unknown>; answers: Record<string, Record<string, unknown>>;
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

function range(tf: string) {
  const bars = madeUpBars(tf);
  const lo = Math.min(...bars.map((b) => b.low)), hi = Math.max(...bars.map((b) => b.high));
  return { lo, hi, last: bars.at(-1)!.close };
}

async function prep(page: Page, theme: string, features: Record<string, boolean>) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features, brokers: CONNECTED });
  await mockCharts(page, {});
}

for (const [w, h] of [[390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`${w} ${theme}`, () => {
      test.use({ viewport: { width: w, height: h }, deviceScaleFactor: 1, ...(w === 390 ? { isMobile: true, hasTouch: true } : {}) });

      test(`copilot answer chart ${w} ${theme}`, async ({ page }) => {
        await prep(page, theme, { ui_v2: true, ai_copilot: true });
        await mockCopilotNumbers(page);
        await mockCharts(page, {});                                          // the made-up bars, registered last so they answer
        const r = range("day");
        const base = F.answers["Read the SENSEX chart"] as { headline: Record<string, unknown> };
        // the area just under the last bars (where the view controls sit)
        const from = Math.round(r.lo + (r.hi - r.lo) * 0.02), to = Math.round(r.lo + (r.hi - r.lo) * 0.12);
        const answer = { ...base, headline: { ...base.headline, area: { side: "supply", from, to, where: "below", distance: 10, distance_pct: 0.1,
                                                                       timeframe: "1d", state: "fresh" } } };
        await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, async (route) => {
          const q = route.request();
          if (q.url().endsWith("/history")) return route.fulfill({ json: { turns: [], keep: 20 } });
          if (q.method() === "GET") return route.fulfill({ json: F.home });
          return route.fulfill({ json: answer });
        });
        await page.goto("/copilot");
        await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
        await page.locator("#copilot-question").fill("Read the SENSEX chart");
        await page.locator("#copilot-question").press("Enter");
        const chart = page.locator("[data-mini-chart='SENSEX']");
        await expect(chart.locator("canvas").first()).toBeVisible({ timeout: 15_000 });
        await page.waitForTimeout(800);
        await chart.evaluate((el) => el.scrollIntoView({ block: "center" }));
        await page.waitForTimeout(300);
        await chart.screenshot({ path: `${OUT}/copilot-${w}-${theme}.png`, animations: "disabled" });
      });

      test(`charts tab reading ${w} ${theme}`, async ({ page }) => {
        await prep(page, theme, { ui_v2: true, chart_workspace: true, read_chart: true, chart_analyze: true, ai_copilot: true });
        await mockChartReading(page);
        await page.goto("/charts");
        await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
        await expect(page.locator("[data-page='charts-v2'] .tv-lightweight-charts canvas").first()).toBeVisible({ timeout: 15_000 });
        await page.locator("[data-ask-ai-open]").click();
        await page.getByRole("dialog", { name: "Ask AI" }).locator("[data-ask-option='read']").click();
        await expect(page.getByTestId("read-chart")).toBeVisible();
        await page.waitForTimeout(800);
        const chart = page.locator("[data-page='charts-v2'] [data-chart-theme]").first();
        await chart.screenshot({ path: `${OUT}/charts-${w}-${theme}.png`, animations: "disabled" });
      });
    });
  }
}

test.describe("390 dark tooltip", () => {
  test.use({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true });

  test("charts tab reading 390 dark: a tap on a band opens its prices in place", async ({ page }) => {
    await prep(page, "dark", { ui_v2: true, chart_workspace: true, read_chart: true, chart_analyze: true, ai_copilot: true });
    await mockChartReading(page);
    await page.goto("/charts");
    await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
    await expect(page.locator("[data-page='charts-v2'] .tv-lightweight-charts canvas").first()).toBeVisible({ timeout: 15_000 });
    await page.locator("[data-ask-ai-open]").click();
    await page.getByRole("dialog", { name: "Ask AI" }).locator("[data-ask-option='read']").click();
    await expect(page.getByTestId("read-chart")).toBeVisible();
    await page.waitForTimeout(800);
    const chart = page.locator("[data-page='charts-v2'] [data-chart-theme]").first();
    const box = await chart.boundingBox();
    if (!box) throw new Error("no chart");
    const tip = chart.locator("[data-band-tip]");
    // tap down the middle of the plot until a band answers (the bands' pixels depend on the made-up bars)
    for (let y = 30; y < box.height * 0.6 && !(await tip.isVisible()); y += 8) {
      await page.touchscreen.tap(box.x + box.width * 0.45, box.y + y);
      await page.waitForTimeout(120);
    }
    await expect(tip).toBeVisible();
    await expect(tip).toContainText("Top");
    await chart.screenshot({ path: `${OUT}/tooltip-390-dark.png`, animations: "disabled" });
    await page.keyboard.press("Escape");
    await expect(tip).toBeHidden();
  });
});
