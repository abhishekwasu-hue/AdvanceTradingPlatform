import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { mockChartReading, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";

/**
 * M75 NAV-5..8 + W-2: capture-only (like m75nav4-capture): before / after shots for the comparison page - a click on
 * My positions (NAV-5b), the bell on Today (NAV-6b), Trading defaults' Risk limits (NAV-7) and the Copilot's Read the
 * chart tab (W-2), 390 and 1440, light and dark. No toHaveScreenshot (no baselines); never runs in CI. By hand:
 * `NAV58_OUT=<folder> npx playwright test --project=phone e2e/m75nav58-capture-v2.e2e.ts` against a build.
 */
test.skip(!!process.env.CI, "capture-only: run by hand for the M75 comparison page, never in CI");
const OUT = process.env.NAV58_OUT ?? "test-results/m75-nav58";
mkdirSync(OUT, { recursive: true });
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

async function prep(page: Page, theme: string) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, read_chart: true, copilot_workspace: true, paper_account: true }, brokers: CONNECTED });
  await mockCharts(page, {});
  await mockChartReading(page);
}

const FONT = "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }";
const FLOWS: { name: string; path: string; click: (p: Page) => ReturnType<Page["locator"]> }[] = [
  { name: "nav5-positions-orders", path: "/positions", click: (p) => p.locator("[data-block='history']").getByRole("link", { name: "Orders" }) },
  { name: "nav6-bell", path: "/", click: (p) => p.getByRole("link", { name: "Notifications" }).first() },
  { name: "nav7-defaults-risk", path: "/settings/trading-defaults", click: (p) => p.getByRole("link", { name: "Open Risk limits" }) },
];

for (const [w, h] of [[390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`${w} ${theme}`, () => {
      test.use({ viewport: { width: w, height: h }, deviceScaleFactor: 1, ...(w === 390 ? { isMobile: true, hasTouch: true } : {}) });
      for (const f of FLOWS) {
        test(`${f.name} ${w} ${theme}`, async ({ page }) => {
          await prep(page, theme);
          await page.goto(f.path);
          await page.addStyleTag({ content: FONT });
          const link = f.click(page);
          await expect(link).toBeVisible({ timeout: 15_000 });
          await link.click();
          await page.waitForTimeout(2000);
          const url = new URL(page.url());
          writeFileSync(`${OUT}/url-${f.name}-${w}-${theme}.txt`, `${url.pathname}${url.search}`);
          await page.screenshot({ path: `${OUT}/${f.name}-${w}-${theme}.png`, animations: "disabled" });
        });
      }
      test(`w2-read-the-chart ${w} ${theme}`, async ({ page }) => {
        await prep(page, theme);
        await page.goto("/copilot/read?symbol=NIFTY%2050");
        await page.addStyleTag({ content: FONT });
        await page.waitForTimeout(3500);
        writeFileSync(`${OUT}/url-w2-read-the-chart-${w}-${theme}.txt`, "/copilot/read?symbol=NIFTY%2050");
        await page.screenshot({ path: `${OUT}/w2-read-the-chart-${w}-${theme}.png`, animations: "disabled" });
      });
    });
  }
}
