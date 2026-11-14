import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockApi, type MockOptions } from "./mockApi";

/**
 * CTB-1 (owner 14 Nov: "Give Buy / Sell buttons; entry and exit must be possible right from the chart"): each pane of the
 * option chart sheet (CE / PE) has Buy / Sell for its own contract, opening the ONE order ticket on that contract; with a
 * held PAPER position, Exit runs its "Exit now" (confirmed, never disabled). Every candle and price here is made up
 * (round shapes, not market data). CTB_SHOTS=<dir> writes the proof screenshots (CTB_BEFORE=1: only the screenshot, for
 * the build before this change).
 */
const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as {
  expiries: Record<string, unknown>; live: Record<string, unknown> & { rows: unknown[] };
};
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON: MockOptions = { features: { ui_v2: true, paper_account: true }, brokers: CONNECTED };
const CE = "NIFTY 24500 CE 03 NOV 26";
const PE = "NIFTY 24500 PE 03 NOV 26";
const SHOTS = process.env.CTB_SHOTS ?? "";
const BEFORE = process.env.CTB_BEFORE === "1";

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

const COMPACT_PE = "NIFTY26N0324500PE";
/** His PAPER position in the PE, stored under the exchange's compact name (the server reads both names as one contract). */
const HELD = { id: 41, mode: "PAPER", symbol: COMPACT_PE, exchange: "NFO", instrument_kind: "OPTION", option_position: "BUY", strategy_id: "manual",
  direction: "LONG", entry_time: "2026-10-27T04:00:00Z", entry_price: 140, quantity: 75, stop_loss: 0, target1: null, target2: null, exit_time: null,
  exit_price: null, exit_reason: null, pnl: null, charges: 40, lot_size: 75, expiry: "2026-11-03", underlying_symbol: "NIFTY 50" };

function facts(symbol: string): Record<string, unknown> {
  const right = / CE |CE$/.test(symbol) ? "CE" : "PE";
  return { symbol, exchange: "NFO", known: true, listed: true, underlying: "NIFTY", right, strike: 24500, expiry: "2026-11-03", lot_size: 75,
    label: `NIFTY 03 NOV 24500 ${right}`, expired: false, days_left: 7, spot_symbol: "NIFTY 50", spot_exchange: "NSE", roll: null, roll_note: null };
}

type Seen = { posts: string[] };

async function open(page: Page, opts: { noPe?: boolean; held?: boolean } = {}): Promise<Seen> {
  const seen: Seen = { posts: [] };
  let held = opts.held ? [HELD] : [];
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
    if (route.request().method() === "POST" && url.pathname.includes("/order")) seen.posts.push(url.pathname);
    if (url.pathname.endsWith("/contract")) {
      const right = url.searchParams.get("right") ?? "CE";
      return route.fulfill({ json: { underlying: "NIFTY", tradingsymbol: right === "PE" ? PE : CE, exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-11-03",
        strike: 24500, right, lot_size: 75, tick_size: 0.05, label: `NIFTY 03 NOV 24500 ${right}`, freeze_quantity: null } });
    }
    if (url.pathname.endsWith("/held")) return route.fulfill({ json: [] });
    if (url.pathname.endsWith("/contracts")) {
      const body = route.request().postDataJSON() as { items: { symbol: string }[] };
      return route.fulfill({ json: { items: body.items.map((i) => facts(i.symbol)) } });
    }
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
  await page.route(/\/api(?:\/v1)?\/positions$/, (r) => r.fulfill({ json: held }));
  await page.route(/\/api(?:\/v1)?\/positions\/41\/exit-now$/, (route) => {
    seen.posts.push("/positions/41/exit-now");
    held = [];
    return route.fulfill({ json: { closed: [{ closed: true, exit_reason: "Exit now (web)", exit_price: 150, pnl: 750 }], warnings: [] } });
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
  return seen;
}

for (const [width, height] of [[1440, 900], [390, 844]] as const) {
  test.describe(`CTB-1 chart trading at ${width}`, () => {
    test.use(width === 390 ? { viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : { viewport: { width, height } });

    test("Buy on the PE pane opens the one ticket on the PE contract", async ({ page }) => {
      const calls = await open(page);
      if (BEFORE) {
        await page.waitForTimeout(1500);
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/before-${width}.png` });
        return;
      }
      const ce = page.locator("[data-option-side='CE']");
      const pe = page.locator("[data-option-side='PE']");
      for (const side of [ce, pe]) {      // one set per pane, for its own contract, 44 px on a phone, inside the sheet
        for (const action of ["buy", "sell"]) {
          const button = side.locator(`[data-chart-trade-action='${action}']`);
          await expect(button).toBeVisible();
          const box = (await button.boundingBox())!;
          if (width === 390) expect(box.height).toBeGreaterThanOrEqual(44);
          expect(box.x + box.width).toBeLessThanOrEqual(width);
        }
        await expect(side.locator("[data-chart-trade-action='exit']")).toHaveCount(0);
      }
      await expect(pe.locator("[data-chart-trade-action='buy']")).toHaveAttribute("aria-label", "Buy NIFTY 03 NOV 24500 PE");
      if (width === 390) {
        const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(sideways).toBeLessThanOrEqual(0);
      }
      // no target / stop wording anywhere on the sheet (signals, not advice)
      expect(await page.locator("[data-inplace-panel='chart']").innerText()).not.toMatch(/\b(Target|Stop)\b/);
      await page.waitForTimeout(600);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-${width}.png` });
      await pe.locator("[data-chart-trade-action='buy']").click();
      const ticket = page.locator("[data-order-ticket]");
      await expect(ticket).toBeVisible();
      await expect(ticket.getByRole("heading").first()).toHaveText("NIFTY 03 NOV 24500 PE");
      await expect(ticket).toHaveAttribute("data-step", "edit");
      await page.waitForTimeout(400);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-${width}-ticket.png` });
      expect(calls.posts.filter((u) => u.includes("/order"))).toEqual([]);   // opening the ticket places nothing
    });

    test("a held PAPER position: the chip and Exit (its Exit now, confirmed)", async ({ page }) => {
      test.skip(BEFORE, "the build before this change has no chart trading");
      const calls = await open(page, { held: true });
      const pe = page.locator("[data-option-side='PE']");
      await expect(pe.locator("[data-chart-trade-held='41']")).toContainText("Long");
      await expect(pe.locator("[data-chart-trade-held='41']")).toContainText("Avg 140.00");
      const exit = pe.locator("[data-chart-trade-action='exit']");
      await expect(exit).toBeEnabled();
      await expect(page.locator("[data-option-side='CE'] [data-chart-trade-action='exit']")).toHaveCount(0);
      await page.waitForTimeout(400);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-${width}-held.png` });
      page.once("dialog", (d) => d.accept());
      await exit.click();
      await expect(pe.locator("[data-chart-trade-note='done']")).toContainText("Closed");
      expect(calls.posts).toContain("/positions/41/exit-now");
      await expect(pe.locator("[data-chart-trade-action='exit']")).toHaveCount(0);
      await expect(pe.locator("[data-chart-trade-action='buy']")).toBeVisible();
    });
  });
}
