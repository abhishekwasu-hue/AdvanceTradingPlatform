import { expect, test, type Page, type Route } from "@playwright/test";
import { fixtureStatus } from "../src/chartAnalyze/fixtures";
import type { AnalyzeStatus } from "../src/chartAnalyze/types";
import { mockApi, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * A1 (UI-H bug lane): Analyze a chart while its engine (the private package) is not installed on the server. The
 * server answers the flag as off for users (the page leaves the navigation) and the status as installed=false; at the
 * page's address a user sees "Chart analysis is not available right now" with no upload or link input, and the admin
 * also sees why and the owner's install steps (the server sends those to SUPER_ADMIN only). Never "Coming soon".
 * Made-up answers only. A1_SHOTS=<dir> also saves the 390 pair (user / admin) there for the PR.
 */

const FEATURES: MockOptions = { features: { ui_v2: true }, brokers: [] };
const ADMIN_ME = { id: 1, email: "ops@example.com", tenant_id: 1, role: "SUPER_ADMIN", mfa_enabled: true, email_verified: true, scopes: [] };
const MISSING: AnalyzeStatus["engine"] = { installed: false, message: "Analysis engine not installed" };
const ADMIN_MISSING: AnalyzeStatus["engine"] = { ...MISSING, reason: "the private package is not installed on this server",
  owner_steps: "The owner adds the host's read-only deploy key (~/.ssh/atp_private_ro) and ATP_PRIVATE_REF (a tag of atp-private) to /opt/atp/.env, then runs the deploy (docs/deploy/HOSTINGER_RUNBOOK.md, 'Chart analysis engine')." };

async function open(page: Page, engine: AnalyzeStatus["engine"] | null, admin = false) {
  await page.clock.install({ time: new Date("2026-10-20T10:30:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, FEATURES);
  if (admin) {
    await page.route(/\/api(?:\/v1)?\/auth\/me$/, (route: Route) => route.fulfill({ json: ADMIN_ME }));
  }
  await page.route(/\/api(?:\/v1)?\/chart-analyze\/.*/, (route: Route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api(?:\/v1)?\/chart-analyze/, "");
    if (path === "/status") {
      if (!engine) return route.fulfill({ status: 503, json: { detail: "The 'chart_analyze' feature is currently disabled by the platform operator" } });
      return route.fulfill({ json: fixtureStatus({ engine }) });
    }
    return route.fulfill({ json: { items: [] } });
  });
}

async function shot(page: Page, name: string) {
  const dir = process.env.A1_SHOTS;
  if (!dir) return;
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.screenshot({ path: `${dir}/${name}`, fullPage: true });
}

test.describe("Analyze a chart without its engine, 390 x 844", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("a user: one plain line, no inputs, no reason, never Coming soon", async ({ page }) => {
    await open(page, MISSING);
    await page.goto("/chart-analyze");
    const box = page.getByTestId("chart-analyze-unavailable");
    await expect(box).toBeVisible();
    await expect(box).toContainText("Chart analysis is not available right now");
    await expect(box).not.toContainText("For admins");
    await expect(page.getByText(/Coming soon|not open for your account/)).toHaveCount(0);
    await expect(page.getByTestId("file-input")).toHaveCount(0);
    await expect(page.getByTestId("drop-zone")).toHaveCount(0);
    await expect(page.getByText("Or paste a chart link")).toHaveCount(0);
    await expectPhoneLayout(page);
    await shot(page, "chart-analyze-engine-missing-390-user.png");
  });

  test("the admin: the same line plus why and the owner's install steps", async ({ page }) => {
    await open(page, ADMIN_MISSING, true);
    await page.goto("/chart-analyze");
    const detail = page.getByTestId("engine-admin-detail");
    await expect(page.getByText("Chart analysis is not available right now")).toBeVisible();
    await expect(detail).toContainText("Why: the private package is not installed on this server.");
    await expect(detail).toContainText("ATP_PRIVATE_REF");
    await expect(page.getByTestId("file-input")).toHaveCount(0);
    await expect(page.getByText(/Coming soon/)).toHaveCount(0);
    await expectPhoneLayout(page);
    await shot(page, "chart-analyze-engine-missing-390-admin.png");
  });

  test("the flag itself off: the shell's standard Coming soon, as before", async ({ page }) => {
    await open(page, null);
    await page.goto("/chart-analyze");
    await expect(page.getByRole("heading", { name: "Coming soon" })).toBeVisible();
    await expect(page.getByTestId("chart-analyze-unavailable")).toHaveCount(0);
  });
});
