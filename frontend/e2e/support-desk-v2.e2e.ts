import { expect, test, type Page, type Route } from "@playwright/test";
import { choose } from "./combobox";
import { openAdmin, type Theme } from "./adminMocks";
import { FEATURES, openSettings } from "./settingsMocks";

/**
 * Production readiness C12: the Help centre and "Report a problem" (Settings › Help & tips, flag `help_centre`) and
 * Admin › Support's tickets and desk settings. Every reply is made up. Baselines (NEW):
 * `help-centre-v2-{360,390,1440}-{light,dark}.png`, `report-problem-v2-...` and `admin-tickets-v2-...`. After an intended
 * change: `npx playwright test --project=phone e2e/support-desk-v2.e2e.ts --update-snapshots`.
 * Fonts: openSettings / the settle() below pin DejaVu Sans (CI has no Inter).
 */

const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };
const ON = { ...FEATURES, help_centre: true };
const HELP = { articles: [
  { id: "help:broker", title: "How do I connect my broker?", text: "Open the Broker page and use \"Connect your broker\": pick your broker, store the API credentials it issued and log in.", page: "/broker", keywords: "broker, connect, login" },
  { id: "help:paper", title: "What is paper trading, and does the platform place real orders?", text: "During this phase every order and trade in the platform is paper (simulated): no real money moves.", page: "/positions", keywords: "paper, simulated, real money" },
  { id: "help:2fa", title: "How do I turn on two-factor authentication?", text: "Open the Account page and find \"Two-factor authentication\".", page: "/account", keywords: "2fa, security" },
] };
const OPEN = { configured: true, market_open: true, next_open: null, whatsapp_url: "https://wa.me/910000000000", email: "help@example.com" };
const CLOSED = { configured: true, market_open: false, next_open: "2026-10-23T03:45:00+00:00", whatsapp_url: null, email: null };
const LAST_ERROR = { id: "3f9c2a7e1b", page: "/option-chain", at: "2026-10-22T05:28:00.000Z" };

async function desk(page: Page, contact: unknown, reports: unknown[]) {
  const R = (p: string) => new RegExp(`/api(?:/v1)?${p}`);
  await page.route(R("/support/help$"), (r: Route) => r.fulfill({ json: HELP }));
  await page.route(R("/support/contact$"), (r: Route) => r.fulfill({ json: contact }));
  await page.route(R("/support/report$"), (r: Route) => {
    reports.push(r.request().postDataJSON());
    return r.fulfill({ status: 201, json: { id: 41, created_at: "2026-10-22T05:30:00+00:00", status: "open", attached: {} } });
  });
}

async function settle(page: Page) {
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
}

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
  test.describe(`C12 ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    for (const theme of ["light", "dark"] as Theme[]) {
      test(`the Help centre searches the help, the tips and the words (${theme})`, async ({ page }) => {
        const reports: unknown[] = [];
        await openSettings(page, "/settings/help", { theme, features: ON, routes: (p) => desk(p, OPEN, reports) });
        const centre = page.locator("[data-help-centre]");
        await expect(centre).toBeVisible();
        await expect(centre.locator("[data-support-contact='open']")).toContainText("WhatsApp");
        await centre.getByLabel("Search the help").fill("broker");
        await expect(centre.locator("[data-help-article]").first()).toHaveAttribute("data-help-article", "help:broker");
        await centre.locator("[data-help-article='help:broker'] summary").click();
        await expect(centre.getByRole("link", { name: "Open the page" })).toHaveAttribute("href", "/broker");
        await settle(page);
        await noSidewaysScroll(page);
        await expect(centre).toHaveScreenshot(`help-centre-v2-${size.name}-${theme}.png`, SHOT);
      });

      test(`Report a problem lists what is attached and sends only that (${theme})`, async ({ page }) => {
        const reports: Record<string, unknown>[] = [];
        await page.addInitScript((e) => { try { sessionStorage.setItem("atp_last_server_error", JSON.stringify(e)); } catch { /* none */ } }, LAST_ERROR);
        await openSettings(page, "/settings/help", { theme, features: ON, routes: (p) => desk(p, OPEN, reports) });
        await page.locator("[data-report-open]").click();
        const dialog = page.getByRole("dialog");
        await expect(dialog.locator("[data-report-attached]")).toContainText("/settings/help");
        await expect(dialog.locator("[data-report-error]")).toContainText("3f9c2a7e1b");
        await dialog.getByLabel("What went wrong?").fill("The option chain showed an error.");
        await settle(page);
        await noSidewaysScroll(page);
        await expect(dialog).toHaveScreenshot(`report-problem-v2-${size.name}-${theme}.png`, SHOT);
        await dialog.getByRole("button", { name: "Send report" }).click();
        await expect(dialog.locator("[data-report-sent='41']")).toContainText("#41");
        expect(reports).toHaveLength(1);
        expect(Object.keys(reports[0]).sort()).toEqual(["app_version", "client_time", "error_at", "error_id", "error_screen", "message", "screen"]);
        expect(reports[0]).toMatchObject({ message: "The option chain showed an error.", screen: "/settings/help", error_id: "3f9c2a7e1b", error_screen: "/option-chain" });
      });

      test(`Admin › Support: tickets with status, assignee and targets (${theme})`, async ({ page }) => {
        const puts: unknown[] = [];
        await openAdmin(page, "/admin?section=support", { theme, routes: async (p) => {
          await p.route(/\/api(?:\/v1)?\/admin\/support\/tickets\/5$/, (r) => {
            puts.push(r.request().postDataJSON());
            return r.fulfill({ json: { id: 5, status: "answered", assignee_id: null, first_response_at: "2026-10-22T05:31:00+00:00", closed_at: null, kind: "problem", context: {},
              first_response_due_at: null, first_response_late: null, resolve_due_at: null, resolve_late: null } });
          });
        } });
        const card = page.locator("[data-console-card='feedback']");
        await expect(card.locator("[data-ticket='5']")).toContainText("3f9c2a7e1b");
        await expect(card.locator("[data-ticket='5']")).toContainText("tr****@example.com");
        await expect(card).toContainText("first answer 4 h, resolve 48 h");
        await expect(page.locator("[data-console-card='support-desk']")).toContainText("No contact set");
        await settle(page);
        await noSidewaysScroll(page);
        await expect(card).toHaveScreenshot(`admin-tickets-v2-${size.name}-${theme}.png`, SHOT);
        await choose(page, card.getByLabel("Status of #5"), "answered");
        await expect(card.locator("[data-ticket='5']")).toHaveAttribute("data-ticket-status", "answered");
        expect(puts).toEqual([{ status: "answered" }]);
      });
    }
  });
}

test.describe("C12 outside market hours", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("the contact line says when it opens; no link", async ({ page }) => {
    await openSettings(page, "/settings/help", { features: ON, routes: (p) => desk(p, CLOSED, []) });
    const line = page.locator("[data-support-contact='closed']");
    await expect(line).toContainText("while the market is open");
    await expect(line).toContainText("09:15");
    await expect(page.locator("a[href^='https://wa.me']")).toHaveCount(0);
  });

  test("with help_centre off the Help & tips page is as before", async ({ page }) => {
    await openSettings(page, "/settings/help", { features: FEATURES });
    await expect(page.locator("[data-tips-row]")).toBeVisible();
    await expect(page.locator("[data-help-centre]")).toHaveCount(0);
  });
});

import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
/** UX-11 (owner, 27 Oct): numbers first - every card passes the text budget; the visible-word count is printed. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`support-desk text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every card fits the text budget", async ({ page }) => {
      await openSettings(page, "/settings/help", { theme: "light", features: ON, routes: (p) => desk(p, OPEN, []) });
      await settle(page);
      console.log(`[words] support-desk ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      expect(await textBudgetProblems(page)).toEqual([]);
    });
  });
}
