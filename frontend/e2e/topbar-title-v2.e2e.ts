import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { F, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";
const setup = JSON.parse(readFileSync(new URL("./fixtures/setup_v2.json", import.meta.url), "utf8")) as { strategies: unknown[] };

/**
 * H9 (UI-H) on the phone top bar with colour v2 (rule CA7): the page title wraps only between words, never inside one
 * ("Watchlis / t" was cut at 360 px), and nothing in the bar runs past the screen edge. Measured on the text itself:
 * every word of the title sits on one line. Made-up data from the shared mocks; no screenshots here.
 */

const FEATURES = { ...(F.features as Record<string, boolean>), colour_v2: true };

async function open(page: Page, path: string, features: Record<string, boolean> = FEATURES) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { state: "connected", me: true });
  await page.route(/\/api\/v1\/strategies$/, (r) => r.fulfill({ json: setup.strategies }));
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; }" });
  // Colour v2 and the v2 top bar come with the v2 shell (`ui_v2`); the v1 page check (ui_v2 off) has neither.
  if (features.ui_v2 === false) return;
  await expect(page.locator("html[data-colour='v2']")).toHaveCount(1);
  await expect(page.locator("[data-topbar-title]")).not.toHaveText("");
}

/** The title's words with the number of lines each one takes, and the bar's overflow. */
async function measure(page: Page) {
  return page.locator("[data-topbar-title]").evaluate((el) => {
    const node = el.firstChild as Text;
    const text = node.textContent ?? "";
    const words: { word: string; lines: number }[] = [];
    for (const m of text.matchAll(/\S+/g)) {
      const range = document.createRange();
      range.setStart(node, m.index!);
      range.setEnd(node, m.index! + m[0].length);
      words.push({ word: m[0], lines: new Set([...range.getClientRects()].filter((r) => r.width > 0).map((r) => Math.round(r.top))).size });
    }
    // The bar's row (the title sits in a title + (?) group inside it).
    const row = el.closest("header")!.firstElementChild as HTMLElement;
    const box = el.getBoundingClientRect();
    return { text, words, right: box.right, rowOverflow: row.scrollWidth - row.clientWidth, viewport: window.innerWidth };
  });
}

const PAGES = ["/", "/charts", "/option-chain", "/markets", "/signals", "/positions", "/analytics", "/strategies", "/news-events", "/settings", "/terminal"];

for (const size of [{ width: 360, height: 800 }, { width: 390, height: 844 }]) {
  test.describe(`H9 phone top-bar title ${size.width}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
    for (const path of PAGES) {
      test(`H9: the title on ${path} wraps only between words`, async ({ page }) => {
        await open(page, path);
        const m = await measure(page);
        for (const w of m.words) expect(w.lines, `"${w.word}" of "${m.text}" on one line`).toBe(1);
        expect(m.right).toBeLessThanOrEqual(m.viewport);
        expect(m.rowOverflow, "nothing in the bar runs past the edge").toBeLessThanOrEqual(0);
      });
    }
  });
}

/**
 * The number of lines an element's text takes: its line boxes grouped by their top. A box within half a line of the
 * previous line's top is on the same line (a filter chip's "All" and its count "4" are separate boxes a pixel or two
 * apart); a wrapped word starts a full line lower.
 */
async function lines(page: Page, selector: string) {
  return page.locator(selector).evaluateAll((els) => els.map((el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const rects = [...range.getClientRects()].filter((r) => r.width > 0).sort((a, b) => a.top - b.top);
    const tops: number[] = [];
    for (const r of rects) if (!tops.length || r.top - tops[tops.length - 1] > r.height / 2) tops.push(r.top);
    return { text: el.textContent ?? "", lines: tops.length, overflow: el.scrollWidth - el.clientWidth };
  }));
}

// REV-S5: on a 375 px phone the page title stays whole on one line ("Paper / bot", "Find / setups" wrapped), and the
// Library's category chips do not wrap ("MULTI- / TIMEFRAME"), on the v2 card page and on the v1 page.
test.describe("REV-S5 phone title and category chip 375", () => {
  test.use({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  for (const path of ["/deployments", "/screener", "/strategies"]) {
    test(`REV-S5: the title on ${path} is on one line`, async ({ page }) => {
      await open(page, path);
      const [title] = await lines(page, "[data-topbar-title]");
      expect(title.lines, `"${title.text}" on one line`).toBe(1);
      const m = await measure(page);
      expect(m.rowOverflow, "nothing in the bar runs past the edge").toBeLessThanOrEqual(0);
    });
  }
  // M49 U3: with `ui_v2` on the Library is the v2 card page - its category / "You use it" chips and the category filters
  // stay whole on one line; with `ui_v2` off the v1 page's category chip does too.
  test("REV-S5: the Library's category chips do not wrap", async ({ page }) => {
    await open(page, "/strategies");
    await expect(page.locator("[data-page='strategy-library-v2']")).toBeVisible();
    await expect(page.locator("[data-library-card] [data-chip]").first()).toBeVisible();
    const chips = await lines(page, "[data-library-card] [data-chip='category'], [data-library-card] [data-chip='in-use']");
    expect(chips.map((c) => c.text)).toContain("Multi-timeframe");
    const filters = await lines(page, "[data-page='strategy-library-v2'] button[aria-pressed]:not([data-library-pick])");
    expect(filters.map((c) => c.text.replace(/\s*\d+$/, ""))).toEqual(expect.arrayContaining(["All", "Indicators", "Multi-timeframe"]));
    for (const c of [...chips, ...filters]) {
      expect(c.lines, `"${c.text}" on one line`).toBe(1);
      expect(c.overflow, `"${c.text}" fits its chip`).toBeLessThanOrEqual(0);
    }
  });
  test("REV-S5: the v1 Library's category chips do not wrap (ui_v2 off)", async ({ page }) => {
    await open(page, "/strategies", { ...FEATURES, ui_v2: false });
    await expect(page.locator("[data-page='strategy-library-v2']")).toHaveCount(0);
    await expect(page.locator("[data-chip='strategy-category']").first()).toBeVisible();
    const chips = await lines(page, "[data-chip='strategy-category']");
    expect(chips.map((c) => c.text)).toContain("Multi-timeframe");
    for (const c of chips) {
      expect(c.lines, `"${c.text}" on one line`).toBe(1);
      expect(c.overflow, `"${c.text}" fits its chip`).toBeLessThanOrEqual(0);
    }
  });
});
