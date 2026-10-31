import { expect, test, type Page } from "@playwright/test";
import { NOW, SYSTEM, openAdmin } from "./adminMocks";

/**
 * M71 PL-4 / PL-6: the admin "Market data feed" card (Admin › System), from a made-up `market_feed` in the console's
 * `/admin/console/system` reply. Baselines (NEW): `admin-feed-status-v2-{1440,390}-light.png` (the card, pending gaps
 * open) - the PL-6 "feed status screenshot". The other admin baselines are untouched (their mock has no `market_feed`,
 * so the card is not drawn there). After an intended change:
 * `npx playwright test --project=phone e2e/admin-feed-status-v2.e2e.ts --update-snapshots`.
 */

const SHOT = { animations: "disabled" as const, maxDiffPixelRatio: 0.02, timeout: 15_000 };
const S = (secondsAgo: number) => new Date(Date.parse(NOW) - secondsAgo * 1000).toISOString();

const MARKET_FEED = {
  enabled: true, credential: "owner", state: "reconnecting", broker: "upstox", note: "", day: "2026-10-22",
  connections: [
    { id: "upstox-0", broker: "upstox", state: "live", symbols: 1500, last_tick_at: S(2), last_tick_age_seconds: 2, reconnects_today: 1 },
    { id: "upstox-1", broker: "upstox", state: "reconnecting", symbols: 620, last_tick_at: S(48), last_tick_age_seconds: 48, reconnects_today: 2 },
  ],
  symbols_subscribed: 2120, symbols_wanted: 2180, symbols_over_limit: 60, symbols_held_back: 4, per_connection_cap: 1500, max_connections: 2,
  universe_at: S(240), last_tick_age_seconds: 2, reconnects_today: 3, gaps_detected_today: 5, gaps_filled_today: 3, gaps_pending: 2,
  pending_gaps: [
    { symbol: "NSE_INDEX|Nifty 50", interval: "1m", start: "2026-10-22T04:01:00+00:00", end: "2026-10-22T04:04:00+00:00", status: "requested", detected_at: S(600) },
    { symbol: "NSE_INDEX|Nifty Bank", interval: "5m", start: "2026-10-22T04:00:00+00:00", end: "2026-10-22T04:05:00+00:00", status: "pending", detected_at: S(600) },
  ],
  bars_written_today: 412345, late_ticks_today: 7, stock_bars_without_volume: 0, updated_at: S(5),
};

async function open(page: Page, marketFeed: unknown) {
  await openAdmin(page, "/admin", { routes: (p) => p.route(/\/api(?:\/v1)?\/admin\/console\/system$/, (r) => r.fulfill({ json: { ...SYSTEM, market_feed: marketFeed } })) });
  const card = page.locator("[data-console-card='market-feed']");
  await expect(card).toBeVisible();
  return card;
}

for (const size of [{ name: "1440", width: 1440, height: 900, mobile: false }, { name: "390", width: 390, height: 844, mobile: true }]) {
  test.describe(`Market data feed card ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height }, isMobile: size.mobile, hasTouch: size.mobile, deviceScaleFactor: 1 });

    test("shows the feed's numbers, connections and pending gaps", async ({ page }) => {
      const card = await open(page, MARKET_FEED);
      await expect(card.locator("[data-feed-state='reconnecting']")).toContainText("Reconnecting");
      await expect(card.locator("[data-feed-stat='subscribed']")).toContainText("2,120");
      await expect(card.locator("[data-feed-connection='2']")).toContainText("48 s");
      await card.locator("[data-details='console.market-feed.gaps'] summary").click();
      await expect(card.locator("[data-feed-gap='NSE_INDEX|Nifty 50']")).toBeVisible();
      await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
      const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
      expect(r.scroll, "page wider than the screen").toBeLessThanOrEqual(r.width);
      // Phone: the connections table fits the card (no sideways scroll inside it).
      const table = await card.locator("[data-feed-connection='1']").evaluate((row) => {
        const box = row.closest("div")!;
        return { scroll: box.scrollWidth, width: box.clientWidth };
      });
      expect(table.scroll, "connections table wider than the card").toBeLessThanOrEqual(table.width);
      // Every figure's label is whole (it wraps on a phone, never cut off).
      const cut = await card.locator("[data-feed-stat] dt span").evaluateAll((els) => els.filter((e) => e.scrollWidth > e.clientWidth).length);
      expect(cut, "a figure's label is cut off").toBe(0);
      await card.scrollIntoViewIfNeeded();
      await expect(card).toHaveScreenshot(`admin-feed-status-v2-${size.name}-light.png`, SHOT);
    });

    test("says the feed is off in one line", async ({ page }) => {
      const card = await open(page, { ...MARKET_FEED, enabled: false, state: "off", connections: [], gaps_pending: 0, pending_gaps: [] });
      await expect(card.locator("[data-feed-off]")).toHaveText("Live feed is off. The owner turns it on in the server settings.");
      await expect(card.locator("[data-feed-stat]")).toHaveCount(0);
      await expect(card.locator("[data-feed-advanced]")).toBeHidden();
    });
  });
}
