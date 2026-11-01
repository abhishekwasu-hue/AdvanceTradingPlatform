import { expect, test } from "@playwright/test";
import { mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";

/**
 * M75 NAV-3 (docs/design/M75_NO_NAVIGATION.md, row pages/ChartWindow.tsx): the classic single-chart window (`/?chart=`,
 * `ui_v2` off) lays the multi-chart workspace over itself in place - the address stays exactly as it was (no reload of
 * the window), and "Back to the chart" shows the same chart again. Made-up answers only.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`classic chart window ${size.width}`, () => {
    test.use({ viewport: size, deviceScaleFactor: 1 });

    test("Open in workspace opens in place; Back to the chart returns to the same chart and address", async ({ page }) => {
      await page.addInitScript(() => { try { localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ } });
      await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
      await mockApi(page, { features: { chart_workspace: true }, brokers: CONNECTED });
      await mockCharts(page);
      const start = "/?chart=NIFTY&tf=15min&exchange=NSE";
      await page.goto(start);
      const openWs = page.locator("[data-open-workspace]");
      await expect(openWs).toBeVisible({ timeout: 15_000 });
      await openWs.click();
      await expect(page.locator("[data-workspace-in-place]")).toBeVisible();
      expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe(start);
      await page.locator("[data-close-workspace]").click();
      await expect(page.locator("[data-workspace-in-place]")).toHaveCount(0);
      await expect(openWs).toBeVisible();
      expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe(start);
    });
  });
}
