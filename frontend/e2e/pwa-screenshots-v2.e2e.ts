import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * AUD-G2.3: makes the web app manifest's two screenshots (public/screenshots/, shown by an install prompt): the public
 * Features page (no sign-in, the server's own fixture e2e/fixtures/site_v2.json) at a phone and a desktop size. A public
 * surface on purpose: no account, no symbol, no price (CLAUDE.md §3). Real product fonts (no DejaVu pin here).
 * Runs only with PWA_SHOTS=1: `PWA_SHOTS=1 npx playwright test --project=phone e2e/pwa-screenshots-v2.e2e.ts`; the
 * sizes must stay those in public/manifest.webmanifest (test/pwa-shell.test.ts reads the files and checks).
 */
const SITE = JSON.parse(readFileSync(new URL("./fixtures/site_v2.json", import.meta.url), "utf8")) as unknown;
const OUT = new URL("../public/screenshots/", import.meta.url).pathname;
const SIZES = [
  { file: "features-narrow.png", viewport: { width: 390, height: 844 }, scale: 2, mobile: true },
  { file: "features-wide.png", viewport: { width: 1280, height: 800 }, scale: 1, mobile: false },
];

test.skip(!process.env.PWA_SHOTS, "PWA_SHOTS=1 makes the manifest screenshots");

for (const size of SIZES) {
  test.describe(size.file, () => {
    test.use({ viewport: size.viewport, deviceScaleFactor: size.scale, isMobile: size.mobile, hasTouch: size.mobile });

    test(`the Features page for ${size.file}`, async ({ page }) => {
      await page.addInitScript(() => {
        try {
          localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
          localStorage.setItem("atp_install_dismissed", "1");
          localStorage.setItem("atp_install_domain_dismissed", "1");
        } catch { /* storage unavailable */ }
      });
      await page.route("**/api/**", (r) => r.fulfill({ status: 401, json: { detail: "Not signed in" } }));
      await page.route(/\/api(?:\/v1)?\/site\/pages$/, (r) => r.fulfill({ json: SITE }));
      await page.goto("/features");
      await page.waitForLoadState("networkidle");
      await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: join(OUT, size.file), animations: "disabled" });
    });
  });
}
