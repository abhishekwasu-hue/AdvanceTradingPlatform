import { expect, test, type Page } from "@playwright/test";
import { F, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";

/**
 * H9 (UI-H) on the phone top bar with colour v2 (rule CA7): the page title wraps only between words, never inside one
 * ("Watchlis / t" was cut at 360 px), and nothing in the bar runs past the screen edge. Measured on the text itself:
 * every word of the title sits on one line. Made-up data from the shared mocks; no screenshots here.
 */

const FEATURES = { ...(F.features as Record<string, boolean>), colour_v2: true };

async function open(page: Page, path: string) {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "light" }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: FEATURES, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { state: "connected", me: true });
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; }" });
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
    const row = el.parentElement!;
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
