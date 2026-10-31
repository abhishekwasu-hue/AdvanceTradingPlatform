import { expect, test, type Page } from "@playwright/test";
import { F, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";

/**
 * UI-28 U4 (owner, 29 Oct: "a modern colour scheme; hide the unnecessary text"): the colour v3 palette as users get it
 * (ON by default, owner 28 Oct). ONE accent - the bottom-bar tab on screen is the only brand-coloured tab, the other
 * tabs and the page / settings icon chips are muted; the Today P&L block has no tinted background; the page subtitle is
 * not shown. Made-up replies (e2e/fixtures/dashboard_v2.json).
 */
const FEATURES = { ...(F.features as Record<string, boolean>), colour_v2: true, colour_v3: true };

async function open(page: Page, path: string, theme: "dark" | "light") {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: FEATURES, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { me: true });
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  await expect(page.locator("html")).toHaveAttribute("data-palette", "v3");
}

const token = (page: Page, name: string) => page.evaluate((n) => `rgb(${getComputedStyle(document.documentElement).getPropertyValue(n).trim().split(/\s+/).join(", ")})`, name);

for (const theme of ["dark", "light"] as const) {
  test.describe(`colour v3 390 (${theme})`, () => {
    test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

    test("one accent: only the tab on screen is brand; the other tabs and the icon chips are muted", async ({ page }) => {
      await open(page, "/", theme);
      const muted = await token(page, "--fg-muted");
      const brand = await token(page, "--brand");
      const nav = page.locator("[data-bottom-nav-v2]");
      const colours = await nav.locator(":scope > a, :scope > button").evaluateAll((els) => els.map((e) => [e.getAttribute("aria-current"), getComputedStyle(e).color]));
      expect(colours.filter(([cur]) => cur === "page").map(([, c]) => c)).toEqual([brand]);
      expect(colours.filter(([cur]) => cur !== "page").every(([, c]) => c === muted)).toBe(true);
      const chip = page.locator("[data-topbar-chip]").first();
      if (await chip.count()) expect(await chip.evaluate((e) => getComputedStyle(e).color)).toBe(muted);
    });

    test("no tinted P&L card on Today; no page subtitle on My positions", async ({ page }) => {
      await open(page, "/", theme);
      const tint = page.locator("[data-pnl-tint]");
      if (await tint.count()) expect(await tint.evaluate((e) => getComputedStyle(e).backgroundColor)).toBe("rgba(0, 0, 0, 0)");
      await page.goto("/positions");
      await page.waitForLoadState("networkidle");
      // PROSE-1: the subtitle now sits behind the title's (?) - nothing of it shows until that is opened.
      await expect(page.locator("header [data-help-button]").first()).toBeVisible();
      for (const p of await page.locator("[data-page-subtitle]").all()) await expect(p).toBeHidden();
    });
  });
}
