import { expect, test, type Page } from "@playwright/test";
import { mockChartReading, mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { expectPhoneLayout } from "./phoneLayout";

/**
 * M75 W-2 (owner order M75 CW-1 and CW-8; flag `copilot_workspace`): the chart workspace in the Copilot's "Read the
 * chart" tab, in a browser with every reply made up here (no backend). The PR plan's W-2 proofs:
 * - opening the tab with a chart reads it ONCE (one `GET /api/charts/read`); a new timeframe reads again, a new symbol
 *   reads again - and nothing else does;
 * - a futures / option contract is offered by the symbol search, and its chart without prices says so;
 * - 360 px and 390 px: no sideways scroll, the chart full width; Market Pulse stays under the workspace.
 * `W2_OUT=<folder>` also writes screenshots (the comparison page), never baselines.
 */
const OUT = process.env.W2_OUT;
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ROWS: Record<string, { tradingsymbol: string; exchange: string; name: string; instrument_type: string }[]> = {
  INDEX: [], EQ: [{ tradingsymbol: "RELIANCE", exchange: "NSE", name: "Reliance Industries", instrument_type: "EQ" }],
  FUT: [{ tradingsymbol: "NIFTY26NOVFUT", exchange: "NFO", name: "NIFTY", instrument_type: "FUT" }], CE: [], PE: [],
};

interface Seen { reads: string[] }

async function open(page: Page, theme: "light" | "dark", features: Record<string, boolean> = {}): Promise<Seen> {
  await page.clock.install({ time: new Date("2026-10-19T11:00:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
    } catch { /* storage unavailable */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: { ui_v2: true, ai_copilot: true, read_chart: true, copilot_workspace: true, ...features }, brokers: CONNECTED });
  await mockCharts(page, {});
  await mockChartReading(page);
  const seen: Seen = { reads: [] };
  await page.route(/\/api(?:\/v1)?\/charts\/read\?/, async (route) => {
    const url = new URL(route.request().url());
    seen.reads.push(`${url.searchParams.get("instrument")} ${url.searchParams.get("tf")}`);
    return route.fallback();
  });
  // A contract with no prices on this timeframe (the data has none): an empty answer with no reason of its own.
  await page.route(/\/api(?:\/v1)?\/market-data\/candles$/, async (route) => {
    const body = route.request().postDataJSON() as { symbols: string[]; timeframe: string };
    if (body.symbols[0] !== "NIFTY26NOVFUT") return route.fallback();
    return route.fulfill({ json: { source: { broker: "upstox", account_label: "primary" }, exchange: "NFO", timeframe: body.timeframe, base_interval: "1min",
      lookback_days: 30, before: null, fetched_at: "2026-10-19T05:30:00Z", warnings: [], note: "",
      symbols: { NIFTY26NOVFUT: { bars: [], count: 0, first: null, last: null, error: null, volume_source: null } } } });
  });
  await page.route(/\/api(?:\/v1)?\/instrument-master\/search/, (route) => {
    const type = new URL(route.request().url()).searchParams.get("type") ?? "";
    return route.fulfill({ json: ROWS[type] ?? [] });
  });
  await page.goto("/copilot/read?symbol=NIFTY%2050");
  await page.addStyleTag({ content: "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }" });
  return seen;
}

const analysis = (page: Page) => page.locator("[data-workspace-analysis]");

for (const [width, height] of [[360, 800], [390, 844], [1440, 900]] as const) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`Copilot chart workspace ${width} ${theme}`, () => {
      test.use({ viewport: { width, height }, deviceScaleFactor: 1, ...(width < 768 ? { isMobile: true, hasTouch: true } : {}) });

      test(`opening reads once; a new timeframe and a new symbol read again (${width} ${theme})`, async ({ page }) => {
        const seen = await open(page, theme);
        const ws = page.locator("[data-copilot-workspace='NIFTY 50']");
        await expect(ws).toBeVisible({ timeout: 15_000 });
        await expect(ws.locator("[data-embedded-chart='NIFTY 50'] canvas").first()).toBeVisible({ timeout: 15_000 });
        await expect(analysis(page)).toHaveAttribute("data-workspace-analysis", "ready", { timeout: 15_000 });
        await page.waitForTimeout(600);
        expect(seen.reads).toEqual(["NSE:NIFTY 50 5m"]);                       // opening the chart read it, once
        // Market Pulse stays in the same tab, under the workspace.
        await expect(page.locator("#copilot-read-pulse")).toBeVisible();
        if (width < 768) {
          await expectPhoneLayout(page);
          const box = await ws.locator("[data-workspace-chart]").boundingBox();
          expect(box?.width).toBeGreaterThanOrEqual(width - 1);                // the chart is full width on a phone
        }
        if (OUT) await page.screenshot({ path: `${OUT}/workspace-${width}-${theme}.png`, animations: "disabled" });

        const tfs = ws.getByRole("group", { name: "Timeframe" });
        if (await tfs.getByRole("button", { name: "15 min", exact: true }).count()) await tfs.getByRole("button", { name: "15 min", exact: true }).click();
        else {                                                                   // a phone: the one dropdown
          await tfs.getByRole("button", { name: /^Timeframe:/ }).click();
          await page.getByRole("menu", { name: "Timeframes" }).getByRole("menuitemradio", { name: "15 min", exact: true }).click();
        }
        await expect.poll(() => seen.reads).toEqual(["NSE:NIFTY 50 5m", "NSE:NIFTY 50 15m"]);   // a new timeframe: read again

        const field = ws.getByRole("combobox", { name: "Symbol" });
        await field.fill("RELI");
        await page.getByRole("option", { name: /RELIANCE/ }).click();
        await expect(page.locator("[data-copilot-workspace='RELIANCE']")).toBeVisible();
        await expect.poll(() => seen.reads).toEqual(["NSE:NIFTY 50 5m", "NSE:NIFTY 50 15m", "NSE:RELIANCE 15m"]);   // a new symbol: read again
        await page.waitForTimeout(600);
        expect(seen.reads).toHaveLength(3);                                     // and nothing else read it
      });
    });
  }
}

test.describe("Copilot chart workspace: contracts and the flag", () => {
  test.use({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true });

  test("a futures contract is offered by the search and its chart without prices says so", async ({ page }) => {
    await open(page, "light");
    const ws = page.locator("[data-copilot-workspace]");
    await expect(ws.locator("[data-embedded-chart] canvas").first()).toBeVisible({ timeout: 15_000 });
    const field = ws.getByRole("combobox", { name: "Symbol" });
    await field.fill("NIFTY26");
    await page.getByRole("option", { name: /NIFTY26NOVFUT/ }).click();
    await expect(page.locator("[data-copilot-workspace='NIFTY26NOVFUT']")).toBeVisible();
    await expect(page.locator("[data-embedded-chart='NIFTY26NOVFUT']")).toContainText(/No (prices|candles)/, { timeout: 15_000 });
    await expectPhoneLayout(page);
    if (OUT) await page.screenshot({ path: `${OUT}/workspace-contract-390-light.png`, animations: "disabled" });
  });

  test("with copilot_workspace off the tab is Market Pulse, as before", async ({ page }) => {
    await open(page, "light", { copilot_workspace: false });
    await expect(page.locator("#copilot-read-pulse")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("[data-copilot-workspace]")).toHaveCount(0);
  });
});
