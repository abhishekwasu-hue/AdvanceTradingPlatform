import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi, type MockOptions } from "./mockApi";

/**
 * CEPE-1 (owner 13 Nov: "show CE and the same-strike PE as 2 charts, one below the other"): the chain's "Chart of this
 * strike" opens a sheet with the CE on top and the same-strike PE below, each half the height (the sheet is filled),
 * with one timeframe bar and a "CE · PE · Both" switch. Every candle and price here is made up (round shapes, not
 * market data). CEPE_SHOTS=<dir> also writes the proof screenshots (CEPE_BEFORE=1: only the screenshot, for the build
 * before this change).
 */
const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown> & { rows: unknown[] };
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, paper_account: true }, brokers: CONNECTED };
const CE = "NIFTY 24500 CE 03 NOV 26";
const PE = "NIFTY 24500 PE 03 NOV 26";
const SHOTS = process.env.CEPE_SHOTS ?? "";
const BEFORE = process.env.CEPE_BEFORE === "1";

/** Made-up 15-minute bars over three sessions: the CE drifting down from 200, the PE up from 120. */
function bars(symbol: string): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = [];
  const up = symbol.includes(" PE ");
  let price = up ? 120 : 200;
  let k = 0;
  for (const day of ["2026-10-23", "2026-10-26", "2026-10-27"]) {
    const start = Date.parse(`${day}T09:15:00+05:30`);
    const end = day === "2026-10-27" ? Date.parse(`${day}T10:15:00+05:30`) : Date.parse(`${day}T15:15:00+05:30`);
    for (let t = start; t <= end; t += 15 * 60_000) {
      const step = (up ? 0.6 : -0.7) + Math.sin(k / 3) * 2.5;
      const open = price;
      price = Math.max(5, Math.round((price + step) * 20) / 20);
      out.push({ timestamp: new Date(t).toISOString(), open, high: Math.max(open, price) + 1.5, low: Math.min(open, price) - 1.5, close: price, volume: 1000 + (k % 7) * 150 });
      k += 1;
    }
  }
  return out;
}

const lastClose = (symbol: string) => bars(symbol).at(-1)!.close as number;

async function open(page: Page, opts: { noPe?: boolean } = {}): Promise<void> {
  await page.clock.install({ time: new Date("2026-10-27T10:30:00+05:30") });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: "dark" }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["option-chain"]));
    } catch { /* storage unavailable */ }
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get: () => "visible" });
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get: () => false });
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, ON);
  const sessions = { NSE: { is_open: true, reason: "made up", next_open: null }, NFO: { is_open: true, reason: "made up", next_open: null } };
  await page.route(/\/api(?:\/v1)?\/system\/status$/, (r) => r.fulfill({ json: { maintenance_mode: false, maintenance_message: null, disabled_brokers: [], sessions } }));
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/expiries")) return route.fulfill({ json: DATA.expiries });
    return route.fulfill({ json: { ...DATA.live, expiry: url.searchParams.get("expiry") ?? DATA.live.expiry, market_open: true } });
  });
  await page.route(/\/api(?:\/v1)?\/paper\/fno\//, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/contract")) {
      const right = url.searchParams.get("right") ?? "CE";
      return route.fulfill({ json: { underlying: "NIFTY", tradingsymbol: right === "PE" ? PE : CE, exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-11-03",
        strike: 24500, right, lot_size: 75, tick_size: 0.05, label: `NIFTY 03 NOV 24500 ${right}`, freeze_quantity: null } });
    }
    if (url.pathname.endsWith("/held")) return route.fulfill({ json: [] });
    if (url.pathname.endsWith("/live-status")) return route.fulfill({ json: { open: false, text: "Live trading is switched off by the admin." } });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.route(/\/api(?:\/v1)?\/market-data\/candles$/, (r) => {
    const body = r.request().postDataJSON() as { symbols: string[]; timeframe: string; lookback_days: number; before?: string };
    const symbol = body.symbols[0];
    const list = body.before || (opts.noPe && symbol === PE) ? [] : bars(symbol);
    return r.fulfill({ json: {
      source: { broker: "upstox", account_label: "primary" }, exchange: "NFO", timeframe: body.timeframe, base_interval: "1min",
      lookback_days: body.lookback_days, before: body.before ?? null, fetched_at: "2026-10-27T05:00:00Z", warnings: [], note: "",
      symbols: { [symbol]: { bars: list, count: list.length, first: list[0]?.timestamp ?? null, last: list.at(-1)?.timestamp ?? null, error: null, volume_source: null } },
    } });
  });
  await page.route(/\/api(?:\/v1)?\/market-data\/ltp/, (r) => {
    const symbol = new URL(r.request().url()).searchParams.get("symbol") ?? "";
    const ltp = lastClose(symbol);   // the last bar's close: the header and the chart's price tag agree
    return r.fulfill({ json: { symbol, exchange: "NFO", source_broker: "upstox", fetched_at: "2026-10-27T05:00:00Z", ltp, timestamp: "2026-10-27T04:59:58Z",
      age_seconds: 2, stale: false, stale_reason: null, source: "tick", bid: null, ask: null, volume: null } });
  });
  await page.goto("/option-chain");
  await page.addStyleTag({ content: [
    "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
    "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  ].join("\n") });
  await expect(page.locator("tr[data-atm]")).toBeVisible();
  await page.locator("tr[data-strike='24500'] td:visible").first().click();
  await page.locator("[data-chain-actions] [data-action='chart']").click();
  await expect(page.locator("[data-inplace-panel='chart']")).toBeVisible();
}

for (const [width, height] of [[1440, 900], [390, 844]] as const) {
  test.describe(`CEPE-1 option sheet at ${width}`, () => {
    test.use(width === 390 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });

    test("the CE on top and the same-strike PE below fill the sheet; the switch shows one", async ({ page }) => {
      await open(page);
      if (BEFORE) {
        await page.waitForTimeout(1500);
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/before-${width}-dark.png` });
        return;
      }
      const ce = page.locator("[data-option-side='CE']");
      const pe = page.locator("[data-option-side='PE']");
      await expect(ce.locator("h3")).toHaveText(CE);
      await expect(pe.locator("h3")).toHaveText(PE);
      await expect(ce.locator("[data-option-ltp]")).toHaveText(lastClose(CE).toFixed(2));
      await expect(pe.locator("[data-option-ltp]")).toHaveText(lastClose(PE).toFixed(2));
      for (const side of [ce, pe]) {   // the change is coloured by its sign
        const change = side.locator("[data-option-change]");
        const text = await change.innerText();
        await expect(change).toHaveAttribute("data-option-change", text.startsWith("+") ? "up" : "down");
        await expect(change).toHaveClass(text.startsWith("+") ? /text-up/ : /text-down/);
      }
      // nothing cut: the contract name is shown whole
      for (const side of [ce, pe]) expect(await side.locator("h3").evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
      await expect(ce.locator("canvas").first()).toBeVisible();
      await expect(pe.locator("canvas").first()).toBeVisible();
      // one timeframe bar (on the CE), both charts below one another, together filling the sheet's height
      await expect(page.locator("[data-inplace-panel='chart'] [aria-label^='Chart controls']")).toHaveCount(1);
      const sheet = (await page.getByRole("dialog").boundingBox())!;
      const a = (await ce.boundingBox())!, b = (await pe.boundingBox())!;
      expect(b.y).toBeGreaterThan(a.y + a.height - 1);
      expect(Math.abs(a.height - b.height)).toBeLessThan(60);
      expect(b.y + b.height).toBeGreaterThan(sheet.y + sheet.height - 40);   // nothing blank under the charts
      if (width === 390) {
        expect(sheet.height).toBeGreaterThan(height - 2);                     // full screen on a phone
        const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(sideways).toBeLessThanOrEqual(0);
      }
      await page.waitForTimeout(600);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-${width}-dark.png` });
      await page.locator("[data-option-pair-switch] [data-option='PE']").click();
      await expect(ce).toHaveCount(0);
      const alone = (await pe.boundingBox())!;
      expect(alone.height).toBeGreaterThan(a.height * 1.6);                 // the one chart fills the sheet
      await expect(page.locator("[data-inplace-panel='chart'] [aria-label^='Chart controls']")).toHaveCount(1);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-${width}-dark-pe-only.png` });
      await page.locator("[data-option-pair-switch] [data-option='both']").click();
      await expect(ce).toBeVisible();
      const text = await page.locator("[data-inplace-panel='chart']").innerText();
      expect(text).not.toMatch(/\b(Buy|Sell|Target|Stop)\b/);
    });
  });
}

test("CEPE-1: a side without candles says so on its half; the other side stays", async ({ page }) => {
  test.skip(BEFORE, "the build before this change has no PE chart");
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, { noPe: true });
  await expect(page.locator("[data-option-side='PE']")).toContainText(`No candles yet for ${PE}. Your broker sent no data for this period.`);
  await expect(page.locator("[data-option-side='CE'] canvas").first()).toBeVisible();
});
