import { expect, test, type Page } from "@playwright/test";
import { mockApi, type MockOptions } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M60 (sidebar / tabs, owner order approved 30 Oct with the five defaults) and M57 (sidebar show / hide, owner order
 * 30 Oct) in the v2 shell:
 * - the five-section sidebar at 1440 and its 56 px rail (light and dark baselines of the sidebar itself);
 * - the menu button left of the title, the shortcut `[`, focus kept on the button, the choice saved on the server
 *   (`/api/me/prefs/shell`) and read back after a reload with this browser's copy cleared;
 * - the tablet default (the rail at 768) and the content taking the freed width;
 * - the phone: the bottom bar (first four entries + More), the More sheet and the menu sheet (390, light and dark).
 * After an intended change: `npx playwright test --project=phone e2e/sidebar-m57-m60-v2.e2e.ts --update-snapshots`.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ALL_ON = { ui_v2: true, terminal: true, screener_v2: true, markets_view: true, ai_copilot: true, learn: true, idea_library: true,
  discipline_engine: true, chart_analyze: true, watchlists: true, colour_v2: true };
const FULL: MockOptions = { features: ALL_ON, brokers: CONNECTED };
const THEMES = ["light", "dark"] as const;
const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };

/** A stand-in for the server's per-user prefs: GET reads what was PUT. Returns what the page saved. */
async function prefsServer(page: Page, start: Record<string, unknown> = {}) {
  const shell: { sidebar: string | null } = { sidebar: null, ...start } as { sidebar: string | null };
  const puts: unknown[] = [];
  await page.route(/\/api(?:\/v1)?\/me\/prefs(?:\/shell)?$/, async (route) => {
    const req = route.request();
    if (req.method() === "PUT") {
      const body = req.postDataJSON() as { sidebar: string };
      puts.push(body);
      shell.sidebar = body.sidebar;
      return route.fulfill({ json: { namespace: "shell", version: puts.length, value: shell } });
    }
    return route.fulfill({ json: { prefs: { shell }, versions: { shell: shell.sidebar ? 1 : 0 } } });
  });
  return { puts, shell };
}

async function open(page: Page, path: string, theme: "dark" | "light", options: MockOptions = FULL, before?: (p: Page) => Promise<void>) {
  await page.clock.install({ time: new Date("2026-10-08T10:30:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, options);
  await before?.(page);
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; transition: none !important; }" });
  await page.waitForLoadState("networkidle");
}

test.describe("M60 + M57 desktop 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`M60 sidebar: five sections, one accent each (${theme})`, async ({ page }) => {
      await open(page, "/terminal", theme, FULL, (p) => prefsServer(p));
      const aside = page.getByRole("complementary", { name: "Navigation" });
      await expect(aside).toHaveAttribute("data-sidebar-mode", "expanded");
      await expect(aside.getByRole("heading")).toHaveText(["Trade", "Find", "Build", "Learn & AI", "Account"]);
      await expect(aside.getByRole("navigation", { name: "Pages" }).getByRole("link")).toHaveText([
        "Today", "Terminal", "My trades", "Find setups", "Signals", "Sectors & indices", "Strategies", "AI Trading Bot", "AI Copilot",
        "Learn", "Settings",
      ]);
      await expect(aside.getByRole("link", { name: "Terminal" })).toHaveAttribute("aria-current", "page");
      await expect(aside).toHaveScreenshot(`m60-sidebar-1440-${theme}.png`, SHOT);
    });

    test(`M57 rail: 56 px of icons with tooltips (${theme})`, async ({ page }) => {
      await open(page, "/terminal", theme, FULL, (p) => prefsServer(p, { sidebar: "rail" }));
      const aside = page.getByRole("complementary", { name: "Navigation" });
      await expect(aside).toHaveAttribute("data-sidebar-mode", "rail");
      expect((await aside.boundingBox())?.width).toBe(56);
      const links = aside.getByRole("navigation", { name: "Pages" }).getByRole("link");
      await expect(links).toHaveCount(11);
      for (const link of await links.all()) {
        const name = (await link.getAttribute("title")) ?? "";
        expect(name.length).toBeGreaterThan(0);
        await expect(link).toHaveAccessibleName(name);
      }
      await expect(aside).toHaveScreenshot(`m57-rail-1440-${theme}.png`, SHOT);
    });
  }

  test("M57: the button and `[` hide and show the menu; focus stays on the button; the page title is never hidden", async ({ page }) => {
    let server!: Awaited<ReturnType<typeof prefsServer>>;
    // The Terminal: a full-width page whose panels take the width the menu frees (U7-3).
    await open(page, "/terminal", "light", FULL, async (p) => { server = await prefsServer(p); });
    const aside = page.getByRole("complementary", { name: "Navigation" });
    const main = page.locator("main#main");
    const before = (await main.boundingBox())!.width;
    const hide = page.getByRole("button", { name: "Hide menu" });
    await hide.click();
    await expect(aside).toHaveAttribute("data-sidebar-mode", "rail");
    const show = page.getByRole("button", { name: "Show menu" }).first();
    await expect(show).toBeFocused();                                           // focus returns to (stays on) the button
    await expect(page.locator("[data-topbar-title]")).toHaveText("Terminal");
    expect((await main.boundingBox())!.width).toBeGreaterThan(before + 150);    // the content takes the freed width
    await expect.poll(() => server.puts).toEqual([{ sidebar: "rail" }]);
    // The shortcut, outside a text field.
    await page.keyboard.press("[");
    await expect(aside).toHaveAttribute("data-sidebar-mode", "expanded");
    // Typing "[" in a field (the search box) never hides the menu.
    await page.keyboard.press("Control+k");
    await expect(page.locator("[data-command-bar]")).toBeVisible();
    await page.keyboard.type("[");
    // (The dialog hides the rest of the page from assistive tech, so the sidebar is found by its marker here.)
    await expect(page.locator("aside[data-sidebar-mode]")).toHaveAttribute("data-sidebar-mode", "expanded");
  });

  test("M57: the choice is saved per user and comes back after a reload, from the server (this browser's copy cleared)", async ({ page }) => {
    let server!: Awaited<ReturnType<typeof prefsServer>>;
    await open(page, "/", "light", FULL, async (p) => { server = await prefsServer(p); });
    await page.getByRole("button", { name: "Hide menu" }).click();
    await expect.poll(() => server.shell.sidebar).toBe("rail");
    await page.addInitScript(() => { try { localStorage.removeItem("atp_sidebar"); } catch { /* storage unavailable */ } });
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("complementary", { name: "Navigation" })).toHaveAttribute("data-sidebar-mode", "rail");
  });
});

test.describe("M57 tablet 768", () => {
  test.use({ viewport: { width: 768, height: 1024 } });
  test("never chosen: the rail on a tablet; the title stays whole", async ({ page }) => {
    await open(page, "/strategies", "light", FULL, (p) => prefsServer(p));
    await expect(page.getByRole("complementary", { name: "Navigation" })).toHaveAttribute("data-sidebar-mode", "rail");
    const title = page.locator("[data-topbar-title]");
    await expect(title).toHaveText("Strategies");
    expect(await title.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
});

test.describe("M60 + M57 phone 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("the bottom bar is the first four entries + More; the layout rules hold", async ({ page }) => {
    await open(page, "/", "dark");
    const bar = page.getByRole("navigation", { name: "Quick navigation" });
    await expect(bar.locator("a, button")).toHaveText(["Today", "Terminal", "My trades", "Find setups", "More"]);
    await expectPhoneLayout(page);
  });

  for (const theme of THEMES) {
    test(`More lists the rest under the section titles (${theme})`, async ({ page }) => {
      await open(page, "/", theme);
      await page.getByRole("button", { name: "More pages" }).click();
      const sheet = page.getByRole("dialog", { name: "All pages" });
      await expect(sheet).toBeVisible();
      for (const section of ["Find", "Build", "Learn & AI", "Account"]) await expect(sheet.getByRole("heading", { name: section, exact: true })).toBeVisible();
      await expect(sheet.getByRole("link", { name: "Terminal" })).toHaveCount(0);
      await expect(page).toHaveScreenshot(`m60-more-390-${theme}.png`, SHOT);
    });

    test(`M57 U7-2: the menu button opens every page as a sheet (${theme})`, async ({ page }) => {
      await open(page, "/positions", theme);
      const button = page.getByRole("button", { name: "Show menu" });
      await expect(button).toBeVisible();
      await button.click();
      const sheet = page.getByRole("dialog", { name: "Menu" });
      await expect(sheet).toBeVisible();
      for (const section of ["Trade", "Find", "Build", "Learn & AI", "Account"]) await expect(sheet.getByRole("heading", { name: section, exact: true })).toBeVisible();
      await expect(sheet.getByRole("link", { name: "Terminal" })).toBeVisible();
      await expect(page).toHaveScreenshot(`m57-menu-390-${theme}.png`, SHOT);
      await page.keyboard.press("Escape");
      await expect(sheet).toBeHidden();
      await expect(button).toBeFocused();
    });
  }
});

/**
 * #759 review (M57): a page title is never cut - no "…", no `truncate` - at 320 and 375 px (and on a tablet with the rail).
 * #741's rule: one line at the normal size, then one size down, then a wrap between words; the bar never runs past the edge.
 */
const LONG_TITLES: [string, string][] = [
  ["/positions/results", "How am I doing?"], ["/markets", "Sectors & indices"], ["/settings/trading-defaults", "My trading defaults"],
  ["/system-logs", "Your account activity"], ["/deployments", "Trading bot"], ["/screener", "Find setups"],
];
for (const size of [{ width: 320, height: 640, phone: true }, { width: 375, height: 812, phone: true }, { width: 768, height: 1024, phone: false }]) {
  test.describe(`#759 review: the whole title shows at ${size.width}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.phone, hasTouch: size.phone, deviceScaleFactor: 1 });
    for (const [path, title] of LONG_TITLES) {
      test(`"${title}" is never cut`, async ({ page }) => {
        await open(page, path, "light", FULL, (p) => prefsServer(p));
        const el = page.locator("[data-topbar-title]");
        await expect(el).toHaveText(title);
        const m = await el.evaluate((node) => {
          const style = getComputedStyle(node);
          const box = node.getBoundingClientRect();
          const row = node.closest("header")!.firstElementChild as HTMLElement;
          return { cls: node.className, ellipsis: style.textOverflow, clipped: node.scrollWidth > node.clientWidth + 1,
            right: box.right, rowOverflow: row.scrollWidth - row.clientWidth, viewport: window.innerWidth };
        });
        expect(m.cls.split(/\s+/).filter((c) => /(^|:)truncate$/.test(c)), "no truncate class").toEqual([]);
        expect(m.ellipsis, "no text-overflow ellipsis").not.toBe("ellipsis");
        expect(m.clipped, "the text fits its box").toBe(false);
        expect(m.right).toBeLessThanOrEqual(m.viewport);
        expect(m.rowOverflow, "nothing in the bar runs past the edge").toBeLessThanOrEqual(0);
      });
    }
  });
}
