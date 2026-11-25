import { expect, test } from "@playwright/test";
import { openSettings } from "./settingsMocks";

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

test("breakout settings save and reload without enabling the worker", async ({ page }) => {
  let version = 0;
  let stored = { enabled: false, instruments: [] as string[], rules: [] as Array<Record<string, unknown>> };
  await openSettings(page, "/settings/advanced#amw-breakout", {
    routes: async (browserPage) => {
      await browserPage.route(/\/api(?:\/v1)?\/me\/prefs$/, (route) => route.fulfill({
        json: { prefs: { amw_breakout: stored }, versions: { amw_breakout: version } },
      }));
      await browserPage.route(/\/api(?:\/v1)?\/me\/amw-breakout\/status$/, (route) => route.fulfill({
        json: { worker_available: false, timeframes: ["5m", "15m", "1h"], sources: ["B1", "B2", "HORIZONTAL"] },
      }));
      await browserPage.route(/\/api(?:\/v1)?\/me\/prefs\/amw_breakout\?version=\d+$/, (route) => {
        expect(route.request().method()).toBe("PUT");
        expect(new URL(route.request().url()).searchParams.get("version")).toBe(String(version));
        stored = route.request().postDataJSON();
        version += 1;
        return route.fulfill({ json: { value: stored, version } });
      });
    },
  });
  const panel = page.locator("#amw-breakout");
  await expect(panel.getByText("Unavailable", { exact: true })).toBeVisible();
  await panel.getByLabel("NSE instrument keys, separated by commas").fill("NSE_INDEX:NIFTY50");
  await panel.getByLabel("Use pure breakout").check();
  await panel.getByLabel("Use RSI-confirmed breakout").check();
  const pure = panel.getByRole("group", { name: "Pure breakout" });
  const rsi = panel.getByRole("group", { name: "RSI-confirmed breakout" });
  await pure.getByRole("combobox", { name: "Breakout timeframe" }).click();
  await page.getByRole("option", { name: "5m", exact: true }).click();
  await rsi.getByRole("combobox", { name: "Direction" }).click();
  await page.getByRole("option", { name: "Bearish only", exact: true }).click();
  await expect(pure.getByLabel("Direction")).toContainText("Bullish and bearish");
  await expect(panel.getByText("Unsaved changes")).toBeVisible();
  await panel.getByRole("button", { name: "Save breakout settings" }).click();
  await expect(panel.getByText("Breakout settings saved.")).toBeVisible();
  await page.reload();
  await expect(pure.getByLabel("Breakout timeframe")).toContainText("5m");
  await expect(rsi.getByLabel("Direction")).toContainText("Bearish only");
  await expect(panel.getByLabel("Enable informational breakout signals")).not.toBeChecked();
});
