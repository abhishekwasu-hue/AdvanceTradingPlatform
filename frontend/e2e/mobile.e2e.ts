import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * PW2: the pages used every day, on the two phone sizes the beta targets (360 x 800 and 390 x 844), in dark and light.
 * Two kinds of checks:
 *  - layout rules that must hold whatever the data: no sideways scroll, the bottom navigation with five 44 px+
 *    targets, every button / field / tab a 44 px+ touch target, the last control on the page reachable above the nav;
 *  - a screenshot of the first screen compared with the committed one (visual regression). Text is drawn in DejaVu
 *    Sans (installed on the CI runner and here) and the clock is frozen, so a difference means the layout changed.
 *    After an intended change: `npx playwright test --project=phone --update-snapshots`, check the new images, commit.
 */
const PAGES: [string, string][] = [
  ["dashboard", "/"],
  ["screener", "/screener"],
  ["option-chain", "/option-chain"],
  ["positions", "/positions"],
  ["orders", "/orders"],
  ["alerts", "/notifications"],
  ["settings", "/settings"],
  ["copilot", "/copilot/market-pulse"],
  ["scanner", "/scanner"],
  ["signals", "/signals"],
];
const SIZES = [{ width: 360, height: 800 }, { width: 390, height: 844 }];
const THEMES = ["dark", "light"] as const;

async function open(page: Page, path: string, theme: "dark" | "light") {
  await page.clock.install({ time: new Date("2026-10-08T10:30:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");          // the install banner is PW1's; not part of these shots
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page);
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
}

for (const size of SIZES) {
  test.describe(`${size.width} x ${size.height}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const [name, path] of PAGES) {
      test(`${name}: no sideways scroll, 44 px targets, nothing hidden behind the bottom bar`, async ({ page }) => {
        await open(page, path, "dark");
        await expectPhoneLayout(page);
      });

      for (const theme of THEMES) {
        test(`${name} looks the same (${theme})`, async ({ page }) => {
          await open(page, path, theme);
          await expect(page).toHaveScreenshot(`${name}-${size.width}-${theme}.png`, {
            animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000,
          });
        });
      }
    }

    test("More opens the full navigation and the drawer closes after a choice", async ({ page }) => {
      await open(page, "/", "dark");
      await page.getByRole("button", { name: "More pages" }).click();
      const drawer = page.locator("#app-navigation");
      await expect(drawer).toBeVisible();
      await drawer.getByRole("link", { name: "Orders" }).click();
      await expect(page).toHaveURL(/\/orders$/);
      await expect(drawer).toBeHidden();
      await expect(page.getByRole("navigation", { name: "Quick navigation" })).toBeVisible();
    });
  });
}

test.describe("desktop", () => {
  test.use({ viewport: { width: 1280, height: 800 } });
  test("no bottom navigation with a mouse and a wide screen", async ({ page }) => {
    await open(page, "/", "dark");
    await expect(page.getByRole("navigation", { name: "Quick navigation" })).toBeHidden();
  });
});
