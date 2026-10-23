import { expect, test, type Page } from "@playwright/test";
import { openAdmin, type Theme } from "./adminMocks";

/**
 * Production readiness A2: Admin > System > Alerts and the footer's "Status" link (signed in: the desktop sidebar and
 * the phone's More sheet; signed out: the sign-in page). Every reply is made up (e2e/adminMocks.ts; the status read
 * below). Baselines (NEW): `admin-alerts-v2-{360,390,1440}-{light,dark}.png`, `footer-status-v2-{360,390,1440}-{light,dark}.png`
 * and `sign-in-status-v2-{360,390,1440}-{light,dark}.png`. After an intended change:
 * `npx playwright test --project=phone e2e/ops-alerts-v2.e2e.ts --update-snapshots`.
 */

const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };
const STATUS = {
  status: "operational", checked_at: "2026-10-22T05:30:00+00:00",
  components: {
    api: { state: "operational", summary: "Answering requests." },
    worker: { state: "operational", summary: "Strategies, scans and alerts are being processed.", seconds_since_heartbeat: 8 },
    data_feed: { state: "operational", summary: "ok", market_open: true, eod: { state: "operational", date: "2026-10-21", summary: "ok" },
                 tier_a: { enabled: false, sessions: 0, summary: "Live data through your own broker is not enabled." } },
    alerts: { state: "operational", summary: "Alerts are being delivered.", lag_seconds: 0 },
  },
  banner: { maintenance: false, maintenance_message: null, incidents: [] },
};

const statusOn = async (page: Page) => {
  await page.route(/\/api(?:\/v1)?\/status$/, (r) => r.fulfill({ json: STATUS }));
};
const signedOut = async (page: Page) => {
  await statusOn(page);
  await page.route(/\/api(?:\/v1)?\/auth\/(me|refresh)$/, (r) => r.fulfill({ status: 401, json: { detail: "Not authenticated" } }));
};

async function noSidewaysScroll(page: Page) {
  const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
  expect(r.scroll, "page wider than the screen").toBeLessThanOrEqual(r.width);
}

const SIZES = [
  { name: "360", width: 360, height: 780, mobile: true },
  { name: "390", width: 390, height: 844, mobile: true },
  { name: "1440", width: 1440, height: 900, mobile: false },
];

for (const size of SIZES) {
  test.describe(`A2 ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as Theme[]) {
      test(`the Alerts card (${theme})`, async ({ page }) => {
        await openAdmin(page, "/admin", { theme });
        const card = page.locator("[data-console-card='ops-alerts']");
        await expect(card).toContainText("1 failing, 1 warning");
        await expect(card.locator("[data-ops-check]").first()).toHaveAttribute("data-ops-check", "backup");   // problems first
        await expect(card).toContainText("Your chat is connected.");
        await noSidewaysScroll(page);
        await expect(card).toHaveScreenshot(`admin-alerts-v2-${size.name}-${theme}.png`, SHOT);
      });

      test(`the footer's Status link, signed in (${theme})`, async ({ page }) => {
        await openAdmin(page, "/admin", { theme, routes: statusOn });
        if (size.mobile) {
          await page.getByRole("button", { name: /^More pages/ }).click();
          const sheet = page.locator("[data-more-sheet]");
          const link = sheet.locator("[data-status-link]");
          await link.scrollIntoViewIfNeeded();
          await expect(link).toHaveAttribute("href", "/status");
          await expect(link).toContainText("Status");
          await expect(sheet.locator("[data-more-footer]")).toHaveScreenshot(`footer-status-v2-${size.name}-${theme}.png`, SHOT);
        } else {
          const footer = page.locator("[data-sidebar-footer]");
          await expect(footer.locator("[data-status-link]")).toHaveAttribute("href", "/status");
          await expect(page.locator("aside[aria-label='Navigation']")).toHaveScreenshot(`footer-status-v2-${size.name}-${theme}.png`, SHOT);
        }
      });

      test(`the sign-in page's Status link (${theme})`, async ({ page }) => {
        await openAdmin(page, "/", { theme, routes: signedOut });
        await expect(page.locator("[data-screen='sign-in']")).toBeVisible();
        await expect(page.locator("[data-sign-in-footer] [data-status-link]")).toHaveAttribute("href", "/status");
        await noSidewaysScroll(page);
        await expect(page).toHaveScreenshot(`sign-in-status-v2-${size.name}-${theme}.png`, { ...SHOT, fullPage: true });
      });
    }
  });
}

test.describe("A2 behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("no status page, no link: the flag is off", async ({ page }) => {
    await openAdmin(page, "/admin");                     // /status answers 404 (the default reply)
    await expect(page.locator("[data-console-card='ops-alerts']")).toBeVisible();
    await expect(page.locator("[data-status-link]")).toHaveCount(0);
  });

  test("Check now and Send a test call the server and say what happened", async ({ page }) => {
    const posts: string[] = [];
    await openAdmin(page, "/admin", { routes: async (p) => {
      await p.route(/\/api(?:\/v1)?\/admin\/ops-alerts\/(run|test)$/, (r) => {
        posts.push(new URL(r.request().url()).pathname);
        return r.request().url().endsWith("/test") ? r.fulfill({ json: { telegram: true, email: true } }) : r.fallback();
      });
    } });
    const card = page.locator("[data-console-card='ops-alerts']");
    await card.locator("[data-ops-test]").click();
    await expect(card.locator("[data-ops-note]")).toHaveText("Telegram: sent · e-mail: sent");
    expect(posts.some((p) => p.endsWith("/admin/ops-alerts/test"))).toBe(true);
  });
});
