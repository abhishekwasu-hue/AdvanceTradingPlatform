import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { mockCopilotNumbers } from "./copilotMocks";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Owner 13 Nov ("look at wrong area marking its a seller area"; "timeframe nivadlya nusar analysis"): capture-only, like
 * m72-pr5-capture-v2 - the Read chart answer before / after. The answers are the backend's own output (chart_reader on
 * a made-up snapshot: a daily buyer area holding the last close, the live price below it), written to the folder in
 * AREA_ROLE_PAYLOADS as before.json / after_1d.json / after_15m.json. No toHaveScreenshot, never runs in CI. By hand:
 * `AREA_ROLE_PAYLOADS=<dir> AREA_ROLE_OUT=<dir> npx playwright test --project=phone e2e/read-area-role-capture-v2.e2e.ts`.
 */
const DIR = process.env.AREA_ROLE_PAYLOADS;
test.skip(!!process.env.CI || !DIR, "capture-only: run by hand with AREA_ROLE_PAYLOADS, never in CI");
const OUT = process.env.AREA_ROLE_OUT ?? "test-results/read-area-role";
const F = JSON.parse(readFileSync(new URL("./fixtures/copilot_chat_v2.json", import.meta.url), "utf8")) as { home: Record<string, unknown> };

async function open(page: Page, payload: Record<string, unknown>) {
  await page.clock.install({ time: new Date("2026-10-19T12:15:00+05:30") });
  await page.addInitScript(() => {
    try { localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" })); localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  // a connected (data) broker, as on the owner's account: the live price exists and an intraday chart shows its own bars
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true }, brokers: [{ broker: "upstox", account_label: "primary", name: "Upstox", status: "connected", message: "", data_only: true, renew: false }] });
  await mockCopilotNumbers(page);
  await mockCharts(page, {});
  await page.route(/\/api(?:\/v1)?\/ai\/chat(\/home|\/history)?$/, async (route) => {
    const q = route.request();
    if (q.url().endsWith("/history")) return route.fulfill({ json: { turns: [], keep: 20 } });
    if (q.method() === "GET") return route.fulfill({ json: F.home });
    return route.fulfill({ json: { ai: { ready: false, title: null, line: null, settings: "/settings/ai", reason: null }, ...payload } });
  });
  await page.goto("/copilot");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.locator("#copilot-question").fill("Read the EEESTOCK chart");
  await page.locator("#copilot-question").press("Enter");
  await expect(page.locator("[data-answer='chart']")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("[data-mini-chart] canvas").first()).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(800);
}

for (const [w, h] of [[390, 844], [1440, 900]] as const) {
  test.describe(`${w}`, () => {
    test.use({ viewport: { width: w, height: h }, deviceScaleFactor: 1, ...(w === 390 ? { isMobile: true, hasTouch: true } : {}) });
    for (const name of ["before", "after_1d", "after_15m"] as const) {
      test(`read chart ${name} ${w}`, async ({ page }) => {
        const payload = JSON.parse(readFileSync(`${DIR}/${name}.json`, "utf8")) as Record<string, unknown>;
        await open(page, payload);
        const a = page.locator("[data-answer='chart']");
        if (name !== "before") {
          await expect(a.locator("[data-headline-live]")).toBeVisible();
          expect(await a.innerText()).not.toMatch(/(\d[\d,.]*) – \1\b/);          // never a zero-width "x – x" range
          if (w === 390) await expectPhoneLayout(page);
        }
        await page.screenshot({ fullPage: true, path: `${OUT}/${name}-${w}.png`, animations: "disabled" });
      });
    }
  });
}
