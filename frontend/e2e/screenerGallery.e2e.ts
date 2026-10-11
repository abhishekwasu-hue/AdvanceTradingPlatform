import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";

/**
 * U9 TX1: the screen gallery on the Screener page - the method rail, the setup chips, an empty cell said out loud, and
 * a starter card opened into the builder. The registry is the builder contract's (src/screener/builderTexts.json) and
 * the taxonomy is the server's own sample (src/screener/taxonomySample.json, checked against the data file by the
 * backend tests). With the flag off the taxonomy call fails and there is no "Browse screens" button.
 */
const read = (path: string): unknown => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
const registry = (read("../src/screener/builderTexts.json") as { registry: unknown }).registry;
const taxonomy = read("../src/screener/taxonomySample.json");

async function screener(page: Page, galleryOn: boolean) {
  await page.addInitScript(() => { try { localStorage.setItem("atp_install_dismissed", "1"); } catch { /* storage unavailable */ } });
  await mockApi(page);
  await page.route(/\/api(\/v1)?\/screener\//, async (route) => {
    const url = route.request().url();
    if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: registry } });
    if (/\/taxonomy$/.test(url)) {
      return galleryOn ? route.fulfill({ json: taxonomy })
        : route.fulfill({ status: 503, headers: { "X-Feature-Disabled": "screener_gallery" }, json: { detail: "disabled" } });
    }
    if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: "", plan: { cost: 1, lookback: {} } } });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.goto("/screener");
  await expect(page.getByText("Start with a template or add your first condition.")).toBeVisible();
}

test("flag off: the page has no gallery button", async ({ page }) => {
  await screener(page, false);
  await expect(page.getByTestId("browse-screens")).toHaveCount(0);
});

test("browse by method and setup, see an empty cell, open a starter into the builder", async ({ page }) => {
  await screener(page, true);
  await page.getByTestId("browse-screens").click();
  const dialog = page.getByRole("dialog", { name: "Screen library" });
  await expect(dialog).toBeVisible();
  const rail = dialog.getByRole("navigation", { name: "Method" });
  const chips = dialog.getByRole("group", { name: "Setup type" });
  await expect(dialog.locator("[data-screen-id]")).toHaveCount(3);

  await rail.getByRole("button", { name: /^Indicator \(/ }).click();
  await expect(rail.getByRole("button", { name: /^Indicator \(/ })).toHaveAttribute("aria-pressed", "true");
  await chips.getByRole("button", { name: /^Breakout \/ breakdown/ }).click();
  await expect(dialog.getByTestId("gallery-empty")).toHaveText("No screens are filed under Indicator → Breakout / breakdown yet.");

  await chips.getByRole("button", { name: /^Reversal/ }).click();
  await expect(dialog.locator("[data-screen-id]")).toHaveCount(1);
  await expect(dialog.getByTestId("gallery-count")).toContainText("1 screen · Indicator → Reversal");
  await dialog.getByRole("button", { name: "Open Oversold in the builder" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByLabel("Scan name")).toHaveValue("Oversold");
  await expect(page.getByRole("list", { name: "Stages - every one must pass" }).locator("li")).not.toHaveCount(0);
});
