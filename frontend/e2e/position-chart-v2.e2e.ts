import { expect, test, type Page } from "@playwright/test";
import { mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";
import { T, mockTrading } from "./tradingMocks";

/**
 * Owner bug report 13 Nov ("I clicked Chart and the NIFTY chart opened; the option's chart should have opened"), on
 * made-up answers only (no real account, price or id):
 * - A sold option's Chart opens its OWN premium chart in place (the panel names the contract), with the position's
 *   average on it; one tap shows the underlying (NIFTY 50) with the short strike's area; one tap back.
 * - A contract the broker sends no candles for: the underlying, with the plain line saying so (never silently).
 * - The sold option stored without a stop reads "Set premium ceiling", never "Premium ceiling 0.00"; the bought one's
 *   own floor (5.00) shows.
 * POS_CHART_SHOTS=<dir>: also writes the 1440 and 390 dark screenshots for the owner; no baseline is kept.
 */

const api = (path: string) => new RegExp(`/api(?:/v1)?${path}`);
const SHOTS = process.env.POS_CHART_SHOTS;
const SHORT_CE = "NIFTY 25150 CE 17 NOV 26";
const LONG_CE = "NIFTY 25300 CE 17 NOV 26";
const leg = (id: number, symbol: string, direction: "LONG" | "SHORT", entry: number, stop: number) => ({
  id, mode: "PAPER", symbol, strategy_id: "manual_fno", direction, entry_time: "2026-11-12T04:30:00", entry_price: entry, quantity: 130, stop_loss: stop,
  target1: null, target2: null, exit_time: null, exit_price: null, exit_reason: null, pnl: null, charges: 0, lot_size: 65, exchange: "NFO",
  instrument_kind: "OPTION", option_position: direction === "LONG" ? "BUY" : "WRITE", underlying_symbol: "NIFTY 50", expiry: "2026-11-17", mark_price: entry,
});
const POSITIONS = [leg(501, SHORT_CE, "SHORT", 174.6, 0), leg(502, LONG_CE, "LONG", 107.2, 5)];
const AREA = { low: 24_989.35, high: 25_141.3, state: "live", where: "below", distance_pct: 0.03, range_text: "24,989.35-25,141.30", from: "strike", change: null };

/** Made-up 15-minute bars: a premium near 120-180 for a contract, an index near 25,050 otherwise. */
function bars(symbol: string) {
  const start = Date.parse("2026-11-10T03:45:00Z");
  const option = symbol.endsWith(" CE 17 NOV 26");
  return Array.from({ length: 90 }, (_, i) => {
    const base = option ? 175 - i * 0.6 + Math.sin(i / 5) * 6 : 25_050 + Math.sin(i / 9) * 90;
    const w = option ? 3 : 25;
    return { timestamp: new Date(start + i * 15 * 60_000).toISOString(), open: base, high: base + w, low: base - w, close: base + w / 3, volume: 1000 + i * 7 };
  });
}

async function open(page: Page, { noCandlesFor = null as string | null } = {}) {
  await page.clock.install({ time: new Date("2026-11-13T11:00:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["charts", "positions", "terminal"]));
    } catch { /* storage unavailable */ }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  const features = { ...(T.features as Record<string, boolean>), paper_account: true, ui_v2: true, terminal: true, term_strike_area: true };
  await mockApi(page, { features, brokers: T.token_status as unknown[] });
  await mockTrading(page, { paperAccount: true });
  const calls = await mockCharts(page);
  await page.route(api("/positions(\\?.*)?$"), (r) => r.fulfill({ json: POSITIONS }));
  await page.route(api("/paper/fno/contracts$"), (r) => r.fulfill({ json: { items: [] } }));
  await page.route(api("/paper/fno/positions/groups$"), (r) => r.fulfill({ json: { groups: [] } }));
  await page.route(api("/paper/fno/group-exits"), (r) => r.fulfill({ json: [] }));
  await page.route(api("/today/my-day$"), (r) => r.fulfill({ json: { positions: [
    { id: 501, symbol: SHORT_CE, mode: "PAPER", side: "SHORT", kind: "OPTION", underlying: "NIFTY", deployment_id: null, chip: null, chip_label: null,
      room_left_pct: null, chip_text: null, expiry: "2026-11-17", expiry_sessions: 3, expiry_text: null, area: AREA, area_text: "" },
  ] } }));
  await page.route(api("/market-data/candles$"), (r) => {
    const body = r.request().postDataJSON() as { symbols: string[]; timeframe: string; before: string | null };
    const symbol = body.symbols[0];
    const list = body.before || symbol === noCandlesFor ? [] : bars(symbol);
    return r.fulfill({ json: { source: { broker: "upstox", account_label: "primary" }, exchange: "NSE", timeframe: body.timeframe, base_interval: "1min",
      lookback_days: 30, before: body.before, fetched_at: "2026-11-13T05:30:00Z", warnings: [], note: "",
      symbols: { [symbol]: { bars: list, count: list.length, first: list[0]?.timestamp ?? null, last: list.at(-1)?.timestamp ?? null, error: null,
        volume_source: null, ...(list.length ? {} : { empty_reason: "No candles yet for this contract. It may be a new contract, or your broker sent no data for this period." }) } } } });
  });
  await page.route(api("/market-data/ltp"), (r) => {
    const symbol = new URL(r.request().url()).searchParams.get("symbol") ?? "";
    const ltp = symbol === SHORT_CE ? 123.1 : symbol === LONG_CE ? 72.1 : 25_020.5;
    return r.fulfill({ json: { symbol, exchange: "NFO", source_broker: "upstox", fetched_at: "2026-11-13T05:30:00Z", ltp, timestamp: "2026-11-13T05:29:58Z",
      age_seconds: 2, stale: false, stale_reason: null, source: "tick", bid: null, ask: null, volume: null } });
  });
  await page.goto("/positions");
  await page.waitForLoadState("networkidle");
  return calls;
}

async function shot(page: Page, name: string, width: number) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}-${width}.png`, animations: "disabled" });
}

const SIZES = [{ width: 1440, height: 900, phone: false }, { width: 390, height: 844, phone: true }];
for (const size of SIZES) {
  test.describe(`position chart ${size.width}`, () => {
    test.use(size.phone ? { viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: size });

    test("Chart opens the option's own chart with its lines; one tap to NIFTY 50 and back; the stop chips", async ({ page }) => {
      await open(page);
      const short = page.locator("[data-position='501']");
      await expect(short.locator("[data-chip='level-unset']").filter({ hasText: "Set premium ceiling" })).toBeVisible();
      await expect(short).not.toContainText("Premium ceiling 0.00");
      await expect(page.locator("[data-position='502']")).toContainText("Premium floor 5.00");
      await shot(page, "after-row", size.width);

      await short.getByTestId("position-chart").click();
      const chart = page.locator("[data-position-chart='501']");
      await expect(chart).toHaveAttribute("data-view", "contract");
      await expect(page.locator(`[data-embedded-chart='${SHORT_CE}']`)).toBeVisible();
      await expect(chart.locator("[data-position-strip]")).toContainText("174.60");
      await expect(chart.locator("[data-ltp]")).toContainText("123.10");
      await expect(page).toHaveURL(/\/positions\?/);                       // in place: the tab stays
      if (size.phone) await expectPhoneLayout(page);
      await page.waitForTimeout(400);
      await shot(page, "after-option-chart", size.width);

      await chart.getByRole("button", { name: "NIFTY 50" }).click();
      await expect(chart).toHaveAttribute("data-view", "underlying");
      await expect(page.locator("[data-embedded-chart='NIFTY 50']")).toBeVisible();
      await expect(chart.locator("[data-underlying-note]")).toHaveText("NIFTY 50, the underlying of your NIFTY 25150 CE position.");
      await page.waitForTimeout(400);
      await shot(page, "after-underlying-chart", size.width);
      await chart.getByRole("button", { name: "Option" }).click();
      await expect(chart).toHaveAttribute("data-view", "contract");
    });

    test("a contract without candles: NIFTY 50, said plainly, with a way back", async ({ page }) => {
      await open(page, { noCandlesFor: LONG_CE });
      await page.locator("[data-position='502']").getByTestId("position-chart").click();
      const chart = page.locator("[data-position-chart='502']");
      await expect(chart).toHaveAttribute("data-view", "underlying");
      await expect(chart.locator("[data-no-candles]")).toContainText(`No candles for ${LONG_CE} from your broker. This chart is NIFTY 50, the underlying.`);
      await expect(page.locator("[data-embedded-chart='NIFTY 50']")).toBeVisible();
      await expect(chart.getByRole("button", { name: "Try the option chart again" })).toBeVisible();
      if (size.phone) await expectPhoneLayout(page);
      await page.waitForTimeout(400);
      await shot(page, "after-no-candles", size.width);
    });
  });
}
