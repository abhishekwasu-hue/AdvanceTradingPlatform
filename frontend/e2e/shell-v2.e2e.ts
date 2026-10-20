import { expect, test, type Page } from "@playwright/test";
import { mockApi, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * UI2-1 (UI v2 proposal §2, §4, §9, §12): the v2 shell with the `ui_v2` flag on (mockApi answers /system/features).
 * - Baselines `shell-v2-<width>-<theme>.png` at 1440, 390 and 360 (and the More sheet at 390). The old baselines in
 *   __screenshots__ are not touched: with the flag off nothing changes (mobile.e2e.ts keeps proving it).
 * - Every phone page in the v2 shell passes the PW2 layout rules (no sideways scroll, 44 px targets, nothing hidden
 *   behind the bottom bar).
 * - The navigation model at work: sections and their memory, flag-hidden items, Admin console for SUPER_ADMIN only,
 *   the More sheet and its search, the command bar (Ctrl K), the redirects, the broker gate on data pages.
 * After an intended change: `npx playwright test --project=phone e2e/shell-v2.e2e.ts --update-snapshots`, check the
 * new images, commit.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const V2: MockOptions = { features: { ui_v2: true }, brokers: CONNECTED };

async function open(page: Page, path: string, theme: "dark" | "light", options: MockOptions = V2, storage: Record<string, string> = {}) {
  await page.clock.install({ time: new Date("2026-10-08T10:30:00+05:30") });
  await page.addInitScript(([t, extra]) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      for (const [k, v] of Object.entries(extra)) localStorage.setItem(k, v);
    } catch { /* storage unavailable */ }
  }, [theme, storage] as const);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, options);
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
}

const PHONE_PAGES: [string, string][] = [
  ["dashboard", "/"], ["charts", "/charts"], ["screener", "/screener"], ["option-chain", "/option-chain"],
  ["positions", "/positions"], ["orders", "/orders"], ["alerts", "/notifications"], ["settings", "/settings"],
  ["copilot", "/copilot/market-pulse"], ["custom-scan", "/screener/custom"], ["signals", "/signals"],
];
const PHONES = [{ width: 360, height: 800 }, { width: 390, height: 844 }];
const THEMES = ["dark", "light"] as const;

for (const size of PHONES) {
  test.describe(`v2 shell ${size.width} x ${size.height}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`shell looks the same (${theme})`, async ({ page }) => {
        await open(page, "/", theme);
        await expect(page.locator("[data-shell='v2']")).toBeVisible();
        await expect(page).toHaveScreenshot(`shell-v2-${size.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    }

    for (const [name, path] of PHONE_PAGES) {
      test(`${name} in the v2 shell: no sideways scroll, 44 px targets, nothing hidden behind the bottom bar`, async ({ page }) => {
        await open(page, path, "dark");
        await expect(page.locator("[data-shell='v2']")).toBeVisible();
        await expectPhoneLayout(page);
      });
    }

    test("the bottom bar is Home, Charts, Signals, Positions, More - each at least 44 px", async ({ page }) => {
      await open(page, "/", "dark");
      const bar = page.getByRole("navigation", { name: "Quick navigation" });
      await expect(bar.locator("a, button")).toHaveText(["Home", "Charts", "Signals", "Positions", "More"]);
      for (const box of await bar.locator("a, button").evaluateAll((els) => els.map((e) => e.getBoundingClientRect()))) {
        expect(box.width).toBeGreaterThanOrEqual(44);
        expect(box.height).toBeGreaterThanOrEqual(44);
      }
      await bar.getByRole("link", { name: "Charts" }).click();
      await expect(page).toHaveURL(/\/charts$/);
    });

    test("More opens a sheet with search and every other page; a choice closes it", async ({ page }) => {
      await open(page, "/", "dark");
      await page.getByRole("button", { name: "More pages" }).click();
      const sheet = page.getByRole("dialog", { name: "All pages" });
      await expect(sheet).toBeVisible();
      for (const section of ["Markets", "Strategies", "Trading", "Insights", "Account", "More"]) {
        await expect(sheet.getByRole("heading", { name: section, exact: true })).toBeVisible();
      }
      await expect(sheet.getByRole("link", { name: "Option chain" })).toBeVisible();
      await expect(sheet.getByRole("link", { name: "My positions" })).toHaveCount(0);   // in the bar already
      await expect(sheet.getByRole("link", { name: "Admin console" })).toHaveCount(0);  // an OWNER, not the operator
      for (const box of await sheet.locator("a").evaluateAll((els) => els.map((e) => e.getBoundingClientRect().height))) {
        expect(box).toBeGreaterThanOrEqual(44);
      }
      // Today's name finds the renamed page.
      await sheet.getByRole("searchbox", { name: "Search pages" }).fill("backtesting");
      await expect(sheet.getByRole("link")).toHaveCount(1);
      await sheet.getByRole("link", { name: /Test a strategy/ }).click();
      await expect(page).toHaveURL(/\/backtest$/);
      await expect(sheet).toBeHidden();
    });

    test("the bar stays usable over the sheet: More closes it, a bar page goes there", async ({ page }) => {
      await open(page, "/", "dark");
      // While the sheet is open the rest of the app is hidden from assistive tech (the sheet has its own Close), so the
      // bar is found by its marker here - a finger still reaches it.
      const more = page.locator("[data-bottom-nav-v2] button");
      await more.click();
      const sheet = page.getByRole("dialog", { name: "All pages" });
      await expect(sheet).toBeVisible();
      await more.click();
      await expect(sheet).toBeHidden();
      await more.click();
      await expect(sheet).toBeVisible();
      await page.locator("[data-bottom-nav-v2] a", { hasText: "Positions" }).click();
      await expect(page).toHaveURL(/\/positions$/);
      await expect(sheet).toBeHidden();
    });
  });
}

test.describe("v2 shell 390, More sheet", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  for (const theme of THEMES) {
    test(`More sheet looks the same (${theme})`, async ({ page }) => {
      await open(page, "/", theme);
      await page.getByRole("button", { name: "More pages" }).click();
      await expect(page.getByRole("dialog", { name: "All pages" })).toBeVisible();
      await expect(page).toHaveScreenshot(`shell-v2-more-390-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("an address without a trusted certificate says why the app cannot be installed", async ({ page }) => {
    await page.addInitScript(() => { Object.defineProperty(window, "isSecureContext", { value: false }); });
    await open(page, "/", "dark", V2, { atp_install_dismissed: "" });
    const message = "Install needs the app's web address (domain) with a trusted certificate. Ask the owner to set up the domain.";
    await expect(page.locator("[data-install='unavailable']")).toHaveText(message);
    await page.getByRole("button", { name: "More pages" }).click();
    await page.getByRole("button", { name: "Install the app" }).click();
    await expect(page.locator("[data-install-message]")).toHaveText(message);
  });
});

test.describe("v2 shell 1440 desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`shell looks the same (${theme})`, async ({ page }) => {
      await open(page, "/", theme);
      await expect(page.getByRole("complementary", { name: "Navigation" })).toBeVisible();
      await expect(page).toHaveScreenshot(`shell-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("five sections and More; Markets and Trading open; a section remembers being opened", async ({ page }) => {
    await open(page, "/", "light");
    const nav = page.getByRole("navigation", { name: "Pages" });
    const headers = nav.locator("button[aria-expanded]");
    await expect(headers).toHaveText(["Markets", "Strategies", "Trading", "Insights", "Account", "More"]);
    await expect(nav.getByRole("link", { name: "Dashboard" })).toHaveAttribute("aria-current", "page");
    await expect(nav.getByRole("link", { name: "Signals" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Library" })).toHaveCount(0);           // Strategies starts closed
    await nav.getByRole("button", { name: "Strategies" }).click();
    await expect(nav.getByRole("link", { name: "Library" })).toBeVisible();
    await nav.getByRole("button", { name: "Trading" }).click();
    await expect(nav.getByRole("link", { name: "Signals" })).toHaveCount(0);
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(nav.getByRole("link", { name: "Library" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Signals" })).toHaveCount(0);
    // The page on screen opens its own section.
    await page.goto("/signals");
    await expect(nav.getByRole("link", { name: "Signals" })).toHaveAttribute("aria-current", "page");
  });

  test("pages whose feature is off are not listed; Admin console only for the platform operator", async ({ page }) => {
    await open(page, "/", "light", { ...V2, features: { ui_v2: true } }, { atp_nav_sections: JSON.stringify({ strategies: true, insights: true, more: true }) });
    const nav = page.getByRole("navigation", { name: "Pages" });
    await expect(nav.getByRole("link", { name: "Factor Lab" })).toBeVisible();
    for (const hidden of ["AI Copilot", "Journal", "Marketplace", "Admin console"]) await expect(nav.getByRole("link", { name: hidden })).toHaveCount(0);

    await page.route(/\/api(?:\/v1)?\/auth\/me$/, (route) => route.fulfill({ json: { id: 1, email: "ops@example.com", tenant_id: 1, role: "SUPER_ADMIN", email_verified: true } }));
    await page.route(/\/api(?:\/v1)?\/system\/features$/, (route) => route.fulfill({ json: { ui_v2: true, ai_copilot: true, discipline_engine: true, marketplace: true } }));
    await page.reload();
    await page.waitForLoadState("networkidle");
    for (const shown of ["AI Copilot", "Journal", "Marketplace", "Admin console"]) await expect(nav.getByRole("link", { name: shown })).toBeVisible();
  });

  test("Ctrl K opens the command bar; typing and Enter go to the page", async ({ page }) => {
    await open(page, "/", "light");
    await page.keyboard.press("Control+k");
    const bar = page.locator("[data-command-bar]");
    await expect(bar).toBeVisible();
    await bar.getByRole("combobox", { name: "Search pages" }).fill("risk");
    await expect(bar.getByRole("option").first()).toHaveText(/Risk/);
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/risk-management$/);
    await expect(bar).toBeHidden();
    // The top bar's search box opens it too; a page whose feature is off is not offered.
    await page.getByRole("button", { name: /^Search pages/ }).click();
    await bar.getByRole("combobox").fill("journal");
    await expect(bar.getByText('No page matches "journal".')).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(bar).toBeHidden();
  });

  test("old addresses forward to the new ones with the flag on", async ({ page }) => {
    await open(page, "/scanner", "light");
    await expect(page).toHaveURL(/\/screener\/custom$/);
    await page.goto("/options-builder?underlying=NIFTY");
    await expect(page).toHaveURL(/\/option-chain\/plan\?underlying=NIFTY$/);
    await page.goto("/account");
    await expect(page).toHaveURL(/\/settings\/account$/);
    await page.goto("/coach");                                          // Journal is off: Coach and guide stays
    await expect(page).toHaveURL(/\/coach$/);
  });

  test("the Charts page opens on NIFTY 50 and on the symbol in the address", async ({ page }) => {
    await open(page, "/charts", "light");
    await expect(page.getByLabel("Symbol")).toHaveValue("NIFTY 50");
    await expect(page.getByRole("heading", { name: "What is this page?" })).toBeVisible();
    await page.getByRole("button", { name: "Close this explanation" }).click();
    await expect(page.getByRole("heading", { name: "What is this page?" })).toHaveCount(0);
    await page.getByLabel("Symbol").fill("reliance");
    await page.getByRole("button", { name: "Open", exact: true }).click();
    await expect(page).toHaveURL(/\/charts\/RELIANCE$/);
    await page.reload();
    await expect(page.getByRole("heading", { name: "What is this page?" })).toHaveCount(0);   // closed stays closed
  });
});

test.describe("v2 broker gate on data pages", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("no broker stored: every data page shows 'Connect your broker' and none of its content", async ({ page }) => {
    await open(page, "/", "light", { features: { ui_v2: true }, brokers: [] });
    for (const path of ["/", "/charts", "/option-chain", "/screener", "/signals", "/positions", "/portfolio", "/copilot/market-pulse"]) {
      await page.goto(path);
      const gate = page.locator("[data-broker-gate='no-broker']");
      await expect(gate, path).toBeVisible();
      await expect(gate.getByRole("heading", { name: "Connect your broker" })).toBeVisible();
      await expect(page.locator("main [data-broker-gate] ~ *")).toHaveCount(0);
    }
    await page.getByRole("link", { name: "Connect a broker" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.locator("[data-broker-gate]")).toHaveCount(0);       // Settings is not a data page
  });

  test("a stored broker without today's login: 'Log in to Upstox'", async ({ page }) => {
    await open(page, "/positions", "light", { features: { ui_v2: true }, brokers: [{ ...CONNECTED[0], needs_login: true, token_status: "EXPIRED" }] });
    await expect(page.locator("[data-broker-gate='login-needed']").getByRole("button", { name: "Log in to Upstox" })).toBeVisible();
  });

  test("the broker check fails: closed, never the page", async ({ page }) => {
    await open(page, "/positions", "light", { features: { ui_v2: true } });          // /broker/token-status answers 404
    await expect(page.locator("[data-broker-gate='unavailable']")).toBeVisible();
  });
});

test.describe("flag off", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  test("today's shell, no /charts route, no redirects, no gate", async ({ page }) => {
    await open(page, "/scanner", "dark", { brokers: [] });
    await expect(page).toHaveURL(/\/scanner$/);
    await expect(page.locator("[data-shell='v2']")).toHaveCount(0);
    await expect(page.locator("[data-broker-gate]")).toHaveCount(0);
    await page.goto("/charts");
    await expect(page.getByText("This page does not exist.")).toBeVisible();
    // A v2 page's new address (a bookmark, or Settings -> Account after signing out) goes back to today's address.
    await page.goto("/screener/custom?preset=gap");
    await expect(page).toHaveURL(/\/scanner\?preset=gap$/);
  });
});
