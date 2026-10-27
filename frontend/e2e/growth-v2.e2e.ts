import { expect, test, type Page, type Route } from "@playwright/test";
import { DEMO_ACTIVATION, DEMO_CARD, DEMO_PLAN } from "../src/growth/growth.fixture";
import { FEATURES as ADMIN_FEATURES, openAdmin } from "./adminMocks";
import { mockApi } from "./mockApi";
import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
import { FEATURES as SETTINGS_FEATURES, UPSTOX, openSettings } from "./settingsMocks";

/**
 * MO4 slice 3 (`ui_v2` + `growth_v2`), on made-up answers (src/growth/growth.fixture.ts):
 * - Settings → Account & plan: the "Your plan" card with the usage meter above the existing plans and billing;
 * - the Journal page: "Share my week", the discipline card (no stock names, no prices), saved as a PNG on his tap only;
 * - the admin console's Users & business: Activation (read-only).
 * Each baseline is the new block only, so every older page baseline stays as it is (those are the "before" pictures):
 * `growth-plan-v2-<width>-<theme>.png`, `growth-share-v2-<width>-<theme>.png` (360, 390, 1440) and
 * `growth-activation-v2-<width>-<theme>.png` (390, 1440). With `growth_v2` off none of it shows and nothing is asked for.
 * After an intended change: `npx playwright test --project=phone e2e/growth-v2.e2e.ts --update-snapshots`.
 */

const THEMES = ["light", "dark"] as const;
type Theme = (typeof THEMES)[number];
const SIZES = [{ width: 360, height: 800 }, { width: 390, height: 844 }, { width: 1440, height: 900 }];
const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };
const json = (body: unknown) => (r: Route) => r.fulfill({ json: body });
// The phone bar is fixed to the bottom of the screen: hidden for the block shots, so it never sits over the block.
const hideBar = (page: Page) => page.addStyleTag({ content: ".bottom-nav { display: none !important; }" });

async function growthRoutes(page: Page, asked: string[]) {
  await page.route(/\/api(?:\/v1)?\/growth\/plan$/, (r) => { asked.push("plan"); return r.fulfill({ json: DEMO_PLAN }); });
  await page.route(/\/api(?:\/v1)?\/growth\/share\/discipline-week$/, (r) => { asked.push("card"); return r.fulfill({ json: DEMO_CARD }); });
}

async function openJournal(page: Page, theme: Theme, features: Record<string, boolean>) {
  await page.clock.install({ time: new Date("2032-03-17T18:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_push_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["journal"]));
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  const calls = await mockApi(page, { features, brokers: [], connections: [UPSTOX] });
  const asked: string[] = [];
  await page.route(/\/api(?:\/v1)?\/journal\/week(\?.*)?$/, (r) => r.fulfill({ status: 503, json: { detail: "off" } }));
  await growthRoutes(page, asked);
  await page.goto("/analytics/journal");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  return { calls, asked };
}

async function noSidewaysScroll(page: Page) {
  const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
  expect(r.scroll, "page wider than the screen").toBeLessThanOrEqual(r.width);
}

const JOURNAL_ON = { ui_v2: true, growth_v2: true, discipline_engine: true, phase0_paper_only: true };

for (const size of SIZES) {
  const phone = size.width < 1000;
  test.describe(`growth v2 ${size.width}`, () => {
    test.use({ viewport: size, isMobile: phone, hasTouch: phone, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`the plan card looks the same (${theme})`, async ({ page }) => {
        const asked: string[] = [];
        await openSettings(page, "/settings/account", { theme, features: { ...SETTINGS_FEATURES, growth_v2: true }, routes: (p) => growthRoutes(p, asked) });
        const card = page.locator("[data-block='plan-usage']");
        await expect(card).toBeVisible();
        await expect(card.locator("[aria-busy='true']")).toHaveCount(0);
        await hideBar(page);
        await noSidewaysScroll(page);
        await expect(card).toHaveScreenshot(`growth-plan-v2-${size.width}-${theme}.png`, SHOT);
      });

      test(`the share card looks the same (${theme})`, async ({ page }) => {
        await openJournal(page, theme, JOURNAL_ON);
        const card = page.locator("[data-block='discipline-card']");
        await expect(card.locator("[data-share-card] svg")).toBeVisible();
        await noSidewaysScroll(page);
        await hideBar(page);
        await expect(card).toHaveScreenshot(`growth-share-v2-${size.width}-${theme}.png`, SHOT);
      });
    }
  });
}

test.describe("growth v2 behaviour", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("the plan card: the meter, what the plan includes, no price in the card, above the existing billing", async ({ page }) => {
    const asked: string[] = [];
    await openSettings(page, "/settings/account", { features: { ...SETTINGS_FEATURES, growth_v2: true }, routes: (p) => growthRoutes(p, asked) });
    const card = page.locator("[data-block='plan-usage']");
    await expect(card.locator("[data-meter='paper_bots']")).toHaveAttribute("data-state", "full");
    await expect(card).toContainText("You have used all of this on your plan.");
    await expect(card).toContainText("Your Pro trial: 4 days left.");
    await expect(card).not.toContainText("₹");
    const below = await page.getByText("Plan & billing", { exact: true }).first().boundingBox();
    const top = await card.boundingBox();
    expect(below!.y).toBeGreaterThan(top!.y);
    expect(asked).toEqual(["plan"]);
  });

  test("the share card: no stock name, no price; the image is saved only on his tap and nothing is sent", async ({ page }) => {
    const { calls, asked } = await openJournal(page, "light", JOURNAL_ON);
    const card = page.locator("[data-share-card]");
    await expect(card).toContainText("My discipline this week");
    await expect(card.locator("[data-card-fact='inside_limits']")).toContainText("2 of 3");
    await expect(card).toContainText("A day without a trade is a good day when nothing met my plan.");
    const shown = `${await card.innerText()} ${await card.locator("svg").textContent()}`;
    expect(shown).not.toMatch(/₹|\d+[.,]\d+|\d{4,}|\b(?:buy|sell|target|stop|profit|loss)\b/i);
    expect(asked).toEqual(["card"]);
    const [download] = await Promise.all([page.waitForEvent("download"), page.locator("[data-download]").click()]);
    expect(download.suggestedFilename()).toBe("my-discipline-week.png");
    expect(calls.posts.filter((p) => !p.url.includes("/auth/refresh"))).toEqual([]);      // the card sends nothing
  });

  test("growth_v2 off: no plan card, nothing asked for", async ({ page }) => {
    const asked: string[] = [];
    await openSettings(page, "/settings/account", { routes: (p) => growthRoutes(p, asked) });
    await expect(page.locator("#plan")).toBeVisible();
    await expect(page.locator("[data-block='plan-usage']")).toHaveCount(0);
    expect(asked).toEqual([]);
  });

  test("growth_v2 off: no share card on the Journal, nothing asked for", async ({ page }) => {
    const { asked } = await openJournal(page, "light", { ...JOURNAL_ON, growth_v2: false });
    await expect(page.locator("main")).toContainText("Journal & discipline");
    await expect(page.locator("[data-block='discipline-card']")).toHaveCount(0);
    expect(asked).toEqual([]);
  });
});

for (const size of [{ name: "1440", width: 1440, height: 900, mobile: false }, { name: "390", width: 390, height: 844, mobile: true }]) {
  test.describe(`growth v2 admin activation ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`Activation looks the same (${theme})`, async ({ page }) => {
        await openAdmin(page, "/admin?section=users", { theme, features: { ...ADMIN_FEATURES, growth_v2: true },
          routes: (p) => p.route(/\/api(?:\/v1)?\/admin\/console\/activation(\?.*)?$/, json(DEMO_ACTIVATION)) });
        const panel = page.locator("[data-console-card='activation']");
        await expect(panel.locator("[data-activation-user='41']")).toBeVisible();
        await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
        await noSidewaysScroll(page);
        // The panel sits below the long People table: clip the full page to it (an element shot of a block below the
        // fold is taken from the wrong place on a phone viewport).
        await hideBar(page);
        const clip = await panel.evaluate((el) => { const r = el.getBoundingClientRect();
          return { x: r.left + window.scrollX, y: r.top + window.scrollY, width: r.width, height: r.height }; });
        await expect(page).toHaveScreenshot(`growth-activation-v2-${size.name}-${theme}.png`, { ...SHOT, fullPage: true, clip });
      });
    }
  });
}

test.describe("growth v2 admin activation behaviour", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("read-only: one GET per period, and nothing with growth_v2 off", async ({ page }) => {
    const reads: string[] = [];
    await openAdmin(page, "/admin?section=users", { features: { ...ADMIN_FEATURES, growth_v2: true },
      routes: (p) => p.route(/\/api(?:\/v1)?\/admin\/console\/activation(\?.*)?$/, (r) => {
        reads.push(`${r.request().method()} ${new URL(r.request().url()).search}`);
        return r.fulfill({ json: DEMO_ACTIVATION });
      }) });
    const panel = page.locator("[data-console-card='activation']");
    await expect(panel).toContainText("All three (activated)");
    await expect(panel).toContainText("20% of sign-ups");
    await panel.getByRole("button", { name: "30 days" }).click();
    await expect.poll(() => reads.length).toBe(2);
    expect(reads).toEqual(["GET ?days=90", "GET ?days=30"]);
  });

  test("growth_v2 off: no Activation panel, no read", async ({ page }) => {
    const reads: string[] = [];
    await openAdmin(page, "/admin?section=users", {
      routes: (p) => p.route(/\/api(?:\/v1)?\/admin\/console\/activation(\?.*)?$/, (r) => { reads.push("activation"); return r.fulfill({ json: DEMO_ACTIVATION }); }) });
    await expect(page.locator("[data-console-card='users']")).toBeVisible();
    await expect(page.locator("[data-console-card='activation']")).toHaveCount(0);
    expect(reads).toEqual([]);
  });
});

/** UX-11 (owner, 27 Oct): numbers first - every <Page> card passes the text budget; the visible-word count is printed. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`growth text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every card fits the text budget", async ({ page }) => {
      await openJournal(page, "light", JOURNAL_ON);
      console.log(`[words] growth ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      expect(await textBudgetProblems(page)).toEqual([]);
    });
  });
}
