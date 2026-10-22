import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockCharts } from "./chartsMocks";
import { F, mockDashboard } from "./dashboardMocks";
import { mockApi, type MockOptions } from "./mockApi";
import { T, mockTrading } from "./tradingMocks";
import { PAGE_TOURS } from "../src/tours/content";
import type { TourId } from "../src/tours/pages";
import { stepsFor, targetSelector, type TourLayout } from "../src/tours/types";

/**
 * MO2 part C: first-time guidance in the v2 shell, on the existing mocks (made-up data only).
 * - THE CI CHECK: for every page tour, on the desktop (1440) and on the phone (390), the page is opened as a first visit,
 *   every step's `data-tour` element must be visible, and the tour must play every step on it (Next ... Done) and be
 *   marked seen on the server. A renamed or removed element fails here.
 * - The welcome on the first sign-in, ending on the Broker page; "?" replays; Esc closes; Skip all tips; Help → Show all
 *   tips again; nothing opens while the server's marks are unknown.
 * - Baselines (new UI only): `tour-welcome-v2-*` and `tour-step-v2-*` at 1440 and 390, light and dark. MO2 part B's
 *   key guide has its own spec (e2e/key-guides-v2.e2e.ts).
 * After an intended change: `npx playwright test --project=phone e2e/tours-v2.e2e.ts --update-snapshots`, check, commit.
 */

const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const OC = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown>;
};
const WHEN = "2026-10-19T05:00:00Z";
const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };

type Marks = { welcome_done_at: string | null; skip_all_at: string | null; seen: Record<string, string> };
const FIRST_VISIT: Marks = { welcome_done_at: WHEN, skip_all_at: null, seen: {} };
const NEW_USER: Marks = { welcome_done_at: null, skip_all_at: null, seen: {} };

interface Server { marks: Marks; version: number; puts: Marks[] }

/** `GET /api/me/prefs` with the tours namespace and `PUT /api/me/prefs/tours` as a compare-and-set, recorded. */
async function mockTours(page: Page, marks: Marks): Promise<Server> {
  const server: Server = { marks: structuredClone(marks), version: 1, puts: [] };
  await page.route(/\/api(?:\/v1)?\/me\/prefs$/, (r) => r.fulfill({ json: {
    prefs: { chart: {}, setup: {}, oc_analyses: { items: {} }, appearance: { text_size: "normal" }, tours: server.marks },
    versions: { chart: 0, setup: 0, oc_analyses: 0, appearance: 0, tours: server.version } } }));
  await page.route(/\/api(?:\/v1)?\/me\/prefs\/tours(\?.*)?$/, (r: Route) => {
    const body = r.request().postDataJSON() as Marks;
    server.puts.push(body);
    server.marks = body;
    server.version += 1;
    return r.fulfill({ json: { namespace: "tours", value: body, version: server.version } });
  });
  return server;
}

/** How each tour page is opened on the specs' own mocks. */
const PAGES: Record<TourId, { path: string; mocks: (page: Page) => Promise<void> }> = {
  dashboard: { path: "/", mocks: async (page) => {
    await mockApi(page, { features: F.features as Record<string, boolean>, brokers: F.token_status as unknown[] });
    await mockDashboard(page, { state: "connected", me: true });
  } },
  broker: { path: "/broker", mocks: async (page) => { await mockApi(page, { features: { ui_v2: true }, brokers: CONNECTED }); } },
  charts: { path: "/charts", mocks: async (page) => {
    await mockApi(page, { features: { ui_v2: true, chart_workspace: true }, brokers: CONNECTED });
    await mockCharts(page);
  } },
  "option-chain": { path: "/option-chain", mocks: async (page) => {
    await mockApi(page, { features: { ui_v2: true }, brokers: CONNECTED });
    await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, (route) => {
      const url = new URL(route.request().url());
      const m = /\/option-chain\/([^/]+)\/(expiries|live)/.exec(url.pathname)!;
      const symbol = decodeURIComponent(m[1]);
      return route.fulfill({ json: m[2] === "expiries" ? { ...OC.expiries, underlying: symbol }
        : { ...OC.live, underlying: symbol, expiry: url.searchParams.get("expiry") ?? OC.live.expiry, market_open: true } });
    });
  } },
  signals: { path: "/signals", mocks: async (page) => {
    await mockApi(page, { features: { ui_v2: true, ai_copilot: true }, brokers: CONNECTED });
    await mockCharts(page);
  } },
  positions: { path: "/positions", mocks: async (page) => { await tradingPage(page); } },
  portfolio: { path: "/portfolio", mocks: async (page) => { await tradingPage(page); } },
  risk: { path: "/risk-management", mocks: async (page) => { await tradingPage(page); } },
  settings: { path: "/settings", mocks: async (page) => { await mockApi(page, { features: { ui_v2: true }, brokers: CONNECTED }); } },
};

async function tradingPage(page: Page) {
  await mockApi(page, { features: T.features as Record<string, boolean>, brokers: T.token_status as unknown[] } as MockOptions);
  await mockTrading(page);
}

async function open(page: Page, id: TourId, marks: Marks, theme: "light" | "dark" = "light", path = PAGES[id].path): Promise<Server> {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_push_dismissed", "1");
    } catch { /* storage unavailable */ }
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get: () => "visible" });
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get: () => false });
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await PAGES[id].mocks(page);
  const server = await mockTours(page, marks);
  await page.goto(path);
  await page.addStyleTag({ content: [
    "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
    "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  ].join("\n") });
  await expect(page.locator("main")).toBeVisible();
  return server;
}

const card = (page: Page) => page.locator("[data-tour-card]");
const next = (page: Page) => card(page).locator("[data-tour-primary]");

for (const [layout, viewport] of [["desktop", DESKTOP], ["phone", PHONE]] as [TourLayout, typeof DESKTOP][]) {
  test.describe(`page tours on the ${layout} (${viewport.width})`, () => {
    // Each test plays every step and reloads: more than the 30 s default on a busy runner.
    test.describe.configure({ timeout: 60_000 });
    test.use(layout === "phone" ? { viewport, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport, deviceScaleFactor: 1 });

    for (const id of Object.keys(PAGES) as TourId[]) {
      test(`${id}: every step's element is on the page, and the first visit plays every step`, async ({ page }) => {
        const server = await open(page, id, FIRST_VISIT);
        const steps = stepsFor(PAGE_TOURS[id], layout);
        await expect(card(page)).toBeVisible({ timeout: 15_000 });
        for (const step of steps) {
          if (step.target) {
            await expect(page.locator(targetSelector(step.target)).first(), `${id} / ${step.id}: no visible ${targetSelector(step.target)} on the ${layout}`).toBeVisible();
          }
        }
        for (const [i, step] of steps.entries()) {
          await expect(card(page).locator(`[data-tour-step="${step.id}"]`)).toBeVisible();
          await expect(card(page)).toContainText(`Tip ${i + 1} of ${steps.length}`);
          await expect(card(page)).toContainText(step.title);
          if (step.target) await expect(page.locator(`[data-tour-spotlight="${step.target}"]`)).toBeAttached();
          // The card stays on the screen.
          const box = await card(page).boundingBox();
          expect(box && box.x >= 0 && box.x + box.width <= viewport.width + 1, `${id} / ${step.id}: the card leaves the screen`).toBe(true);
          await next(page).click();
        }
        await expect(card(page)).toHaveCount(0);
        await expect.poll(() => server.marks.seen[id] ?? null).not.toBeNull();
        // Seen: the next visit opens nothing by itself.
        await page.reload();
        await expect(page.locator("main")).toBeVisible();
        await page.waitForTimeout(1500);
        await expect(card(page)).toHaveCount(0);
      });
    }
  });
}

test.describe("first-time guidance", () => {
  test.describe.configure({ timeout: 60_000 });
  test.use({ viewport: DESKTOP, deviceScaleFactor: 1 });

  test("the welcome on the first sign-in: four cards, Next, and it ends on the Broker page", async ({ page }) => {
    const server = await open(page, "dashboard", NEW_USER);
    await expect(card(page)).toHaveAttribute("data-tour-card", "welcome-what", { timeout: 15_000 });
    for (const title of ["Welcome to AiAlgoDesk", "Connect your broker", "Your paper bot", "Get alerts on your phone"]) {
      await expect(card(page)).toContainText(title);
      if (title !== "Get alerts on your phone") await next(page).click();
    }
    await card(page).getByRole("button", { name: "Connect your broker" }).click();
    await expect(page).toHaveURL(/\/broker$/);
    await expect.poll(() => server.marks.welcome_done_at).not.toBeNull();
    // On the Broker page its own tips follow (its first visit).
    await expect(card(page)).toHaveAttribute("data-tour-card", /^broker-/, { timeout: 10_000 });
  });

  test("Back goes to the previous tip; Esc closes and marks the page seen; the page still works under the tips", async ({ page }) => {
    const server = await open(page, "portfolio", FIRST_VISIT);
    await expect(card(page)).toBeVisible({ timeout: 15_000 });
    await next(page).click();
    await expect(card(page)).toContainText("Tip 2 of");
    await card(page).getByRole("button", { name: "Back" }).click();
    await expect(card(page)).toContainText("Tip 1 of");
    // No modal and no focus trap: the page's own link is still reachable while the tips show.
    await expect(page.locator("[aria-modal='true']")).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(card(page)).toHaveCount(0);
    await expect.poll(() => server.marks.seen.portfolio ?? null).not.toBeNull();
  });

  test("a tap outside the tips closes them and still reaches the page", async ({ page }) => {
    await open(page, "risk", FIRST_VISIT);
    await expect(card(page)).toBeVisible({ timeout: 15_000 });
    await page.getByRole("link", { name: "Notifications" }).click();
    await expect(card(page)).toHaveCount(0);
    await expect(page).toHaveURL(/\/notifications$/);
  });

  test("the ? button replays a page's tips after they were seen", async ({ page }) => {
    await open(page, "positions", { ...FIRST_VISIT, seen: { positions: WHEN } });
    await page.waitForTimeout(1500);
    await expect(card(page)).toHaveCount(0);
    await page.getByRole("button", { name: "Show the tips for this page" }).click();
    await expect(card(page)).toContainText("Tip 1 of 3");
  });

  test("Skip all tips: nothing opens by itself, the What is this page? cards hide, and ? still works", async ({ page }) => {
    await open(page, "risk", { ...FIRST_VISIT, skip_all_at: WHEN });
    await page.waitForTimeout(1500);
    await expect(card(page)).toHaveCount(0);
    await expect(page.locator("[data-page-intro]")).toHaveCount(0);
    await page.getByRole("button", { name: "Show the tips for this page" }).click();
    await expect(card(page)).toBeVisible();
  });

  test("Skip all tips from the welcome is saved on the server", async ({ page }) => {
    const server = await open(page, "dashboard", NEW_USER);
    await expect(card(page)).toBeVisible({ timeout: 10_000 });
    await card(page).getByRole("button", { name: "Skip all tips" }).click();
    await expect(card(page)).toHaveCount(0);
    await expect.poll(() => server.marks.skip_all_at).not.toBeNull();
  });

  test("Help → Show all tips again clears the marks and the welcome opens on the Dashboard", async ({ page }) => {
    const server = await open(page, "settings", { welcome_done_at: WHEN, skip_all_at: WHEN, seen: { dashboard: WHEN, settings: WHEN } });
    const help = page.locator("[data-tour='settings-help']");
    await help.scrollIntoViewIfNeeded();
    await expect(help.getByText("Tips are off.")).toBeVisible();
    await help.getByRole("button", { name: "Show all tips again" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect.poll(() => server.marks).toEqual({ welcome_done_at: null, skip_all_at: null, seen: {} });
    await expect(card(page)).toHaveAttribute("data-tour-card", "welcome-what", { timeout: 15_000 });
  });

  test("Help → Skip all tips", async ({ page }) => {
    const server = await open(page, "settings", { ...FIRST_VISIT, seen: { settings: WHEN } });
    const help = page.locator("[data-tour='settings-help']");
    await help.scrollIntoViewIfNeeded();
    await help.getByRole("button", { name: "Skip all tips" }).click();
    await expect.poll(() => server.marks.skip_all_at).not.toBeNull();
    await expect(help.getByText("Tips are off.")).toBeVisible();
  });

  test("while the server's marks are unknown (an older server) nothing opens and there is no ? button", async ({ page }) => {
    await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
    await PAGES.risk.mocks(page);                                         // GET /me/prefs answers 404 (mockApi)
    await page.goto("/risk-management");
    await expect(page.locator("main")).toBeVisible();
    await page.waitForTimeout(1500);
    await expect(card(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Show the tips for this page" })).toHaveCount(0);
  });
});

// Baselines of the new UI only: the welcome card and a page tip with its spotlight.
for (const viewport of [DESKTOP, PHONE]) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`tour cards ${viewport.width} ${theme}`, () => {
      test.use(viewport === PHONE ? { viewport, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport, deviceScaleFactor: 1 });

      test("the welcome card", async ({ page }) => {
        await open(page, "dashboard", NEW_USER, theme);
        await expect(card(page)).toHaveAttribute("data-tour-card", "welcome-what", { timeout: 15_000 });
        await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
        await expect(page).toHaveScreenshot(`tour-welcome-v2-${viewport.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });

      test("a page tip with its spotlight", async ({ page }) => {
        await open(page, "risk", FIRST_VISIT, theme);
        await expect(card(page)).toHaveAttribute("data-tour-card", "risk-today", { timeout: 15_000 });
        await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
        await page.waitForTimeout(500);                                     // the scroll into view settles
        await expect(page).toHaveScreenshot(`tour-step-v2-${viewport.width}-${theme}.png`, { animations: "disabled", maxDiffPixelRatio: 0.02, timeout: 15_000 });
      });
    });
  }
}

