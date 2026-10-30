import { expect, test, type Page, type Route } from "@playwright/test";
import { openAdmin, type Theme } from "./adminMocks";

/**
 * MO3-A1: the admin's read-only "View as user" (ui_v2 on). In Support the admin opens one person, gives a reason and
 * starts the view; the app swaps to the view's token, reads as the person and shows the banner "Viewing as <name> -
 * read only" with End over the page; End tells the server with the view's token and brings the admin back to the same
 * person in Support. The person's Settings › Account & plan lists the view.
 * - Baselines (NEW): `view-as-start-v2-{1440,390}-{light,dark}.png` (the Support form) and
 *   `view-as-banner-v2-{1440,390}-{light,dark}.png` (the banner over the person's front page, top of the screen).
 * Every reply is made up. After an intended change: `npx playwright test --project=phone e2e/view-as-v2.e2e.ts --update-snapshots`.
 */

const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };
const PERSON_ME = { id: 12, email: "tr****@example.com", tenant_id: 7, role: "USER", mfa_enabled: true, email_verified: true, scopes: [] };
const VIEW = {
  id: 41, user_id: 12, tenant_id: 7, name: "tr****@example.com", reason: "The person asked why a trading bot stopped",
  started_at: "2026-10-22T05:30:00Z", expires_at: "2026-10-22T06:00:00Z", ended_at: null, end_reason: null, active: true,
  seconds_left: 1800, requests: 0, refused: 0, last_seen_at: null,
};

interface Seen { starts: unknown[]; ends: (string | null)[]; meAs: string[] }

async function viewRoutes(page: Page, seen: Seen) {
  const A = (p: string) => new RegExp(`/api(?:/v1)?${p}`);
  await page.route(A("/auth/me$"), (r: Route) => {
    const auth = r.request().headers().authorization ?? "";
    seen.meAs.push(auth);
    return r.fulfill({ json: auth === "Bearer view-token" ? PERSON_ME : { id: 1, email: "ops@example.com", tenant_id: 1, role: "SUPER_ADMIN", mfa_enabled: true, email_verified: true, scopes: [] } });
  });
  await page.route(A("/admin/view-sessions(\\?.*)?$"), (r: Route) => {
    if (r.request().method() === "POST") {
      seen.starts.push(r.request().postDataJSON());
      return r.fulfill({ json: { token: "view-token", view: VIEW } });
    }
    return r.fulfill({ json: { views: [{ ...VIEW, active: false, ended_at: "2026-10-21T10:20:00Z", end_reason: "ended by support", requests: 18, refused: 1, admin_user_id: 1 }] } });
  });
  await page.route(A("/view-session/end$"), (r: Route) => {
    seen.ends.push(r.request().headers().authorization ?? null);
    return r.fulfill({ json: { ended: true } });
  });
  await page.route(A("/me/support-access$"), (r: Route) => r.fulfill({ json: { views: [
    { id: 41, by: "ATP support", reason: "The person asked why a trading bot stopped", started_at: "2026-10-21T10:00:00Z", ended_at: "2026-10-21T10:20:00Z", active: false, read_only: true, requests: 18 },
  ] } }));
}

async function startView(page: Page) {
  const person = page.locator("[data-support-user='12']");
  await person.getByRole("button", { name: "View as this person" }).click();
  const start = page.getByRole("button", { name: "Start read-only view" });
  await expect(start).toBeDisabled();
  await page.getByLabel("Why are you opening this account? (required)").fill("The person asked why a trading bot stopped");
  await expect(start).toBeEnabled();
  return start;
}

for (const size of [{ name: "1440", width: 1440, height: 900, mobile: false }, { name: "390", width: 390, height: 844, mobile: true }]) {
  test.describe(`View as user ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as Theme[]) {
      test(`the Support form and the banner look the same (${theme})`, async ({ page }) => {
        const seen: Seen = { starts: [], ends: [], meAs: [] };
        await openAdmin(page, "/admin?section=support&user=12", { theme, routes: (p) => viewRoutes(p, seen) });
        const start = await startView(page);
        await expect(page.locator("[data-view-as-start='form']")).toHaveScreenshot(`view-as-start-v2-${size.name}-${theme}.png`, SHOT);
        await start.click();
        const banner = page.locator("[data-view-as-banner='12']");
        await expect(banner).toBeVisible();
        await expect(page).toHaveURL(/127\.0\.0\.1:4173\/$/);
        await page.evaluate(() => window.scrollTo(0, 0));
        await expect(page).toHaveScreenshot(`view-as-banner-v2-${size.name}-${theme}.png`,
          { ...SHOT, clip: { x: 0, y: 0, width: size.width, height: size.mobile ? 160 : 120 } });
        const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
        expect(r.scroll, "page wider than the screen").toBeLessThanOrEqual(r.width);
      });
    }
  });
}

test.describe("View as user behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("start reads as the person under the banner; End goes back to the admin in Support", async ({ page }) => {
    const seen: Seen = { starts: [], ends: [], meAs: [] };
    await openAdmin(page, "/admin?section=support&user=12", { routes: (p) => viewRoutes(p, seen) });
    await expect(page.locator("[data-console-card='views']")).toContainText("ended by support");
    await (await startView(page)).click();
    const banner = page.locator("[data-view-as-banner='12']");
    await expect(banner).toContainText("Viewing as tr****@example.com - read only");
    await expect(banner).toContainText("min left");
    expect(seen.starts).toEqual([{ user_id: 12, reason: "The person asked why a trading bot stopped" }]);
    await expect.poll(() => seen.meAs.includes("Bearer view-token")).toBe(true);
    // The person's own Settings lists the support views (the view reads as the person).
    await page.goto("/settings/account");
    // A full page load ends a view in this tab: the banner is gone and the admin is back.
    await expect(page.locator("[data-view-as-banner]")).toHaveCount(0);
    await page.goto("/admin?section=support&user=12");
    await (await startView(page)).click();
    await expect(banner).toBeVisible();
    await banner.getByRole("button", { name: "End" }).click();
    await expect(page.locator("[data-view-as-banner]")).toHaveCount(0);
    await expect(page).toHaveURL(/\/admin\?section=support&user=12$/);
    expect(seen.ends).toEqual(["Bearer view-token"]);
    await expect(page.locator("[data-support-user='12']")).toBeVisible();
  });

  test("a stale two-factor check asks for the code, then the view starts", async ({ page }) => {
    const seen: Seen = { starts: [], ends: [], meAs: [] };
    let refusedOnce = false;
    await openAdmin(page, "/admin?section=support&user=12", {
      routes: async (p) => {
        await viewRoutes(p, seen);
        await p.route(/\/api(?:\/v1)?\/admin\/view-sessions$/, (r) => {
          if (r.request().method() === "POST" && !refusedOnce) {
            refusedOnce = true;
            return r.fulfill({ status: 403, headers: { "X-Step-Up": "mfa" },
              json: { detail: "Viewing an account requires a fresh two-factor check - enter your authenticator code" } });
          }
          return r.fallback();
        });
      },
    });
    await (await startView(page)).click();
    await expect(page.getByText("Two-factor check required")).toBeVisible();
    await page.getByPlaceholder("authenticator code").fill("123456");
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.locator("[data-view-as-banner='12']")).toBeVisible();
    expect(seen.starts).toHaveLength(1);
  });

  test("an administrator's account offers no view", async ({ page }) => {
    await openAdmin(page, "/admin?section=support&user=12", {
      routes: async (p) => {
        await p.route(/\/api(?:\/v1)?\/admin\/console\/support\/users\/12$/, (r) => r.fulfill({ json: {
          user: { id: 12, email: "op*@example.com", role: "SUPER_ADMIN", active: true, mfa: true, email_verified: true, trading_paused: false, signed_up_at: null, last_seen_at: null },
          tenant: null, brokers: [], sign_ins: [], bots_with_errors: [], ai_failures_7d: [], alert_deliveries_failed: 0 } }));
        await p.route(/\/api(?:\/v1)?\/admin\/view-sessions/, (r) => r.fulfill({ json: { views: [] } }));
      },
    });
    await expect(page.locator("[data-view-as-start='admin']")).toContainText("cannot be viewed");
    await expect(page.getByRole("button", { name: "View as this person" })).toHaveCount(0);
  });

  test("the person's Settings shows when support viewed the account", async ({ page }) => {
    const seen: Seen = { starts: [], ends: [], meAs: [] };
    await openAdmin(page, "/settings/account", { routes: (p) => viewRoutes(p, seen) });
    const card = page.locator("[data-support-access]");
    await expect(card).toContainText("ATP support viewed your account (read only)");
    await expect(card).toContainText("Reason: The person asked why a trading bot stopped");
  });
});
