import { expect, test, type Page } from "@playwright/test";
import { mockDashboard } from "./dashboardMocks";
import { mockApi, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * UI2-1 (UI v2 proposal §2, §4, §9, §12): the v2 shell with the `ui_v2` flag on (mockApi answers /system/features).
 * - Baselines `shell-v2-<width>-<theme>.png` at 1440, 390 and 360 (and the More sheet at 390). The old baselines in
 *   __screenshots__ are not touched: with the flag off nothing changes (mobile.e2e.ts keeps proving it).
 * - Every phone page in the v2 shell passes the PW2 layout rules (no sideways scroll, 44 px targets, nothing hidden
 *   behind the bottom bar).
 * - The navigation model at work: the M60 sidebar (five sections), the merged pages' tabs, flag-hidden items, Admin for SUPER_ADMIN only,
 *   the More sheet and its search, the command bar (Ctrl K), the redirects, the broker gate on data pages.
 * After an intended change: `npx playwright test --project=phone e2e/shell-v2.e2e.ts --update-snapshots`, check the
 * new images, commit.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const V2: MockOptions = { features: { ui_v2: true }, brokers: CONNECTED };

/** `time` and `before` (more routes, registered after mockApi so they answer first) are for the 1440 shot only. */
async function open(page: Page, path: string, theme: "dark" | "light", options: MockOptions = V2, storage: Record<string, string> = {},
  extra: { time?: string; before?: (page: Page) => Promise<void> } = {}) {
  await page.clock.install({ time: new Date(extra.time ?? "2026-10-08T10:30:00+05:30") });
  await page.addInitScript(([t, extra]) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      for (const [k, v] of Object.entries(extra)) localStorage.setItem(k, v);
    } catch { /* storage unavailable */ }
  }, [theme, storage] as const);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, options);
  await extra.before?.(page);
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
}

const PHONE_PAGES: [string, string][] = [
  ["dashboard", "/"], ["charts", "/charts"], ["screener", "/screener"], ["option-chain", "/option-chain"],
  ["positions", "/positions"], ["orders", "/positions/orders"], ["alerts", "/notifications"], ["settings", "/settings"],
  ["copilot", "/copilot/market-pulse"], ["custom-scan", "/screener/custom"], ["signals", "/signals"],
  // UX3-9: the merged pages' tab bars on a phone.
  ["money", "/positions/money"], ["strategies", "/strategies"], ["backtest", "/strategies/test"], ["performance", "/analytics"],
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

    test("M60: the bottom bar is the first four entries and More - each at least 44 px", async ({ page }) => {
      await open(page, "/", "dark");
      const bar = page.getByRole("navigation", { name: "Quick navigation" });
      // Without the Terminal (flag off here) its stand-ins Charts and Option chain come first.
      await expect(bar.locator("a, button")).toHaveText(["Today", "Charts", "Chain", "My trades", "More"]);
      for (const box of await bar.locator("a, button").evaluateAll((els) => els.map((e) => e.getBoundingClientRect()))) {
        expect(box.width).toBeGreaterThanOrEqual(44);
        expect(box.height).toBeGreaterThanOrEqual(44);
      }
      await bar.getByRole("link", { name: "Charts" }).click();
      await expect(page).toHaveURL(/\/charts$/);
      // UX3-9: a My trades tab keeps My trades lit in the bar.
      await page.goto("/positions/orders");
      await expect(bar.getByRole("link", { name: "My trades" })).toHaveAttribute("aria-current", "page");
    });

    test("More opens a sheet with search and every other page; a choice closes it", async ({ page }) => {
      await open(page, "/", "dark");
      await page.getByRole("button", { name: "More pages" }).click();
      const sheet = page.getByRole("dialog", { name: "All pages" });
      await expect(sheet).toBeVisible();
      for (const section of ["Find", "Build", "Learn & AI", "Account"]) {
        await expect(sheet.getByRole("heading", { name: section, exact: true })).toBeVisible();
      }
      await expect(sheet.getByRole("link", { name: "Signals" })).toBeVisible();
      await expect(sheet.getByRole("link", { name: "Strategies" })).toBeVisible();
      await expect(sheet.getByRole("link", { name: "My trades" })).toHaveCount(0);      // in the bar already
      await expect(sheet.getByRole("link", { name: "Factor Lab" })).toHaveCount(0);     // UX3-9: found by search
      await expect(sheet.getByRole("link", { name: "Admin", exact: true })).toHaveCount(0);  // an OWNER, not the operator
      for (const box of await sheet.locator("a").evaluateAll((els) => els.map((e) => e.getBoundingClientRect().height))) {
        expect(box).toBeGreaterThanOrEqual(44);
      }
      // Today's name finds the renamed page.
      await sheet.getByRole("searchbox", { name: "Search pages" }).fill("backtesting");
      await expect(sheet.getByRole("link")).toHaveCount(1);
      await sheet.getByRole("link", { name: /Test a strategy/ }).click();
      await expect(page).toHaveURL(/\/strategies\/test$/);                    // UX3-9: Strategies › Test
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
      await page.locator("[data-bottom-nav-v2] a", { hasText: "My trades" }).click();
      await expect(page).toHaveURL(/\/positions$/);
      await expect(sheet).toBeHidden();
      // The page already on screen: the sheet still closes (review M4).
      await more.click();
      await expect(sheet).toBeVisible();
      await page.locator("[data-bottom-nav-v2] a", { hasText: "My trades" }).click();
      await expect(sheet).toBeHidden();
    });

    test("closing the More sheet puts focus back on More (review M2)", async ({ page }) => {
      await open(page, "/", "dark");
      const more = page.getByRole("button", { name: "More pages" });
      await more.click();
      await expect(page.getByRole("dialog", { name: "All pages" })).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog", { name: "All pages" })).toBeHidden();
      await expect(more).toBeFocused();
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

  test("a page opened over plain http says in the install entry why it cannot install - no banner, no domain advice", async ({ page }) => {
    await page.addInitScript(() => { Object.defineProperty(window, "isSecureContext", { value: false }); });
    await open(page, "/", "dark", V2);

    await expect(page.locator("[data-install='unavailable']")).toHaveCount(0);
    await page.getByRole("button", { name: "More pages" }).click();
    await page.getByRole("button", { name: "Install the app" }).click();
    await expect(page.locator("[data-install-message]")).toHaveText(
      "Install works only on the app's secure address. Open this page with https:// and try again.");
  });
});

test.describe("v2 shell 1440 desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`shell looks the same (${theme})`, async ({ page }) => {
      // UI2-2: `/` is the v2 dashboard, whose lower blocks show at 1440. Every read it makes is answered (the dashboard
      // spec's made-up replies, at its date) and the shot waits for its blocks, so it never captures 404 / error states.
      await open(page, "/", theme, V2, {}, { time: "2026-10-19T11:00:00+05:30", before: (p) => mockDashboard(p) });
      await expect(page.getByRole("complementary", { name: "Navigation" })).toBeVisible();
      await expect(page.locator("[data-block='today']")).toBeVisible();
      await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
      await expect(page).toHaveScreenshot(`shell-v2-1440-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
    });
  }

  test("M60: five sections in task order; the page on screen is marked, a tab or a Settings page lights up its entry", async ({ page }) => {
    await open(page, "/", "light", { ...V2, features: { ui_v2: true, ai_copilot: true, screener_v2: true } });
    const nav = page.getByRole("navigation", { name: "Pages" });
    await expect(nav.locator("button[aria-expanded]")).toHaveCount(0);                  // nothing folds away
    await expect(nav.getByRole("heading")).toHaveText(["Trade", "Find", "Build", "Learn & AI", "Account"]);
    await expect(nav.getByRole("link")).toHaveText([
      "Today", "Charts", "Option chain", "My trades", "Find setups", "Signals", "Strategies", "AI Trading Bot", "AI Copilot",
      "News & events", "Settings",
    ]);
    await expect(nav.getByRole("link", { name: "Today" })).toHaveAttribute("aria-current", "page");
    await page.goto("/signals");
    await expect(nav.getByRole("link", { name: "Signals" })).toHaveAttribute("aria-current", "page");
    // A merged page's tab and a Settings page light up their entry.
    await page.goto("/strategies/test");
    await expect(nav.getByRole("link", { name: "Strategies" })).toHaveAttribute("aria-current", "page");
    await page.goto("/risk-management");
    await expect(nav.getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "page");
  });

  test("M60: My trades, Strategies, Learn and Settings are tabs (at most four), each tab at its own address", async ({ page }) => {
    await open(page, "/positions", "light", { ...V2, features: { ui_v2: true, discipline_engine: true, idea_library: true, learn: true } });
    const tabs = page.getByRole("navigation", { name: "My trades" });
    await expect(tabs.getByRole("link")).toHaveText(["Positions", "Orders", "Money", "Results"]);
    await expect(tabs.getByRole("link", { name: "Positions" })).toHaveAttribute("aria-current", "page");
    await tabs.getByRole("link", { name: "Orders" }).click();
    await expect(page).toHaveURL(/\/positions\/orders$/);
    await expect(tabs.getByRole("link", { name: "Orders" })).toHaveAttribute("aria-current", "page");
    await expect(page).toHaveTitle("My trades · AiAlgoDesk");
    await tabs.getByRole("link", { name: "Money" }).click();
    await expect(page).toHaveURL(/\/positions\/money$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/positions\/orders$/);

    await page.goto("/strategies");
    await expect(page.getByRole("navigation", { name: "Strategies" }).getByRole("link")).toHaveText(["Library", "Build", "Test"]);
    // Performance is My trades › Results; Journal (and, while the AI Copilot is off, the Coach) are its parts.
    await page.goto("/analytics");
    await expect(page).toHaveURL(/\/positions\/results$/);
    await expect(tabs.getByRole("link", { name: "Results" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("navigation", { name: "Results" }).getByRole("link")).toHaveText(["Summary", "Journal", "Coach"]);
    await page.goto("/learn/news");
    await expect(page.getByRole("navigation", { name: "Learn" }).getByRole("link")).toHaveText(["Lessons", /Words explained/, "Ideas", "News & events"]);
    await page.goto("/settings/alerts");
    await expect(page.getByRole("navigation", { name: "Settings" }).getByRole("link")).toHaveText([/Account & broker/, "Trading", "AI", "App"]);
    await expect(page.getByRole("navigation", { name: "Trading" }).getByRole("link")).toHaveText(["My trading defaults", "Risk limits", "Alerts", "Inbox"]);
  });

  test("pages whose feature is off are not listed; Admin only for the platform operator", async ({ page }) => {
    await open(page, "/", "light", { ...V2, features: { ui_v2: true } });
    const nav = page.getByRole("navigation", { name: "Pages" });
    await expect(nav.getByRole("link", { name: "Settings" })).toBeVisible();
    for (const hidden of ["AI Copilot", "Find setups", "Sectors & indices", "Terminal", "Learn"]) await expect(nav.getByRole("link", { name: hidden, exact: true })).toHaveCount(0);
    await expect(nav.getByRole("link", { name: "Admin", exact: true })).toHaveCount(0);
    // The tools taken out of the user navigation are found by search.
    for (const gone of ["Factor Lab", "Contract specs", "Activity log", "Company research", "Team", "Notifications", "Broker"]) {
      await expect(nav.getByRole("link", { name: gone, exact: true })).toHaveCount(0);
    }

    await page.route(/\/api(?:\/v1)?\/auth\/me$/, (route) => route.fulfill({ json: { id: 1, email: "ops@example.com", tenant_id: 1, role: "SUPER_ADMIN", email_verified: true } }));
    await page.route(/\/api(?:\/v1)?\/system\/features$/, (route) => route.fulfill({ json: { ui_v2: true, ai_copilot: true, discipline_engine: true, marketplace: true } }));
    await page.reload();
    await page.waitForLoadState("networkidle");
    for (const shown of ["AI Copilot", "Admin"]) await expect(nav.getByRole("link", { name: shown, exact: true })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Admin", exact: true })).toHaveCount(1);
  });

  test("Ctrl K opens the command bar; typing and Enter go to the page", async ({ page }) => {
    await open(page, "/", "light");
    await page.route(/\/api(?:\/v1)?\/instrument-master\/search/, (r) => r.fulfill({ json: [] }));   // no symbol matches here
    await page.keyboard.press("Control+k");
    const bar = page.locator("[data-command-bar]");
    await expect(bar).toBeVisible();
    await bar.getByRole("combobox", { name: "Search" }).fill("risk");
    await expect(bar.getByText("Searching…")).toHaveCount(0);
    await expect(bar.getByRole("option").first()).toHaveText(/Risk limits/);
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/settings\/risk-limits$/);                   // M64 RM-5: Risk limits is the Settings page now
    await expect(bar).toBeHidden();
    // The top bar's search box opens it too; a page whose feature is off is not offered.
    await page.getByRole("button", { name: /^Search \(/ }).click();
    await bar.getByRole("combobox").fill("journal");
    await expect(bar.getByText('No symbol or page matches "journal".')).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(bar).toBeHidden();
    await expect(page.getByRole("button", { name: /^Search \(/ })).toBeFocused();      // focus back on the opener (M2)
  });

  test("UX-2: symbols first - a share opens its chart and becomes a recent symbol; 'nifty 22700 ce' finds the contract", async ({ page }) => {
    await open(page, "/", "light");
    await page.route(/\/api(?:\/v1)?\/instrument-master\/search/, (r) => {
      const type = new URL(r.request().url()).searchParams.get("type");
      return r.fulfill({ json: type === "EQ" ? [{ tradingsymbol: "RELIANCE", exchange: "NSE", name: "Made-up Reliance", instrument_type: "EQ" }] : [] });
    });
    await page.route(/\/api(?:\/v1)?\/charts\/contracts\/search/, (r) => {
      const url = new URL(r.request().url());
      expect(url.searchParams.get("underlying")).toBe("NIFTY");
      expect(url.searchParams.get("kind")).toBe("CE");
      expect(url.searchParams.get("near")).toBe("22700");
      return r.fulfill({ json: { underlying: "NIFTY", total: 1, note: "", contracts: [
        { key: "NFO:NIFTY:CE:2026-10-28:22700", tradingsymbol: "NIFTYMADEUPCE", underlying: "NIFTY", kind: "CE", expiry: "2026-10-28", strike: 22700, lot_size: 75 }] } });
    });
    const bar = page.locator("[data-command-bar]");
    await page.keyboard.press("Control+k");
    await bar.getByRole("combobox").fill("reli");
    await expect(bar.getByRole("option").first()).toContainText("RELIANCE");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/charts\/RELIANCE$/);
    await page.keyboard.press("Control+k");
    await expect(bar.locator("[data-command-group='symbols']")).toHaveText("Recent symbols");      // empty field: recent first
    await expect(bar.getByRole("option").first()).toContainText("RELIANCE");
    await bar.getByRole("combobox").fill("nifty 22700 ce");
    await expect(bar.getByRole("option").first()).toContainText("NIFTY 28 Oct 22700 CE");
    await page.keyboard.press("Escape");
  });

  test("Ctrl K is left alone while another dialog is open", async ({ page }) => {
    await open(page, "/", "light");
    await page.getByRole("button", { name: "Send feedback" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Control+k");
    await expect(page.locator("[data-command-bar]")).toHaveCount(0);
  });

  test("old addresses forward to the new ones with the flag on", async ({ page }) => {
    await open(page, "/scanner", "light");
    await expect(page).toHaveURL(/\/screener\/custom$/);
    await page.goto("/options-builder?underlying=NIFTY");
    await expect(page).toHaveURL(/\/option-chain\/plan\?underlying=NIFTY$/);
    await page.goto("/account");
    await expect(page).toHaveURL(/\/settings\/account$/);
    // UX3-9: the merged pages' old addresses open their tabs, keeping the query.
    for (const [from, to] of [["/orders", "/positions/orders"], ["/portfolio", "/positions/money"], ["/strategy-builder", "/strategies/build"],
      ["/backtest?strategy=orb", "/strategies/test?strategy=orb"], ["/journal", "/positions/results/journal"], ["/coach", "/analytics/coach"],
      ["/journal/coach", "/analytics/coach"],
      // M60: the moved pages' old addresses open their new homes.
      ["/analytics", "/positions/results"], ["/analytics/journal", "/positions/results/journal"], ["/broker", "/settings/broker"],
      ["/news-events", "/learn/news"]]) {
      await page.goto(from);
      await expect(page, from).toHaveURL(new RegExp(`${to.replace(/[?]/g, "\\?")}$`));
    }
  });

  test("the Charts page opens on NIFTY 50 and on the symbol in the address", async ({ page }) => {
    await open(page, "/charts", "light");
    await expect(page.getByRole("combobox", { name: "Symbol" })).toHaveValue("NIFTY 50");
    // UX-6: no "About this page" card above the chart; its line is behind the (?) beside the title.
    await expect(page.locator("[data-page-intro]")).toHaveCount(0);
    await page.getByRole("button", { name: "Help: Charts" }).click();
    await expect(page.locator("[data-page-help='charts']")).toContainText("Search a symbol");
    await page.keyboard.press("Escape");
    // UI2-4b2: the shared symbol picker; with no match, Enter opens the symbol as typed.
    await page.getByRole("combobox", { name: "Symbol" }).fill("reliance");
    await page.getByRole("combobox", { name: "Symbol" }).press("Enter");
    await expect(page).toHaveURL(/\/charts\/RELIANCE$/);
    await page.reload();
    await expect(page.locator("[data-page-intro]")).toHaveCount(0);
  });
});

test.describe("v2 broker gate on data pages", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("no broker stored: every data page shows 'Connect your broker' and none of its content", async ({ page }) => {
    // UX1: Find setups and the Copilot open only while their flags are on (off, the address says "Coming soon" - tested
    // below); `ai_copilot` is ON by default on the server.
    await open(page, "/", "light", { features: { ui_v2: true, screener_v2: true, ai_copilot: true }, brokers: [] });
    for (const path of ["/", "/charts", "/option-chain", "/screener", "/signals", "/positions", "/portfolio", "/copilot/market-pulse"]) {
      await page.goto(path);
      const gate = page.locator("[data-broker-gate='no-broker']");
      await expect(gate, path).toBeVisible();
      await expect(gate.getByRole("heading", { name: "Connect your broker" })).toBeVisible();
      await expect(page.locator("main [data-broker-gate] ~ *")).toHaveCount(0);
      if (path === "/positions") {
        // UI2-9a: My positions shows the gate itself and, under it, only the trader's own open positions with their exits
        // (ADR-0004) - never the summary, the closed trades or any price or P&L figure.
        await expect(page.locator("[data-block='summary']")).toHaveCount(0);
        await expect(page.locator("[data-block='history']")).toHaveCount(0);
        await expect(page.locator("main [data-col]")).toHaveCount(0);
        await expect(page.locator("main").getByText(/[+−]₹/)).toHaveCount(0);
      }
    }
    await page.getByRole("link", { name: "Connect a broker" }).click();
    await expect(page).toHaveURL(/\/broker$/);                             // P2b: the Broker page
    await expect(page.locator("[data-broker-gate]")).toHaveCount(0);       // the Broker page is not a data page
  });

  test("percent-encoded and undecodable addresses are gated too (review M1)", async ({ page }) => {
    await open(page, "/", "light", { features: { ui_v2: true }, brokers: [] });
    for (const path of ["/option%2Dchain", "/%70ositions", "/p%6Frtfolio", "/%63harts"]) {
      await page.goto(path);
      await expect(page.locator("[data-broker-gate='no-broker']"), path).toBeVisible();
      await expect(page.getByText(/sample/i), path).toHaveCount(0);
    }
    // An undecodable address (the preview server refuses to serve one, so it is reached inside the app): fails closed.
    await page.evaluate(() => { window.history.pushState({}, "", "/%E0%A4"); window.dispatchEvent(new PopStateEvent("popstate")); });
    await expect(page.locator("[data-broker-gate='no-broker']")).toBeVisible();
    await page.goto("/option%2Dchain");
    await expect(page.locator("[data-shell='v2'] header")).toContainText("Option chain");
  });

  test("while the flags are not known yet, a data page shows neither today's page nor sample data (review M3)", async ({ page }) => {
    await open(page, "/settings", "light", { features: { ui_v2: true }, brokers: [] });
    let release: () => void = () => undefined;
    const held = new Promise<void>((r) => { release = r; });
    await page.route(/\/api(?:\/v1)?\/system\/features$/, async (route) => { await held; await route.fulfill({ json: { ui_v2: true } }); });
    await page.goto("/option-chain");
    await expect(page.locator("[data-shell='waiting']")).toBeVisible();
    await expect(page.getByText(/sample/i)).toHaveCount(0);
    await expect(page.locator("main")).toHaveCount(0);
    release();
    await expect(page.locator("[data-broker-gate='no-broker']")).toBeVisible();
  });

  test("flag on, flags 1 s late: the waiting screen, then the gate - never ungated content", async ({ page }) => {
    await open(page, "/settings", "light", { features: { ui_v2: true }, brokers: [] });
    await page.route(/\/api(?:\/v1)?\/system\/features$/, async (route) => {
      await new Promise((r) => setTimeout(r, 1000));
      await route.fulfill({ json: { ui_v2: true } });
    });
    // Record every state the page passes through: today's shell (its menu button) must never appear on a data page.
    await page.addInitScript(() => {
      const seen: string[] = [];
      (window as unknown as { __seen: string[] }).__seen = seen;
      new MutationObserver(() => {
        if (document.querySelector("[aria-label='Open navigation menu']")) seen.push("today");
        if (document.querySelector("[data-shell='waiting']")) seen.push("waiting");
        if (/sample/i.test(document.body?.innerText ?? "")) seen.push("sample");
      }).observe(document, { childList: true, subtree: true });
    });
    await page.goto("/positions");
    await expect(page.locator("[data-broker-gate='no-broker']")).toBeVisible();
    const seen = await page.evaluate(() => (window as unknown as { __seen: string[] }).__seen);
    expect(seen).toContain("waiting");
    expect(seen).not.toContain("today");
    expect(seen).not.toContain("sample");
  });

  test("flags that never answer: today's shell within about 3 s, not a blank screen forever (review R2-1)", async ({ page }) => {
    await open(page, "/settings", "light", { brokers: [] });
    await page.route(/\/api(?:\/v1)?\/system\/features$/, () => new Promise(() => undefined));   // never answers
    const started = Date.now();
    await page.goto("/positions");
    await expect(page.locator("[data-shell='waiting']")).toBeVisible();
    await expect(page.locator("[data-shell='waiting']").getByText("Loading")).toBeVisible();     // visible, not only for readers
    await expect(page.getByRole("button", { name: "Open navigation menu" })).toBeVisible({ timeout: 6_000 });
    expect(Date.now() - started).toBeLessThan(6_000);
    await expect(page.locator("[data-shell='waiting']")).toHaveCount(0);
  });

  test("a failed flags read keeps today's shell (the kill switch)", async ({ page }) => {
    await open(page, "/settings", "light", { brokers: [] });                            // /system/features answers 404
    await page.goto("/option-chain");
    await expect(page.locator("[data-shell='v2']")).toHaveCount(0);
    await expect(page.locator("[data-shell='waiting']")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Open navigation menu" })).toBeVisible();
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
    // UX3-9: a merged page's tab goes back to today's page, and today's sidebar is unchanged.
    await page.goto("/positions/orders");
    await expect(page).toHaveURL(/\/orders$/);
    await page.goto("/strategies/test");
    await expect(page).toHaveURL(/\/backtest$/);
    await expect(page.locator("[data-page-tabs]")).toHaveCount(0);
  });
});

test.describe("UX1 sign-in screen and Coming soon", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("a page whose flag is off says 'Coming soon' at its address and is not in the navigation", async ({ page }) => {
    await open(page, "/screener", "light", { features: { ui_v2: true }, brokers: CONNECTED });
    await expect(page.locator("[data-state='coming-soon']")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Coming soon" })).toBeVisible();
    await expect(page.locator("main")).not.toContainText(/screener_v2|not turned on|Not found/);
    await page.getByRole("button", { name: "More pages" }).click();
    await expect(page.locator("#more-sheet").getByRole("link", { name: "Find setups" })).toHaveCount(0);
  });

  test("signed out: one sign-in card at any address - no navigation, no page data", async ({ page }) => {
    await open(page, "/charts", "light", { features: { ui_v2: true }, brokers: CONNECTED }, {}, {
      before: (p) => p.route(/\/api(?:\/v1)?\/auth\/(refresh|me)$/, (r) => r.fulfill({ status: 401, json: { detail: "Not authenticated" } })),
    });
    await expect(page.locator("[data-screen='sign-in']")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
    await expect(page.locator("[data-shell='v2'], nav[aria-label='Quick navigation']")).toHaveCount(0);
    await expect(page.getByText("Create an account")).toHaveCount(0);          // sign-up closed (the read 404s here)
    await expect(page.getByText("Continue with Google")).toHaveCount(0);       // GS-1: hidden until set up
    await expect(page).toHaveURL(/\/charts$/);                                 // the address stays as asked
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);   // no sideways scroll
  });
});
