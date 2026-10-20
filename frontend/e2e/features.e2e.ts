import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";

/**
 * UI2-0 review M2: the shell reads /system/features once and shares it (useFeatures), and must stay at least as fresh
 * as the per-page reads it replaced without asking the server on every click (M3): a route change or the tab shown again
 * re-reads at most once per 30 s; a failed re-read keeps the last good flags (L7). Observed through the top bar's Help
 * button (flag `support_bot`); the page clock is controlled.
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

test("a route change re-reads the flags at most once per 30 s; a flag turned off is gone after that, without a reload", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-10-08T10:30:00+05:30") });
  const state = await withFlags(page);
  await page.goto("/");
  const help = page.getByRole("button", { name: "Help" });
  await expect(help).toBeVisible();
  const first = state.reads;

  state.flags = { support_bot: false };                         // the operator turns it off
  await navigate(page, "/positions");                           // within 30 s: no new read (review M3)
  await navigate(page, "/orders");
  await expect(page).toHaveURL(/\/orders$/);
  expect(state.reads).toBe(first);
  await expect(help).toBeVisible();

  await page.clock.fastForward(31_000);
  await navigate(page, "/positions");
  await expect(help).toHaveCount(0);
  expect(state.reads).toBe(first + 1);

  state.flags = { support_bot: true };
  await page.clock.fastForward(31_000);
  await navigate(page, "/orders");
  await expect(help).toBeVisible();
});

test("showing the tab again re-reads the flags, sharing the 30 s limit", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-10-08T10:30:00+05:30") });
  const state = await withFlags(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Help" })).toBeVisible();
  state.flags = {};
  const before = state.reads;
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  expect(state.reads).toBe(before);                              // too soon
  await page.clock.fastForward(31_000);
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(page.getByRole("button", { name: "Help" })).toHaveCount(0);
  expect(state.reads).toBe(before + 1);
});

test("a failed re-read keeps the last good flags (only the first read fails closed)", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-10-08T10:30:00+05:30") });
  const state = await withFlags(page);
  await page.goto("/");
  const help = page.getByRole("button", { name: "Help" });
  await expect(help).toBeVisible();
  await page.route(/\/api(?:\/v1)?\/system\/features$/, (route) => { state.reads += 1; return route.fulfill({ status: 503, json: { detail: "down" } }); });
  const before = state.reads;
  await page.clock.fastForward(31_000);
  await navigate(page, "/positions");
  await expect.poll(() => state.reads).toBe(before + 1);
  await expect(help).toBeVisible();
});
