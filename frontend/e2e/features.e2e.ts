import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";

/**
 * UI2-0 review M2: the shell reads /system/features once and shares it (useFeatures), and must stay at least as fresh
 * as the per-page reads it replaced - a re-read on every route change, when the tab is shown again, and after an
 * admin flag change. Observed through the top bar's Help button (flag `support_bot`).
 */
async function withFlags(page: Page) {
  const state = { flags: { support_bot: true } as Record<string, boolean>, reads: 0 };
  await mockApi(page);
  // Registered after mockApi, so it answers first (Playwright runs the newest matching route first).
  await page.route(/\/api(?:\/v1)?\/system\/features$/, (route) => { state.reads += 1; return route.fulfill({ json: state.flags }); });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  return state;
}

const navigate = (page: Page, path: string) =>
  page.evaluate((p) => { window.history.pushState({}, "", p); window.dispatchEvent(new PopStateEvent("popstate")); }, path);

test("a flag turned off is gone on the next page, without a reload", async ({ page }) => {
  const state = await withFlags(page);
  await page.goto("/");
  const help = page.getByRole("button", { name: "Help" });
  await expect(help).toBeVisible();
  const before = state.reads;

  state.flags = { support_bot: false };                         // the operator turns it off
  await navigate(page, "/positions");
  await expect(help).toHaveCount(0);
  expect(state.reads).toBeGreaterThan(before);

  state.flags = { support_bot: true };
  await navigate(page, "/orders");
  await expect(help).toBeVisible();
});

test("showing the tab again re-reads the flags", async ({ page }) => {
  const state = await withFlags(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Help" })).toBeVisible();
  state.flags = {};
  const before = state.reads;
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(page.getByRole("button", { name: "Help" })).toHaveCount(0);
  expect(state.reads).toBe(before + 1);
});
