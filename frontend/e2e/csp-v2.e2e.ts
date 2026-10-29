import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi } from "./mockApi";
import { mockCharts } from "./chartsMocks";
import { F, mockDashboard } from "./dashboardMocks";

/**
 * AUD-G2.3: the main v2 pages under the production Content-Security-Policy, with no violation in the console.
 * `vite preview` sends no CSP, so every page answer here gets the policy the frontend image sends (frontend/nginx.conf,
 * read from the file, so this test follows any later change). The edge copies (deploy/Caddyfile, Caddyfile.ip) must be
 * the same policy - backend/tests/test_a4_security.py checks that.
 * Every reply is made-up data (mockApi, dashboardMocks, chartsMocks, fixtures/option_chain_v2.json): no real account,
 * price or id. CSP_POLICY=<policy> tries another policy (used to survey what a stricter one would break).
 * Unlike the other specs, nothing here adds a <style> tag (page.addStyleTag): it would itself be a violation.
 */
const NGINX = readFileSync(new URL("../nginx.conf", import.meta.url), "utf8");
const FROM_NGINX = /add_header Content-Security-Policy "([^"]+)"/.exec(NGINX)?.[1] ?? "";
const POLICY = process.env.CSP_POLICY ?? FROM_NGINX;
const CHAIN = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as { expiries: unknown; live: Record<string, unknown> };
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];

/** Every document the preview server answers gets the policy; the console's CSP reports are collected. */
async function underPolicy(page: Page): Promise<string[]> {
  const violations: string[] = [];
  page.on("console", (m) => { if (/Content Security Policy|Content-Security-Policy/i.test(m.text())) violations.push(m.text()); });
  page.on("pageerror", (e) => { if (/Content Security Policy/i.test(e.message)) violations.push(e.message); });
  await page.route((url) => !url.pathname.startsWith("/api"), async (route) => {
    if (route.request().resourceType() !== "document") return route.fallback();
    const response = await route.fetch();
    return route.fulfill({ response, headers: { ...response.headers(), "content-security-policy": POLICY } });
  });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts", "option-chain", "terminal"]));
    } catch { /* storage unavailable */ }
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get: () => "visible" });
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get: () => false });
  });
  return violations;
}

async function chain(page: Page) {
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/expiries")) return route.fulfill({ json: CHAIN.expiries });
    return route.fulfill({ json: { ...CHAIN.live, market_open: true } });
  });
}

const FLAGS = { ...(F.features as Record<string, boolean>), ui_v2: true, terminal: true, paper_account: true, chart_workspace: true };

test.beforeEach(async ({ page }) => { await page.clock.install({ time: new Date("2026-10-27T10:30:00+05:30") }); });

test("the policy has no third-party font origin", () => {
  expect(FROM_NGINX).toContain("default-src 'self'");
  expect(FROM_NGINX).not.toMatch(/fonts\.(googleapis|gstatic)\.com/);
});

for (const [name, path] of [["Today", "/"], ["Charts", "/charts"], ["Option chain", "/option-chain"], ["Terminal", "/terminal"]] as const) {
  test(`${name} shows no CSP violation`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const violations = await underPolicy(page);
    await mockApi(page, { features: FLAGS, brokers: CONNECTED });
    if (path === "/") await mockDashboard(page, { state: "connected", me: true });
    else await mockCharts(page);
    await chain(page);
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    await expect(page.locator("main")).toBeVisible();
    // A Radix dialog is where a library adds its own <style> tag at run time (the scroll lock): open the command bar.
    await page.route(/\/api(?:\/v1)?\/instrument-master\/search/, (r) => r.fulfill({ json: [] }));
    await page.keyboard.press("Control+k");
    const bar = page.locator("[data-command-bar]");
    await expect(bar).toBeVisible();
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(300);
    expect(violations, violations.join("\n")).toEqual([]);
  });
}

test("the offline page shows no CSP violation", async ({ page }) => {
  const violations = await underPolicy(page);
  await page.goto("/offline.html");
  await expect(page.getByRole("heading", { name: "You are offline" })).toBeVisible();
  expect(violations, violations.join("\n")).toEqual([]);
});
