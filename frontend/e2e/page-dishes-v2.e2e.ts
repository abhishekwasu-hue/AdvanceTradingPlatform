import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import { DEMO_DISHES } from "../src/growth/dish.fixture";
import { DEMO_CARD } from "../src/growth/growth.fixture";
import type { DishPage } from "../src/growth/types";
import { mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { T, mockTrading } from "./tradingMocks";
import { readCards } from "./textBudget";
import { cardTextProblems } from "../src/ui/textBudget";

/**
 * MO4 slice 4 (`ui_v2` + `growth_v2`): the "ready-made dish" at the top of Signals, My positions, the Journal, Charts and
 * Find setups, on made-up answers (src/growth/dish.fixture.ts). No screenshot baselines: each page's own baselines
 * keep proving it with the flag off (the dish is behind `growth_v2`). With `DISH_SHOTS=<folder>` the size / theme loop
 * also saves the page before (flag off) and after (flag on) at 390 and 1440, light and dark, for the PR - never
 * committed.
 */

const THEMES = ["light", "dark"] as const;
type Theme = (typeof THEMES)[number];
const SIZES = [{ width: 390, height: 844 }, { width: 1440, height: 900 }];
const SHOTS = process.env.DISH_SHOTS ?? "";
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const SIGNALS = JSON.parse(readFileSync(new URL("./fixtures/signals_v2.json", import.meta.url), "utf8")) as { today: { status: string }[]; earlier: unknown[] };

const PAGES: { page: DishPage; path: string; intro: string; features: Record<string, boolean> }[] = [
  { page: "signals", path: "/signals", intro: "signals", features: { ui_v2: true, ai_copilot: true } },
  { page: "positions", path: "/positions", intro: "positions", features: { ...(T.features as Record<string, boolean>) } },
  { page: "journal", path: "/analytics/journal", intro: "journal", features: { ui_v2: true, discipline_engine: true } },
  { page: "charts", path: "/charts/DEMOALPHA", intro: "charts", features: { ui_v2: true } },
  { page: "screener", path: "/screener", intro: "screener", features: { ui_v2: true, screener_v2: true } },
];

async function pageRoutes(page: Page, which: DishPage) {
  if (which === "signals") {
    await page.route(/\/api(?:\/v1)?\/(signals\/feed|ai\/actions|strategies$)/, (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith("/signals/feed")) {
        const tab = url.searchParams.get("tab") ?? "today";
        const waiting = SIGNALS.today.filter((r) => r.status === "waiting");
        const items = tab === "waiting" ? waiting : tab === "today" ? SIGNALS.today : [...SIGNALS.today, ...SIGNALS.earlier];
        return route.fulfill({ json: { tab, source: "all", symbol: null, items, next_cursor: null, counts: { waiting: waiting.length, today: SIGNALS.today.length },
          as_of: "2026-10-08T05:00:00+00:00", since: null, sources: {} } });
      }
      return route.fulfill({ json: [] });
    });
  } else if (which === "positions") {
    await mockTrading(page);
  } else if (which === "journal") {
    await page.route(/\/api(?:\/v1)?\/journal\/week(\?.*)?$/, (r) => r.fulfill({ json: { week: "2032-W11", start: "2032-03-15T00:00:00+05:30",
      end: "2032-03-22T00:00:00+05:30", entries: [], score: { score: 78, decisions: 3, trades: 3, components: [], count_unconfirmed: true } } }));
    await page.route(/\/api(?:\/v1)?\/growth\/share\/discipline-week$/, (r) => r.fulfill({ json: DEMO_CARD }));
  } else if (which === "charts") {
    await mockCharts(page);
  } else {
    await page.route(/\/api(\/v1)?\/screener\//, (route) => {
      const url = route.request().url();
      if (/\/registry$/.test(url)) return route.fulfill({ json: { version: "screenql/1", timeframes: [], entries: [] } });
      if (/\/validate$/.test(url)) return route.fulfill({ json: { ok: true, problems: [], text: "", plan: { cost: 1, lookback: {} } } });
      return route.fulfill({ status: 503, headers: { "X-Feature-Disabled": "screener_gallery" }, json: { detail: "disabled" } });
    });
  }
}

async function open(page: Page, which: (typeof PAGES)[number], theme: Theme, dish: boolean) {
  await page.clock.install({ time: new Date("2032-03-17T11:00:00+05:30") });
  await page.addInitScript(([t, intro]) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_push_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify([intro]));
    } catch { /* storage unavailable */ }
  }, [theme, which.intro] as const);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ...which.features, phase0_paper_only: true, ...(dish ? { growth_v2: true } : {}) }, brokers: CONNECTED });
  await pageRoutes(page, which.page);
  const asked: string[] = [];
  await page.route(/\/api(?:\/v1)?\/growth\/dish\/[a-z]+(\?.*)?$/, (r) => {
    const url = new URL(r.request().url());
    const name = url.pathname.split("/").pop() as DishPage;
    asked.push(`${name}${url.search}`);
    return dish ? r.fulfill({ json: DEMO_DISHES[name] }) : r.fulfill({ status: 404, headers: { "X-Feature-Disabled": "growth_v2" }, json: { detail: "Not found" } });
  });
  await page.goto(which.path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  return asked;
}

async function noSidewaysScroll(page: Page) {
  const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
  expect(r.scroll, "page wider than the screen").toBeLessThanOrEqual(r.width);
}

for (const size of SIZES) {
  const phone = size.width < 1000;
  test.describe(`page dishes v2 ${size.width}`, () => {
    test.use({ viewport: size, isMobile: phone, hasTouch: phone, deviceScaleFactor: 1 });
    for (const which of PAGES) {
      for (const theme of THEMES) {
        test(`${which.page}: the dish on top with growth_v2, nothing without it (${theme})`, async ({ page }) => {
          const before = await open(page, which, theme, false);
          await expect(page.locator("[data-block='page-dish']")).toHaveCount(0);
          if (SHOTS) {
            mkdirSync(SHOTS, { recursive: true });
            await page.screenshot({ path: `${SHOTS}/${which.page}-${size.width}-${theme}-before.png` });
          }
          expect(before.length).toBeLessThanOrEqual(1);             // asked once, answered 404 - the page is unchanged
          const after = await open(page, which, theme, true);
          const card = page.locator(`[data-dish='${which.page}']`);
          await expect(card).toBeVisible();
          await expect(card.locator("[data-dish-headline]")).toHaveText(DEMO_DISHES[which.page].headline!);
          if (which.page === "charts") {
            expect(after[0]).toBe("charts?symbol=DEMOALPHA");
            await expect(card.locator("[data-rhythm-section]")).toHaveCount(0);      // folded: the chart comes first
          } else {
            await expect(card.locator("[data-rhythm-section]").first()).toBeVisible();
          }
          await noSidewaysScroll(page);
          // UX-11b C7: the dish card fits the text budget (its headline is its one line; prose sits in Details).
          if (which.page !== "charts") {
            const dishCards = (await readCards(page)).filter((c) => c.card === DEMO_DISHES[which.page].title);
            expect(cardTextProblems(dishCards)).toEqual([]);
          }
          if (SHOTS) await page.screenshot({ path: `${SHOTS}/${which.page}-${size.width}-${theme}-after.png` });
        });
      }
    }
  });
}

test.describe("page dishes v2 behaviour", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("Charts: unfolds on his tap, and the choice is kept on the next visit", async ({ page }) => {
    await open(page, PAGES[3], "light", true);
    const card = page.locator("[data-dish='charts']");
    await card.locator("[data-dish-toggle]").click();
    await expect(card.locator("[data-rhythm-section='yours']")).toBeVisible();
    await expect(card.locator("[data-chip='list']")).toHaveText("◆Swing ideas");     // UX-11b: his lists as chips
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.locator("[data-dish='charts'] [data-rhythm-section='story']")).toBeVisible();
  });

  test("Signals: no line with a call word and nothing is sent", async ({ page }) => {
    const posts: string[] = [];
    page.on("request", (r) => { if (r.method() !== "GET" && !r.url().includes("/auth/refresh")) posts.push(r.url()); });
    await open(page, PAGES[0], "light", true);
    const shown = await page.locator("[data-dish='signals']").innerText();
    expect(shown).not.toMatch(/\b(?:buy|sell|target|stop[- ]?loss)\b/i);
    expect(posts).toEqual([]);
  });
});
