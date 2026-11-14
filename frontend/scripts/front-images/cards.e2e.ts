import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { openAdmin, type Theme } from "../../e2e/adminMocks";

/**
 * Front-door images, review: shoots each of the six benefit cards AS THE SIGN-IN PAGE SHOWS THEM (the image's 16:10
 * crop, its title and its text) at 1440 and 390, light and dark, to FRONT_CARDS (default /tmp/front-cards), as
 * <size>-<theme>-<slug>.png. For the before / after comparison page; nothing is asserted beyond "the six cards show".
 * Run from frontend/ after `npx vite build`:
 *   npx playwright test -c scripts/front-images/playwright.config.ts cards.e2e.ts --trace off
 */
const OUT = process.env.FRONT_CARDS ?? "/tmp/front-cards";
mkdirSync(OUT, { recursive: true });
const SLUGS = ["copilot", "option-chain", "find-setups", "trading-bot", "journal", "daily-report"];
const SIZES = [{ name: "1440", width: 1440, height: 900, mobile: false }, { name: "390", width: 390, height: 844, mobile: true }];

for (const size of SIZES) {
  test.describe(`cards ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });
    for (const theme of ["dark", "light"] as Theme[]) {
      test(`the six cards (${theme})`, async ({ page }) => {
        await openAdmin(page, "/", { theme, routes: async (p) => {
          await p.route(/\/api(?:\/v1)?\/auth\/(me|refresh)$/, (r) => r.fulfill({ status: 401, json: { detail: "Not authenticated" } }));
        } });
        await expect(page.locator("[data-screen='sign-in']")).toBeVisible();
        for (const slug of SLUGS) {
          const card = page.locator(`[data-front-benefit='${slug}']`);
          await card.scrollIntoViewIfNeeded();
          await page.waitForFunction((s) => (document.querySelector(`img[data-front-image='${s}']`) as HTMLImageElement | null)?.complete === true, slug);
          await card.screenshot({ path: join(OUT, `${size.name}-${theme}-${slug}.png`), animations: "disabled" });
        }
      });
    }
  });
}
