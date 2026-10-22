import { expect, test, type Page } from "@playwright/test";
import { F, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * Handover item 1: the Large and Extra large text sizes keep every layout rule - on a phone no sideways scroll, 44 px
 * targets and nothing behind the bottom bar; at 1440 no sideways scroll - and no figure spills out of its box. Normal is
 * covered by every other spec's baselines. Made-up data only (e2e/fixtures/dashboard_v2.json).
 */
async function open(page: Page, size: "large" | "xlarge") {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((s) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light", textSize: s }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, size);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: F.features as Record<string, boolean>, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { state: "connected", me: true });
  // The user's stored size (the server's copy wins on sign-in, so it must agree with the browser's).
  await page.route(/\/api(?:\/v1)?\/me\/prefs$/, (r) => r.fulfill({ json: {
    prefs: { chart: {}, setup: {}, oc_analyses: { items: {} }, appearance: { text_size: size } },
    versions: { chart: 0, setup: 0, oc_analyses: 0, appearance: 1 } } }));
  await page.goto("/");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[data-page='dashboard-v2']")).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-text-size", size);
}

async function spills(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    for (const e of document.querySelectorAll("[data-block] *")) {
      if (e.closest("[aria-label^='What is']") || e.querySelector("[aria-label^='What is']")) continue;
      if (e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX === "visible" && e.clientWidth > 0) {
        out.push(`${e.tagName}:${(e.textContent ?? "").trim().slice(0, 20)}`);
      }
    }
    return out;
  });
}

for (const size of ["large", "xlarge"] as const) {
  test.describe(`text size ${size}`, () => {
    test.describe("phone 390", () => {
      test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
      test("the layout rules hold and nothing spills", async ({ page }) => {
      await open(page, size);
      await expectPhoneLayout(page);
      expect(await spills(page)).toEqual([]);
      });
    });

    test("desktop 1440: no sideways scroll, nothing spills", async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await open(page, size);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      expect(await spills(page)).toEqual([]);
    });
  });
}
