import { expect, test, type Page } from "@playwright/test";
import { F, api, mockDashboard } from "./dashboardMocks";
import { mockApi } from "./mockApi";

/**
 * Colour v2 (flag `colour_v2`, behind `ui_v2`): the owner's review points of 24 Oct on a phone, with the flag on.
 * - CV-1: the bottom bar's inactive tabs are neutral grey; only the tab on screen is brand (and no tab at all on a page
 *   that is not in the bar, such as Option chain).
 * - CV-2: every bottom-bar label stays on one line, the bold active one included, at 360 and 390 px.
 * - CV-4: Today's P&L is fully on screen when Today opens at 390 x 844 (above the bottom bar), with the sparklines on.
 * The candles behind the sparklines are made-up 5-minute bars (no real prices). With the flag off nothing here applies;
 * the flag-off baselines stay in the page specs.
 */

const FEATURES = { ...(F.features as Record<string, boolean>), colour_v2: true };

/** Made-up 5-minute closes for any symbol asked, so the strip's sparklines draw. */
async function mockCandles(page: Page) {
  await page.route(api("/market-data/candles$"), (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}") as { symbols?: string[] };
    const symbols: Record<string, unknown> = {};
    for (const s of body.symbols ?? []) {
      const bars = Array.from({ length: 22 }, (_, i) => {
        const close = 100 + i * 0.5 + Math.sin(i * 1.3) * 2;
        return { timestamp: new Date(Date.parse("2026-10-19T03:45:00Z") + i * 300_000).toISOString(), open: close, high: close, low: close, close, volume: 0 };
      });
      symbols[s.toUpperCase()] = { bars, count: bars.length, first: bars[0].timestamp, last: bars[bars.length - 1].timestamp, error: null };
    }
    return route.fulfill({ json: { source: { broker: "upstox", account_label: "primary" }, exchange: "NSE", timeframe: "5min", base_interval: "5min",
      lookback_days: 5, fetched_at: "2026-10-19T05:30:00Z", symbols, warnings: [], note: "" } });
  });
}

async function open(page: Page, path: string, theme: "dark" | "light" = "light") {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: FEATURES, brokers: F.token_status as unknown[] });
  await mockDashboard(page, { state: "connected", me: true });
  await mockCandles(page);
  await page.goto(path);
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("html[data-colour='v2']")).toHaveCount(1);
}

/** Each bottom-bar label: its text, the number of lines it takes, its weight, and whether it fits its tab. */
async function labels(page: Page) {
  return page.locator("[data-bottom-nav-v2] [data-nav-label]").evaluateAll((els) => els.map((el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const tops = new Set([...range.getClientRects()].map((r) => Math.round(r.top)));
    const tab = el.closest("a, button")!.getBoundingClientRect();
    const box = el.getBoundingClientRect();
    return { text: el.textContent ?? "", lines: tops.size, weight: Number(getComputedStyle(el).fontWeight),
      fits: box.left >= tab.left - 0.5 && box.right <= tab.right + 0.5, right: box.right };
  }));
}

const BAR: [string, string][] = [["Today", "/"], ["Charts", "/charts"], ["Signals", "/signals"], ["My trades", "/positions"]];

for (const size of [{ width: 360, height: 800 }, { width: 390, height: 844 }]) {
  test.describe(`colour v2 bottom bar ${size.width}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const [active, path] of BAR) {
      test(`CV-2: every label on one line with "${active}" active`, async ({ page }) => {
        await open(page, path);
        const bar = page.locator("[data-bottom-nav-v2]");
        await expect(bar.locator("a[aria-current='page']")).toHaveText(active);
        const found = await labels(page);
        expect(found.map((l) => l.text)).toEqual(["Today", "Charts", "Signals", "My trades", "More"]);
        for (const l of found) {
          expect(l.lines, `${l.text} on one line`).toBe(1);
          expect(l.fits, `${l.text} inside its tab`).toBe(true);
          expect(l.right).toBeLessThanOrEqual(size.width);
        }
        expect(found.find((l) => l.text === active)!.weight).toBeGreaterThanOrEqual(600);
      });
    }
  });
}

test.describe("colour v2 phone 390 x 844", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const [page_, path, active] of [["Signals", "/signals", "Signals"], ["Option chain", "/option-chain", null]] as const) {
    test(`CV-1: on ${page_} only the tab on screen is brand; the others are neutral grey`, async ({ page }) => {
      await open(page, path);
      const colours = await page.locator("[data-bottom-nav-v2] > a, [data-bottom-nav-v2] > button").evaluateAll((tabs) => {
        const probe = (cls: string) => {
          const s = document.createElement("span");
          s.className = cls;
          document.body.append(s);
          const c = getComputedStyle(s).color;
          s.remove();
          return c;
        };
        const grey = probe("text-fg-muted");
        const brand = probe("text-brand");
        return tabs.map((t) => {
          const icon = t.querySelector("svg")!;
          return { text: (t.textContent ?? "").trim(), icon: getComputedStyle(icon).color, word: getComputedStyle(t).color, grey, brand };
        });
      });
      for (const c of colours) {
        const want = c.text === active ? c.brand : c.grey;
        expect(c.icon, `${c.text} icon`).toBe(want);
        expect(c.word, `${c.text} word`).toBe(want);
      }
    });
  }

  for (const theme of ["light", "dark"] as const) {
    test(`CV-4: Today's P&L is fully on screen on open, above the bottom bar, sparklines on (${theme})`, async ({ page }) => {
      await open(page, "/", theme);
      await expect(page.locator("[data-block='strip'] [data-sparkline]")).toHaveCount(3);
      const pnl = (await page.locator("[data-block='strip'] [data-pnl]").boundingBox())!;
      const bar = (await page.locator("[data-bottom-nav-v2]").boundingBox())!;
      expect(await page.evaluate(() => window.scrollY)).toBe(0);
      expect(pnl.y).toBeGreaterThanOrEqual(0);
      expect(pnl.x).toBeGreaterThanOrEqual(0);
      expect(pnl.x + pnl.width).toBeLessThanOrEqual(390);
      expect(pnl.y + pnl.height).toBeLessThanOrEqual(Math.min(844, bar.y));
      await expect(page.locator("[data-block='strip'] [data-pnl]")).toContainText("Today's P&L");
    });
  }
});
