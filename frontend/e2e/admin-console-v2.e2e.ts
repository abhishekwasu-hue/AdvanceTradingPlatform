import { expect, test, type Page } from "@playwright/test";
import { openAdmin, type Theme } from "./adminMocks";

/**
 * MO3 addition (a): the admin console in sections (ui_v2 on) at /admin - System, Controls, Users & business, Testing
 * tools, Support - and the Diagnostics drawer. A non-admin gets the plain "This page does not exist" card and the page
 * asks the server for nothing of the console.
 * - Baselines (NEW): `admin-<system|controls|users|testing|support>-v2-{1440,390}-{light,dark}.png` and
 *   `admin-diagnostics-v2-{1440,390}-{light,dark}.png`. Other baselines are untouched.
 *   MO3-A1 (23 Oct): `admin-support-v2-*` updated - the "View as this person" button and the "Recent views as a user" card.
 *   TM 9.4 (23 Oct): `admin-system-v2-*` updated - the "Chain history" card at the end of System.
 *   Trader pack (23 Oct): `admin-controls-v2-*` updated - the "Trader features" card; NEW
 *   `admin-trader-pack-v2-{1440,390}-{light,dark}.png` (the card alone, before "Enable trader features").
 *   ADM-1 (30 Oct): `admin-controls-v2-*` updated - Controls opens on its Safety group; NEW
 *   `admin-controls-<limits|flags>-v2-{1440,390}-{light,dark}.png` (Risk limits; Feature flags folded by category).
 * Every reply is made up (e2e/adminMocks.ts). After an intended change:
 * `npx playwright test --project=phone e2e/admin-console-v2.e2e.ts --update-snapshots`.
 */

const SHOT = { animations: "disabled" as const, fullPage: true, maxDiffPixelRatio: 0.02, timeout: 15_000 };
const SECTIONS = [
  { id: "system", path: "/admin", label: "System" },
  { id: "controls", path: "/admin?section=controls", label: "Controls" },
  { id: "users", path: "/admin?section=users", label: "Users & business" },
  { id: "testing", path: "/admin?section=testing", label: "Testing tools" },
  { id: "support", path: "/admin?section=support&user=12", label: "Support" },
] as const;

const consoleRoot = (page: Page) => page.locator("[data-admin-console]");

async function noSidewaysScroll(page: Page) {
  const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
  expect(r.scroll, "page wider than the screen").toBeLessThanOrEqual(r.width);
}

for (const size of [{ name: "1440", width: 1440, height: 900, mobile: false }, { name: "390", width: 390, height: 844, mobile: true }]) {
  test.describe(`Admin console ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as Theme[]) {
      for (const s of SECTIONS) {
        test(`${s.label} looks the same (${theme})`, async ({ page }) => {
          await openAdmin(page, s.path, { theme });
          await expect(consoleRoot(page)).toHaveAttribute("data-admin-console", s.id);
          await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
          await noSidewaysScroll(page);
          await expect(page).toHaveScreenshot(`admin-${s.id}-v2-${size.name}-${theme}.png`, SHOT);
        });
      }

      for (const group of ["limits", "flags"]) {
        test(`Controls › ${group} looks the same (${theme})`, async ({ page }) => {
          await openAdmin(page, `/admin?section=controls&group=${group}`, { theme });
          await expect(page.locator(`[data-controls-groups='${group}']`)).toBeVisible();
          await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
          await noSidewaysScroll(page);
          await expect(page).toHaveScreenshot(`admin-controls-${group}-v2-${size.name}-${theme}.png`, SHOT);
        });
      }

      test(`the Trader features card looks the same (${theme})`, async ({ page }) => {
        await openAdmin(page, "/admin?section=controls&group=features", { theme });
        const card = page.locator("[data-console-card='trader-pack']");
        await expect(card.locator("[data-trader-pack-summary]")).toContainText("4 of 7");
        await expect(card).toHaveScreenshot(`admin-trader-pack-v2-${size.name}-${theme}.png`, SHOT);
      });

      test(`the Diagnostics drawer looks the same (${theme})`, async ({ page }) => {
        await openAdmin(page, "/admin", { theme });
        await page.locator("[data-diagnostics-button]").click();
        const drawer = page.getByRole("dialog", { name: "Diagnostics" });
        await expect(drawer.locator("[data-diagnostics]")).toBeVisible();
        await expect(drawer).toContainText("5f2c9a1e0d7b4c3a");
        await expect(page).toHaveScreenshot(`admin-diagnostics-v2-${size.name}-${theme}.png`, { ...SHOT, fullPage: false });
      });
    }
  });
}

test.describe("Admin console behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const role of ["OWNER", "USER", "VIEWER"]) {
    test(`${role}: the plain not-found card, and nothing of the console is asked for`, async ({ page }) => {
      const asked = await openAdmin(page, "/admin?section=controls", { role });
      await expect(page.locator("main")).toContainText("This page does not exist.");
      await expect(page.locator("[data-admin-console]")).toHaveCount(0);
      await expect(page.locator("[data-diagnostics-button]")).toHaveCount(0);
      expect(asked.filter((p) => p.includes("/admin/"))).toEqual([]);
    });
  }

  test("System shows the chain history recorder: what is recorded, today's counts and gaps", async ({ page }) => {
    await openAdmin(page, "/admin");
    const card = page.locator("[data-console-card='chain-history']");
    await expect(card).toContainText("Recording");
    await expect(card).toContainText("Every 1 min");
    await expect(card.locator("[data-chain-row='NIFTY']")).toContainText("None");
    await expect(card.locator("[data-chain-row='SENSEX']")).toContainText("7 missing · 09:15–09:20, 10:12–10:14");
  });

  test("Trader features: enable turns the pack on, Undo puts it back, and the status says what needs setup", async ({ page }) => {
    const asked = await openAdmin(page, "/admin?section=controls&group=features");
    const card = page.locator("[data-console-card='trader-pack']");
    await expect(card.locator("[data-trader-pack-summary]")).toHaveText("4 of 7 trader features are on; 1 needs setup.");
    await expect(card.locator("[data-pack-flag='ai_copilot']")).toContainText("No AI key yet");
    await expect(card.getByRole("button", { name: "Undo" })).toBeDisabled();
    await card.getByRole("button", { name: "Enable trader features" }).click();
    await expect(card.getByRole("status")).toHaveText("3 features turned on.");
    await expect(card.locator("[data-trader-pack-summary]")).toHaveText("All trader features are on; 1 needs setup.");
    await expect(card.locator("[data-pack-flag='watchlists']")).toHaveAttribute("data-pack-state", "working");
    await expect(card.getByRole("button", { name: "Enable trader features" })).toBeDisabled();
    await card.getByRole("button", { name: "Undo" }).click();
    await expect(card.getByRole("status")).toHaveText("3 switches put back as they were.");
    await expect(card.locator("[data-pack-flag='watchlists']")).toHaveAttribute("data-pack-state", "off");
    expect(asked.filter((p) => p.includes("/trader-pack")).length).toBeGreaterThanOrEqual(3);
    const text = await card.innerText();
    expect(text).not.toMatch(/\b[a-z]+_[a-z0-9_]+\b/);        // plain words only: no flag names on the card
  });

  test("the sections switch in place and the address keeps the section", async ({ page }) => {
    await openAdmin(page, "/admin");
    const nav = page.getByRole("navigation", { name: "Admin sections" });
    await expect(nav.getByRole("link", { name: "System" })).toHaveAttribute("aria-current", "page");
    await expect(page.locator("[data-console-card='health']")).toContainText("paper only");
    await expect(page.locator("[data-worker='news_worker']")).toContainText("stale");
    await nav.getByRole("link", { name: "Controls" }).click();
    await expect(page).toHaveURL(/\/admin\?section=controls$/);
    await expect(page.locator("[data-console-card='phase0']")).toContainText("LIVE is locked");
    await expect(page.locator("[data-admin-cards='controls']")).toContainText("Global kill switch");
    // ADM-1: one control group at a time; the flags are folded by category until a filter or a search opens them.
    await expect(page.locator("[data-flag-area]").first()).toHaveCount(0);
    await page.getByRole("navigation", { name: "Control groups" }).getByRole("link", { name: "All flags" }).click();
    await expect(page).toHaveURL(/\/admin\?section=controls&group=flags$/);
    await expect(page.locator("[data-admin-cards='controls']")).not.toContainText("Global kill switch");
    await expect(page.locator("[data-flag='live_trading']")).toBeHidden();
    await page.locator("[data-flag-filter='off']").click();
    await expect(page.locator("[data-flag='live_trading']")).toBeVisible();
    await page.reload();
    await expect(consoleRoot(page)).toHaveAttribute("data-admin-console", "controls");
    await expect(page.locator("[data-controls-groups='flags']")).toBeVisible();
    await page.goBack();
    await expect(page.locator("[data-controls-groups='safety']")).toBeVisible();
    await page.goBack();
    await expect(consoleRoot(page)).toHaveAttribute("data-admin-console", "system");
  });

  test("Users & business: masked addresses, AI cost per organisation and per task, and Status opens Support", async ({ page }) => {
    await openAdmin(page, "/admin?section=users");
    const users = page.locator("[data-console-card='users']");
    await expect(users.locator("[data-user='12']")).toContainText("tr****@example.com");
    const main = await page.locator("main").innerText();
    expect(main).not.toContain("owner@example.com");            // the existing tenants table masks too in the console
    expect(main).not.toContain("newbie@example.com");
    const ai = page.locator("[data-console-card='ai-cost']");
    await expect(ai.locator("[data-ai-tenant='7']")).toContainText("Desk Seven");
    await expect(ai.locator("[data-ai-task='copilot']")).toContainText("fast");
    await expect(ai.locator("[data-ai-task='classification']")).toContainText("(est.)");
    await users.locator("[data-user='12']").getByRole("link", { name: "Status" }).click();
    await expect(page).toHaveURL(/section=support&user=12$/);
    const person = page.locator("[data-support-user='12']");
    await expect(person).toContainText("Chrome on Android");
    await expect(person).toContainText("198.51.100.x");
    await expect(person).toContainText("expired");
    await expect(page.locator("[data-feedback='5']")).toContainText("tr****@example.com");
  });

  test("Support: an unknown person number says so", async ({ page }) => {
    await openAdmin(page, "/admin?section=support");
    await page.getByLabel("Person number (from Users & business)").fill("404");
    await page.getByRole("button", { name: "Show status" }).click();
    await expect(page).toHaveURL(/user=404$/);
    await expect(page.locator("[data-console-card='support-user']")).toContainText("nobody with that number");
  });

  test("Testing tools: every existing tool's state, read-only", async ({ page }) => {
    await openAdmin(page, "/admin?section=testing");
    const tools = page.locator("[data-tool]");
    await expect(tools).toHaveCount(7);
    await expect(page.locator("[data-tool='screener-audit']")).toContainText("0 failing");
    await expect(page.locator("[data-tool='dead-letters']")).toContainText("http_4xx");
    await expect(page.locator("[data-console-card='testing-tools'] button")).toHaveCount(0);
  });
});
