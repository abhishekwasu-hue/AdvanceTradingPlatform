import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { DEMO_ANALYSES } from "../src/optionChain/analyses.fixture";
import { mockCharts } from "./chartsMocks";
import { mockApi } from "./mockApi";
import { readCards, textBudgetProblems, visibleWords } from "./textBudget";
import { expectPhoneLayout } from "./phoneLayout";
import { pickOption } from "./combobox";

/**
 * Trading Terminal T1 (/terminal) with `ui_v2`, `terminal` and the practice account on, on made-up answers only (no real
 * account, price or id): the made-up chain of e2e/fixtures/option_chain_v2.json, made-up candles (chartsMocks), and the
 * status, positions and orders below.
 * - 1440: the Option seller layout (the default for a new user), switching layouts (remembered on the server), saving
 *   his own, moving a panel with the keyboard, no sideways scroll; the chain fills the order panel (the one ticket).
 * - 390 and 360: the panels as tabs, one at a time, the short status bar, no sideways scroll, 44 px targets.
 * - Flag off: no navigation entry and "Coming soon" at the address.
 * - TERMINAL_SHOTS=<dir>: also writes the screenshots for the owner's comparison page (light and dark); no baselines are
 *   kept in the repository for this page yet (the flag stays off until the owner's approval).
 * - TERMINAL_MEASURE=1: prints the T8 measurements this browser can honestly make (layout shift while panels update, click
 *   to the order request leaving the page, chart pan frame rate with 5,000 bars).
 */
const DATA = JSON.parse(readFileSync(new URL("./fixtures/option_chain_v2.json", import.meta.url), "utf8")) as { expiries: unknown; live: Record<string, unknown> };
const CONNECTED = [{ broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null, login_url_supported: false, code_param: null }];
const ON = { ui_v2: true, terminal: true, paper_account: true, data_hub: true, smart_execution: true };
const THEMES = ["light", "dark"] as const;
const SHOTS = process.env.TERMINAL_SHOTS;
const MEASURE = process.env.TERMINAL_MEASURE === "1";

const STATUS = {
  today: { mtm: 2650, booked: 1900, note: "Booked today plus your open positions' profit or loss since entry, at their last price." },
  open: { count: 2, priced: 2, mtm: 750, as_of: "2026-10-27T05:00:00Z" },
  funds: { balance: 1_001_900, available: 993_300, source: "practice", note: "Your practice balance minus the margin estimate." },
  margin: { used: 8600, estimated: true, not_estimated: 0, note: "ATP's estimate, not your broker's number." },
  mode: { mode: "PAPER", live_allowed: false, message: "Practice mode: no real order is sent and no money moves." },
  feed: { state: "live", message: "Live prices from Upstox.", broker_name: "Upstox" },
  kill_switch: { engaged: false, scope: null, message: "New orders are allowed." },
  as_of: "2026-10-27T05:00:00Z",
};
const NOW = "2026-10-27T04:45:00Z";
const POSITION = (id: number, symbol: string, direction: string, price: number) => ({
  id, mode: "PAPER", symbol, strategy_id: "manual_fno", direction, entry_time: NOW, entry_price: price, quantity: 75, stop_loss: direction === "LONG" ? 0.05 : price * 2,
  target1: null, target2: null, exit_time: null, exit_price: null, exit_reason: null, pnl: null, charges: 0, charges_source: "ESTIMATED",
  instrument_kind: "OPTION", exchange: "NFO", lot_size: 75, expiry: "2026-11-03", option_position: direction === "LONG" ? "BUY" : "WRITE", mark_price: price,
});
const POSITIONS = [POSITION(41, "NIFTY 25000 CE 03 NOV 26", "SHORT", 120), POSITION(42, "NIFTY 25100 CE 03 NOV 26", "LONG", 80)];
const ORDER = (id: number, symbol: string, direction: string, status: string, reasons: string[] = []) => ({
  id, mode: "PAPER", strategy_id: "manual_fno", symbol, direction, quantity: 75, status, idempotency_key: null, broker_order_id: null, algo_tag: null,
  trade_id: status === "POSITION_OPEN" ? id : null, reasons, created_at: NOW, updated_at: NOW,
});
const ORDERS = [ORDER(41, "NIFTY 25000 CE 03 NOV 26", "SHORT", "POSITION_OPEN"), ORDER(42, "NIFTY 25100 CE 03 NOV 26", "LONG", "POSITION_OPEN"),
  ORDER(43, "NIFTY 24000 PE 03 NOV 26", "LONG", "REJECTED", ["Today's loss limit is reached: no new entries today. Exits still work."])];
const CONTRACT = { underlying: "NIFTY", tradingsymbol: "NIFTY26N0324500CE", exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-11-03",
  strike: 24500, right: "CE", lot_size: 75, tick_size: 0.05, label: "NIFTY 03 NOV 24500 CE", freeze_quantity: null };

function preview(body: Record<string, unknown>) {
  const lots = Number(body.lots ?? 1);
  const fill = body.side === "SELL" ? 149.5 : 150.5;
  return {
    mode: "PAPER", practice: "Practice mode (PAPER): no real order is sent and no money moves.", contract: CONTRACT, label: CONTRACT.label,
    side: body.side, lots, lot_size: 75, units: lots * 75, order_type: body.order_type, product: body.product, price: 150, price_source: "live", fill_price: fill,
    fill_note: `Fills at the live price ${fill.toFixed(2)}`, stop_loss: body.stop_loss ?? null,
    margin: { value: fill * lots * 75, basis: "Long options only: the premium paid.", estimated: true },
    max_loss: { value: fill * lots * 75, basis: "The premium you pay, if the option expires worthless." }, charges: { total: 42.5, estimated: true },
    risk: null, errors: [], refusals: [], warnings: [], can_place: true, confirm_label: null, as_of: "2026-10-27T05:00:00+00:00",
  };
}

interface Seen { puts: { url: string; body: Record<string, unknown> }[]; orderPosts: { at: number; url: string }[] }

const INCOMPLETE = {
  group_id: "g1", underlying: "NIFTY", strategy: "Bear call spread", mode: "PAPER", state: "INCOMPLETE",
  message: "Incomplete: the hedge filled, the sold leg did not go through. Nothing more is sent until you choose.",
  failure: "The sold leg was not accepted.", deadline: null, seconds_left: null, on_no_answer: "keep", rule: null,
  filled: [{ trade_id: 42, symbol: "NIFTY 25100 CE 03 NOV 26", side: "BUY", role: "hedge", quantity: 75, entry_price: 80, open: true }],
  pending: [{ symbol: "NIFTY 25000 CE 03 NOV 26", side: "SELL", role: "sold", quantity: 75 }], actions: ["complete_rest", "close_filled", "keep"],
};
const EVALUATION = {
  ok: true, errors: [], prices: [], today: [], on_date: [], at_expiry: null, days_forward: 0, iv_shift: 0, single_expiry: true,
  extremes: { max_profit: 3000, max_loss: -4500, unbounded_profit: false, unbounded_loss: false }, profitable: null, breakevens: [25040],
  summary: { expiry: "2026-11-03", t_years: 0.02, sigma: 0.13, pop: 0.61, expected_move: 300, expected_pnl: 0, greeks: { delta: 0, gamma: 0, theta: 0, vega: 0 }, method: "", model: "" },
  legs: [], disclaimer: "", net_premium: 3000, reward_to_risk: null, costs: { total: 80, estimated: true },
  margin: { value: 32000, estimated: true, basis: "Estimate", note: "",
    hedge: { with_hedge: { value: 32000, estimated: true, basis: "With the hedge" }, without_hedge: { value: 145000, estimated: true, basis: "Sold leg alone" },
      benefit: 113000, hedged: true, source: "estimate", note: "" } },
  lot_sizes: [75, 75], labels: [], order: [1, 0],
};

async function open(page: Page, theme: "light" | "dark", opts: {
  features?: Record<string, boolean>; bars?: number; vary?: boolean; incomplete?: boolean;
  /** TERM-2 step 4: legs already in the basket, and extra routes registered last (they win over the ones above). */
  basket?: unknown[]; routes?: (page: Page) => Promise<void>;
  /** M75 NAV-3: another Terminal address (the previous Terminal at `?view=classic`). */
  path?: string;
  /** The layout in use stored on the server (owner 13 Nov: "option_buyer"); absent: none (the Option seller). */
  active?: string;
} = {}): Promise<Seen> {
  await page.clock.install({ time: new Date("2026-10-27T10:30:00+05:30") });
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("atp_appearance", JSON.stringify({ theme: t }));
      localStorage.setItem("atp_install_dismissed", "1");
      localStorage.setItem("atp_page_intro_dismissed:2", JSON.stringify(["terminal"]));
      sessionStorage.removeItem("atp_fno_basket");
      sessionStorage.removeItem("atp_roll_pending");
    } catch { /* storage unavailable */ }
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get: () => "visible" });
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get: () => false });
    // T8: every layout shift from the first paint on (input-driven shifts excluded, as CLS does).
    const w = window as unknown as { __cls: number };
    w.__cls = 0;
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries() as unknown as { value: number; hadRecentInput: boolean }[]) if (!e.hadRecentInput) w.__cls += e.value;
      }).observe({ type: "layout-shift", buffered: true });
    } catch { /* no layout-shift entries in this browser */ }
  }, theme);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await mockApi(page, { features: opts.features ?? ON, brokers: CONNECTED });
  await mockCharts(page);
  // Owner 9 Nov: the chain panel is the Option chain page, with its analyses below the chain (the OC2 fixture, as on that page)
  await page.route(/\/api(?:\/v1)?\/option-chain\/analyses/, (r) => r.fulfill({ json: { ...DEMO_ANALYSES, symbol: "NIFTY" } }));
  const seen: Seen = { puts: [], orderPosts: [] };
  if (opts.bars) {
    const n = opts.bars;
    await page.route(/\/api(?:\/v1)?\/market-data\/candles$/, (r) => {
      const body = r.request().postDataJSON() as { symbols: string[]; timeframe: string; before: string | null };
      const start = Date.parse("2026-01-01T03:45:00Z");
      const bars = body.before ? [] : Array.from({ length: n }, (_, i) => {
        const base = 24000 + Math.sin(i / 40) * 300 + (i % 7) * 3;
        return { timestamp: new Date(start + i * 15 * 60_000).toISOString(), open: base, high: base + 12, low: base - 12, close: base + 4, volume: 1000 + i };
      });
      return r.fulfill({ json: { source: { broker: "upstox", account_label: "primary" }, exchange: "NSE", timeframe: body.timeframe, base_interval: "1min",
        lookback_days: 30, before: body.before, fetched_at: NOW, warnings: [], note: "",
        symbols: { [body.symbols[0]]: { bars, count: bars.length, first: bars[0]?.timestamp ?? null, last: bars.at(-1)?.timestamp ?? null, error: null, volume_source: null } } } });
    });
  }
  let stored: Record<string, unknown> = { layouts: {}, active: opts.active ?? null };
  let version = 0;
  await page.route(/\/api(?:\/v1)?\/me\/prefs(\/[a-z_]+)?(\?.*)?$/, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (req.method() === "PUT" && url.pathname.endsWith("/terminal")) {
      stored = req.postDataJSON() as Record<string, unknown>;
      seen.puts.push({ url: url.pathname + url.search, body: stored });
      version += 1;
      return route.fulfill({ json: { namespace: "terminal", value: stored, version } });
    }
    if (req.method() === "PUT") return route.fulfill({ json: { namespace: "chart", value: req.postDataJSON(), version: 2 } });
    return route.fulfill({ json: { prefs: { chart: {}, terminal: stored }, versions: { chart: 1, terminal: version } } });
  });
  let reads = 0;
  // TERM-2 step 1: the context bar's India VIX and the previous close (the Today strip).
  await page.route(/\/api(?:\/v1)?\/market\/strip$/, (r) => r.fulfill({ json: { indices: [
    { key: "NIFTY 50", label: "NIFTY 50", ltp: 24480, prev_close: 24400 }, { key: "INDIA VIX", label: "INDIA VIX", ltp: 13.4, prev_close: 13.1 },
  ] } }));
  await page.route(/\/api(?:\/v1)?\/terminal\/status$/, (r) => {
    reads += 1;
    // `vary`: every read brings new figures (a bigger and a smaller number in turn), as a moving market does.
    const k = opts.vary ? (reads % 2 ? 137 * reads : -9_871 * reads) : 0;
    return r.fulfill({ json: { ...STATUS, today: { ...STATUS.today, mtm: STATUS.today.mtm + k }, open: { ...STATUS.open, mtm: STATUS.open.mtm + k } } });
  });
  await page.route(/\/api(?:\/v1)?\/positions\?mine=true$/, (r) => r.fulfill({ json: POSITIONS }));
  await page.route(/\/api(?:\/v1)?\/orders\?mine=true/, (r) => r.fulfill({ json: ORDERS }));
  await page.route(/\/api(?:\/v1)?\/orders\/\d+\/events$/, (r) => r.fulfill({ json: [
    { id: 1, from_status: null, to_status: "CREATED", detail: "", created_at: NOW },
    { id: 2, from_status: "CREATED", to_status: "POSITION_OPEN", detail: "", created_at: NOW },
  ] }));
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/(expiries|live)/, (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/expiries")) return route.fulfill({ json: DATA.expiries });
    // M96: the server's ATM ± 6 PCR (Trade's banner window), what the context bar shows; made up like the rest.
    return route.fulfill({ json: { ...DATA.live, summary: { ...(DATA.live.summary as Record<string, unknown>), pcr_oi_atm: 0.91 }, market_open: true } });
  });
  // T1-6: live prices for P&L on every card (the My trades read), the two legs as one basket group.
  await page.route(/\/api(?:\/v1)?\/portfolio\/exposure/, (r) => r.fulfill({ json: {
    price_source: "ltp", currency: "INR", open_positions: 2, unrealised_pnl: 750, realised_today: 1900,
    priced_symbols: POSITIONS.map((p) => p.symbol),
    by_symbol: [{ symbol: POSITIONS[0].symbol, notional: 110 * 75 }, { symbol: POSITIONS[1].symbol, notional: 90 * 75 }],
  } }));
  await page.route(/\/api(?:\/v1)?\/trading\/incomplete$/, (r) => r.fulfill({ json: { items: opts.incomplete ? [INCOMPLETE] : [], as_of: NOW } }));
  await page.route(/\/api(?:\/v1)?\/paper\/fno\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    // M96-Q1: the ticket reads whether LIVE can go (off here, as by default).
    if (url.pathname.endsWith("/live-status")) return route.fulfill({ json: { open: false, text: "Live trading is switched off by the admin." } });
    if (url.pathname.endsWith("/positions/groups")) return route.fulfill({ json: { groups: [{ key: "basket:b1", kind: "basket", name: "Bear call spread", trade_ids: [41, 42], hedge_ids: [42] }] } });
    // CX6: no combined exit set here (its own spec: e2e/combined-exit-v2.e2e.ts).
    if (url.pathname.endsWith("/group-exits")) return route.fulfill({ json: [] });
    if (url.pathname.endsWith("/contracts")) return route.fulfill({ json: { items: [] } });
    if (url.pathname.endsWith("/basket/settings")) return route.fulfill({ json: { source: null, width_steps: 2, lots: 1, stop_pct: 100, note: "" } });
    if (url.pathname.endsWith("/basket/evaluate")) return route.fulfill({ json: EVALUATION });
    if (url.pathname.endsWith("/contract")) return route.fulfill({ json: { ...CONTRACT, strike: Number(url.searchParams.get("strike")), right: url.searchParams.get("right") } });
    if (url.pathname.endsWith("/held")) return route.fulfill({ json: [] });
    if (url.pathname.endsWith("/resting")) return route.fulfill({ json: [] });         // T2: no waiting orders here
    if (url.pathname.endsWith("/risk-budget") || url.pathname.endsWith("/event-risk")) return route.fulfill({ status: 404, json: { detail: "Not found" } });
    const body = request.postDataJSON() as Record<string, unknown>;
    if (url.pathname.endsWith("/order/preview")) return route.fulfill({ json: preview(body) });
    seen.orderPosts.push({ at: Date.now(), url: url.pathname });
    return route.fulfill({ json: { ...preview(body), status: "PLACED", reasons: [], order_id: 9, trade_id: 44, filled: { price: 150.5, quantity: 75, lots: 1 } } });
  });
  if (opts.basket) {
    await page.addInitScript((legs) => { try { sessionStorage.setItem("atp_fno_basket", JSON.stringify(legs)); } catch { /* storage unavailable */ } }, opts.basket);
  }
  if (opts.routes) await opts.routes(page);
  await page.goto(opts.path ?? "/terminal");
  await settle(page);
  return seen;
}

async function settle(page: Page) {
  await page.addStyleTag({ content: [
    "*, *::before, *::after { font-family: 'DejaVu Sans', sans-serif !important; caret-color: transparent !important; }",
    "* { scrollbar-width: none !important; } *::-webkit-scrollbar { display: none !important; }",
  ].join("\n") });
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 15_000 });
}

async function shot(page: Page, name: string) {
  if (!SHOTS) return;
  // Not `fullPage: true`: in this (mobile-emulation) project a full-page capture flips the window to 1 px wide for a moment,
  // which crosses the phone breakpoint and remounts the grid (a maximised panel was lost, M58 double-click test). Grow the
  // viewport to the page's height instead - the width never changes - then put it back.
  const view = page.viewportSize();
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  if (view && height > view.height) await page.setViewportSize({ width: view.width, height });
  await page.screenshot({ path: `${SHOTS}/${name}.png`, animations: "disabled" });
  if (view && height > view.height) await page.setViewportSize(view);
}

async function noSidewaysScroll(page: Page) {
  const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
  expect(r.scroll).toBeLessThanOrEqual(r.width);
}

test.describe("v2 terminal 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`the Option seller layout, the status bar, no sideways scroll (${theme})`, async ({ page }) => {
      await open(page, theme);
      await expect(page.locator("[data-page='terminal-v2']")).toHaveAttribute("data-layout", "option_seller");
      const panels = page.locator("[data-terminal-grid] [data-panel]");
      // TERM-2 step 3: chart | chain | basket above the one bottom panel (Positions · Orders · Alerts).
      await expect(panels).toHaveCount(4);
      for (const id of ["chart-1", "chain", "basket", "positions"]) await expect(page.locator(`[data-panel='${id}']`)).toBeVisible();
      const account = page.locator("[data-panel='positions'] [data-panel-body='account']");
      await expect(account.getByRole("tab")).toHaveText([/^Positions\s*2$/, "Orders", "Alerts"]);
      await expect(page.locator("[data-terminal-chain] tr[data-atm]")).toContainText("24,500");
      await expect(page.locator("[data-status='today']")).toContainText("+₹2,650");
      await expect(page.locator("[data-status='margin']")).toContainText("(estimate)");
      // TERM-2 step 1: one PAPER badge, in the context bar (the status bar no longer repeats it).
      await expect(page.locator("[data-terminal='mode']")).toHaveText("PAPER");
      await expect(page.locator("[data-status='mode']")).toHaveCount(0);
      await expect(page.locator("[data-status='feed']")).toHaveCount(0);           // CT-1 C9: the shell / chart say freshness once
      await noSidewaysScroll(page);
      await shot(page, `terminal-1440-${theme}`);
      await page.locator("[data-status='mask']").click();
      await expect(page.locator("[data-status='today']")).toContainText("₹••••");
      await expect(page.locator("[data-status='funds']")).not.toContainText("993");
      if (theme === "light") await shot(page, "terminal-1440-light-masked");
    });
  }

  test("switching layouts is remembered on the server; Save as mine; Reset", async ({ page }) => {
    const seen = await open(page, "light");
    await pickOption(page, page.locator("[data-terminal=\'layout\']"), "quick_trade");
    await expect(page.locator("[data-page='terminal-v2']")).toHaveAttribute("data-layout", "quick_trade");
    await expect(page.locator("[data-terminal-grid] [data-panel]")).toHaveCount(3);       // chain | order, the bottom panel
    await expect(page.locator("[data-panel='chart-1']")).toHaveCount(0);
    expect(seen.puts.at(-1)?.body).toMatchObject({ active: "quick_trade" });
    await shot(page, "terminal-1440-light-quick-trade");
    // TERM-2 step 2: no Multi-chart layout in the Terminal (the Charts page owns multi-chart, CT-1 C2).
    await page.locator("[data-terminal='layout']").click();
    await expect(page.getByRole("option", { name: "Multi-chart" })).toHaveCount(0);
    await page.keyboard.press("Escape");
    // His own: back to Option seller, close the basket, save it under his name.
    await pickOption(page, page.locator("[data-terminal=\'layout\']"), "option_seller");
    await page.locator("[data-panel-close='basket']").click();
    await expect(page.locator("[data-panel='basket']")).toHaveCount(0);
    await page.locator("[data-terminal='save']").click();
    await page.locator("[data-terminal='name']").fill("Expiry desk");
    await page.locator("[data-terminal='ask-ok']").click();
    await expect(page.locator("[data-note='terminal-layout'], [data-testid='terminal-layout']").first()).toContainText("Saved as");
    const saved = seen.puts.at(-1)?.body as { layouts: Record<string, { name: string; panels: { id: string; open: boolean }[] }>; active: string };
    const id = saved.active;
    expect(saved.layouts[id].name).toBe("Expiry desk");
    expect(saved.layouts[id].panels.find((p) => p.id === "basket")?.open).toBe(false);
    await expect(page.locator("[data-terminal='count']")).toHaveText("1 of 10 saved");
    // Reset brings the preset's panels back (unsaved until Save).
    await page.locator("[data-terminal='reset']").click();
    await expect(page.locator("[data-panel='basket']")).toBeVisible();
  });

  // Owner 13 Nov: a third ready layout, "Option buyer" - its basket lists the option-buying structures.
  const BUYER = ["Long call", "Long put", "Bull call spread", "Bear put spread", "Straddle", "Strangle"];
  for (const theme of THEMES) {
    test(`the Option buyer layout: its panels and the buyer strategies in the basket (${theme})`, async ({ page }) => {
      const seen = await open(page, theme);
      const layout = page.locator("[data-terminal='layout']");
      await layout.click();
      await expect(page.locator("[data-combobox-list] [role='option']").first()).toBeVisible();
      for (const name of ["Option seller", "Option buyer", "Quick trade"]) await expect(page.getByRole("option", { name })).toHaveCount(1);
      await shot(page, `buyer-after-1440-${theme}-menu`);
      await page.keyboard.press("Escape");
      // Before: the Option seller basket keeps its usual list.
      await expect(page.locator("[data-panel='basket'] [data-strategy]").first()).toHaveText("Bull put spread");
      await pickOption(page, layout, "option_buyer");
      await expect(page.locator("[data-page='terminal-v2']")).toHaveAttribute("data-layout", "option_buyer");
      expect(seen.puts.at(-1)?.body).toMatchObject({ active: "option_buyer" });
      await expect(page.locator("[data-terminal-grid] [data-panel]")).toHaveCount(4);
      for (const id of ["chart-1", "chain", "basket", "positions"]) await expect(page.locator(`[data-panel='${id}']`)).toBeVisible();
      const chips = page.locator("[data-panel='basket'] [data-strategy]");
      await expect(chips).toHaveText(BUYER);
      // One tap: the debit spread around the ATM, his own width (2 strikes) and lots (1) from his settings.
      await chips.filter({ hasText: "Bull call spread" }).click();
      await expect(page.locator("[data-panel='basket'] [data-basket-note='done']")).toContainText("Bull call spread around 24,500: 2 legs");
      await noSidewaysScroll(page);
      await settle(page);
      await shot(page, `buyer-after-1440-${theme}`);
    });
  }

  test("Option buyer: Save as mine keeps it as the layout's base; Reset goes back to it", async ({ page }) => {
    const seen = await open(page, "light", { active: "option_buyer" });
    await expect(page.locator("[data-page='terminal-v2']")).toHaveAttribute("data-layout", "option_buyer");
    await page.locator("[data-panel-close='positions']").click();
    await page.locator("[data-terminal='save']").click();
    await page.locator("[data-terminal='name']").fill("Buyer desk");
    await page.locator("[data-terminal='ask-ok']").click();
    await expect(page.locator("[data-terminal='count']")).toHaveText("1 of 10 saved");
    const saved = seen.puts.at(-1)?.body as { layouts: Record<string, { name: string; preset: string }>; active: string };
    expect(saved.layouts[saved.active]).toMatchObject({ name: "Buyer desk", preset: "option_buyer" });
    await expect(page.locator("[data-panel='basket'] [data-strategy]")).toHaveText(BUYER);   // his own layout keeps the list
    await page.locator("[data-terminal='reset']").click();
    await expect(page.locator("[data-panel='positions']")).toBeVisible();
    await expect(page.locator("[data-terminal='reset']")).toHaveAttribute("title", "Back to the Option buyer layout");
  });

  test("a panel moves with the keyboard and never onto another one", async ({ page }) => {
    await open(page, "light");
    const keys = page.locator("[data-panel-keys='positions']");
    const before = await page.locator("[data-panel='positions']").boundingBox();
    await keys.focus();
    await page.keyboard.press("ArrowDown");
    const after = await page.locator("[data-panel='positions']").boundingBox();
    expect(after!.y).toBeGreaterThan(before!.y);
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("ArrowUp");                                      // onto the chart / chain: refused
    const still = await page.locator("[data-panel='positions']").boundingBox();
    expect(still!.y).toBe(before!.y);
    await page.keyboard.press("Shift+ArrowDown");                              // taller
    const taller = await page.locator("[data-panel='positions']").boundingBox();
    expect(taller!.height).toBeGreaterThan(after!.height);
  });

  test("owner fixes T1-3 / T1-6: chain columns and one spot; positions with P&L, plain words, a group exit and Exit all", async ({ page }) => {
    await open(page, "light");
    const chain = page.locator("[data-panel='chain']");
    // TERM-2 step 2: the chain page's own chain and summary strip (one spot card; Greeks from the columns menu).
    // Owner 13 Nov: the chain at 4 of the 12 columns (the chart widest) - IV goes first, OI and OI change stay (T4-5).
    for (const col of ["call-oi", "call-oi_change", "call-ltp", "put-oi"]) await expect(chain.locator(`[data-col='${col}']`).first()).toBeVisible();
    for (const col of ["call-iv", "put-iv"]) await expect(chain.locator(`[data-col='${col}']`)).toHaveCount(0);
    await expect(chain.locator("[data-summary='spot']")).toContainText(/[\d,.]+/);
    await expect(chain.locator("[data-summary]")).not.toHaveCount(0);
    await expect(chain.locator("[data-strike-range]")).toBeVisible();
    const positions = page.locator("[data-panel='positions']");
    await expect(positions.locator("[data-col='pnl']").first()).toBeVisible();
    await expect(positions.locator("[data-col='pnl']")).toHaveCount(2);
    await expect(positions).not.toContainText("Manual fno");
    await expect(positions).not.toContainText("0.05");
    await expect(positions.locator("[data-testid='exit-group']")).toBeVisible();
    await expect(positions.locator("[data-terminal='exit-all']")).toHaveText("Exit all (2)");
    await expect(positions.locator("[data-terminal='exit-all']")).toBeEnabled();
    await shot(page, "terminal-1440-light-fixes");
  });

  test("TERM-2 step 2 (owner fix T1-4): the one basket in its panel - net, max loss, breakevens, margin; a broken leg alert", async ({ page }) => {
    await open(page, "light", { incomplete: true });
    for (const [cell, leg] of [["call-24600", "basket-buy"], ["call-24500", "basket-sell"]] as const) {
      await page.locator(`[data-cell='${cell}']`).click();
      await page.locator("[data-chain-actions] [data-action='basket']").click();
      await page.locator(`[data-chain-actions] [data-action='${leg}']`).click();
      await page.keyboard.press("Escape");                                  // the sheet stays open after "Add"; close it
      await expect(page.locator("[data-chain-actions]")).toHaveCount(0);
    }
    // TERM-2 step 2: the one basket placed in the panel - the server's figures rendered once (no spread-builder copy).
    const builder = page.locator("[data-panel='basket'] [data-basket-panel][data-embedded]");
    await expect(builder.locator("[data-basket-body]")).toBeVisible();
    await expect(builder.locator("[data-basket='legs'] > li")).toHaveCount(2);
    // M68 PR-2 (TS1-022): no lots of his own saved - each added leg asks for its lots; the numbers follow once entered
    await expect(builder.locator("[data-basket='problems']")).toContainText("Leg 1: enter its lots.");
    for (const i of [0, 1]) await builder.locator("[data-leg-lots]").nth(i).fill("1");
    for (const f of ["net", "max-loss", "breakevens", "margin"]) await expect(builder.locator(`[data-figure='${f}']`)).toBeVisible();
    await expect(page.locator("[data-panel='basket'] [data-practice-frame]")).toHaveCount(0);     // one PAPER badge per screen
    const alert = page.locator("[data-terminal='incomplete'] [data-incomplete-action]");
    await expect(alert.first()).toHaveText("Complete the leg");
    await expect(alert.nth(1)).toHaveText("Exit the filled leg");
    await shot(page, "terminal-1440-light-spread-builder");
  });

  test("a price on the chain fills the order panel; the order goes through the one ticket (PAPER)", async ({ page }) => {
    const seen = await open(page, "dark");
    // TERM-2 step 3: Quick trade is chain | order panel; a price fills the order panel there.
    await pickOption(page, page.locator("[data-terminal='layout']"), "quick_trade");
    await page.locator("[data-cell='call-24500']").click();
    await page.locator("[data-chain-actions] [data-action='buy']").click();
    const order = page.locator("[data-panel='order']");
    await expect(order).toContainText("NIFTY 03 NOV 24500 CE");
    await expect(order.locator("[data-practice-frame]")).toHaveCount(0);            // TERM-2 step 5: the one PAPER badge is in the bar
    await expect(page.locator("[data-terminal='mode']")).toHaveText("PAPER");
    // M68 PR-1: no order type is pre-selected either - pick Market where none is chosen yet
    if (await order.locator("[data-ticket='lots']").inputValue() === "") await order.locator("[data-ticket='lots']").fill("1");   // M68 PR-2: lots asked first
    if (await order.locator("[data-ticket='order-type'] [aria-checked='true']").count() === 0) await order.locator("[data-ticket='order-type'] [data-option='MARKET']").click();
    await order.locator("[data-ticket='product'] [data-option='NRML']").click();   // F1: no product saved - pick one before Review
    await order.locator("[data-ticket='review']").click();
    await expect(order.locator("[data-ticket='confirm']")).toBeVisible();
    await shot(page, "terminal-1440-dark-order-review");
    // T8: the click on Place (captured in the page) to the order request leaving the page (the fetch call is timed).
    await page.evaluate(() => {
      const w = window as unknown as { __clickAt: number; __sentAt: number };
      document.addEventListener("click", (e) => { if ((e.target as Element).closest("[data-ticket='place']")) w.__clickAt = performance.now(); }, true);
      const original = window.fetch.bind(window);
      window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        if (/\/paper\/fno\/order(\?|$)/.test(url)) w.__sentAt = performance.now();
        return original(input, init);
      };
    });
    const sent = page.waitForRequest((r) => r.url().includes("/paper/fno/order") && !r.url().includes("preview"));
    await order.locator("[data-ticket='place']").click();
    await sent;
    await expect(order.locator("[data-ticket='result']")).toBeVisible();
    expect(seen.orderPosts).toHaveLength(1);
    const ms = await page.evaluate(() => { const w = window as unknown as { __clickAt: number; __sentAt: number }; return w.__sentAt - w.__clickAt; });
    if (MEASURE) console.log(`T8 click on Place to the order request leaving the page: ${ms.toFixed(1)} ms`);
  });
});

test.describe("v2 terminal context bar (TERM-2 step 1)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("one symbol + expiry: on an expiry day the next weekly; the figures; changing the expiry re-reads the chain", async ({ page }) => {
    const asked: string[] = [];
    page.on("request", (r) => { if (/option-chain\/[^/]+\/live/.test(r.url())) asked.push(new URL(r.url()).search); });
    await open(page, "light");
    const bar = page.locator("[data-block='terminal-context']");
    await expect(bar).toHaveAttribute("data-symbol", "NIFTY");
    await expect(bar).toHaveAttribute("data-expiry", "2026-11-03");            // 27 Oct is an expiry day: the next one opens
    await expect(bar.locator("[data-figure='pcr']")).toContainText("0.91");
    await expect(bar.locator("[data-figure='dte']")).toContainText("7");
    await expect(bar.locator("[data-figure='vix']")).toContainText("13.40");
    await expect(bar.locator("[data-figure='spot'] [data-signed]")).toBeVisible();
    await expect(bar.locator("[data-terminal='mode']")).toHaveText("PAPER");
    await page.locator("[data-terminal='expiry']").click();
    await page.getByRole("option", { name: /10 Nov 2026/ }).click();
    await expect(bar).toHaveAttribute("data-expiry", "2026-11-10");
    await expect.poll(() => asked.some((q) => q.includes("2026-11-10"))).toBe(true);
    await page.locator("[data-terminal='symbol']").click();
    await page.getByRole("option", { name: "BANKNIFTY", exact: true }).click();
    await expect(bar).toHaveAttribute("data-symbol", "BANKNIFTY");
    await expect(page.locator("[data-panel='chart-1']")).toContainText("NIFTY BANK");
  });
});

test.describe("v2 terminal tablet 820 (CT-1 T3)", () => {
  test.use({ viewport: { width: 820, height: 1180 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const theme of THEMES) {
    test(`the phone tabs and no layout controls (${theme})`, async ({ page }) => {
      await open(page, theme);
      await shot(page, `terminal-820-${theme}`);
      await expect(page.locator("[data-terminal-tabs]")).toBeVisible();
      await expect(page.locator("[data-block='terminal-toolbar']")).toHaveCount(0);
      await expect(page.locator("[data-block='terminal-context']")).toBeVisible();
      await noSidewaysScroll(page);
    });
  }
});

for (const size of [{ width: 360, height: 800 }, { width: 390, height: 844 }]) {
  test.describe(`v2 terminal ${size.width} x ${size.height}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

    for (const theme of THEMES) {
      test(`tabs one at a time, the short status bar (${theme})`, async ({ page }) => {
        await open(page, theme);
        await expect(page.locator("[data-terminal-grid]")).toHaveCount(0);
        await expect(page.locator("[data-terminal-tabs] [role='tab']")).toHaveText([/^Chart$/, /^Chain$/, /^Basket$/, /^Positions\s*2$/, /^Orders$/, /^Payoff$/, /^Bots$/]);
        await expect(page.locator("[data-block='terminal-status'][data-compact]")).toBeVisible();
        await expect(page.locator("[data-status='margin']")).toHaveCount(0);
        await shot(page, `terminal-${size.width}-${theme}`);
        await page.locator("[data-tab='chain']").tap();
        await expect(page.locator("[data-phone-panel='chain'] [data-terminal-chain] [data-chain-scroll]")).toBeVisible();
        await shot(page, `terminal-${size.width}-${theme}-chain`);
        await page.locator("[data-tab='positions']").tap();
        await expect(page.locator("[data-phone-panel='positions'] [data-position]")).toHaveCount(2);
        await shot(page, `terminal-${size.width}-${theme}-positions`);
      });
    }

    // Owner 13 Nov: with the Option buyer layout in use, the phone still shows the panels as tabs; its basket lists the
    // option-buying structures.
    test(`Option buyer on the phone: the same tabs; the basket lists the buyer strategies (dark)`, async ({ page }) => {
      await open(page, "dark", { active: "option_buyer" });
      await expect(page.locator("[data-terminal-grid]")).toHaveCount(0);
      await expect(page.locator("[data-terminal-tabs] [role='tab']")).toHaveText([/^Chart$/, /^Chain$/, /^Basket$/, /^Positions\s*2$/, /^Orders$/, /^Payoff$/, /^Bots$/]);
      await page.locator("[data-tab='basket']").tap();
      await expect(page.locator("[data-phone-panel='basket'] [data-strategy]")).toHaveText(["Long call", "Long put", "Bull call spread", "Bear put spread", "Straddle", "Strangle"]);
      await settle(page);
      await expectPhoneLayout(page);
      await shot(page, `buyer-after-${size.width}-dark-basket`);
    });

    test("no sideways scroll and 44 px targets on every tab; a chain price opens the one ticket as a sheet", async ({ page }) => {
      await open(page, "light");
      for (const tab of ["chart", "chain", "basket", "positions", "orders", "payoff", "bots"]) {     // TERM-2 step 3 tabs + M63 I-5
        await page.locator(`[data-tab='${tab}']`).tap();
        await expect(page.locator(`[data-phone-panel='${tab}']`)).toBeVisible();
        await settle(page);
        await expectPhoneLayout(page);
      }
      await page.locator("[data-tab='chain']").tap();
      await page.locator("[data-cell='put-24500']").tap();
      await page.locator("[data-chain-actions] [data-action='sell']").tap();
      // TERM-2 step 3: the one order ticket opens as a bottom sheet (no Order tab of the Terminal's own).
      const ticket = page.locator("[data-order-ticket]");
      await expect(ticket.locator("[data-ticket='review']")).toHaveText("Review Sell order");
      await expect(ticket.getByText("Keys: B for Buy")).toBeHidden();             // a desktop keyboard hint only
      const review = await ticket.locator("[data-ticket='review']").boundingBox();
      expect(review && review.height >= 44).toBe(true);
      await shot(page, `terminal-${size.width}-light-order`);
      await page.keyboard.press("Escape");
      await expect(page.locator("[data-order-ticket]")).toHaveCount(0);
      // T1-3: LTP + OI per side on a phone; a strike opens the rest.
      await page.locator("[data-tab='chain']").tap();
      await expect(page.locator("[data-phone-panel='chain'] [data-col='call-iv']").first()).toBeHidden();
      await page.locator("[data-phone-panel='chain'] tr[data-atm] th").tap();
      await expect(page.locator("[data-row-details]")).toBeVisible();
      await noSidewaysScroll(page);
      await shot(page, `terminal-${size.width}-light-chain-expanded`);
    });

  });
}

test.describe("v2 terminal with the flag off", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("no navigation entry; the address says Coming soon", async ({ page }) => {
    await open(page, "light", { features: { ui_v2: true, paper_account: true } });
    await expect(page.getByText("Coming soon").first()).toBeVisible();
    await expect(page.locator("nav a[href='/terminal']")).toHaveCount(0);
    await expect(page.locator("[data-page='terminal-v2']")).toHaveCount(0);
    await shot(page, "terminal-1440-light-flag-off");
  });
});

test.describe("T8 measurements", () => {
  test.skip(!MEASURE, "TERMINAL_MEASURE=1 only");
  test.use({ viewport: { width: 1440, height: 900 } });

  test("layout shift while the panels update, chart pan with 5,000 bars", async ({ page }) => {
    await open(page, "light", { bars: 5000, vary: true });
    // Layout shift: from the first paint, through four status reads with new figures and the list reads coming back.
    await page.clock.runFor(61_000);
    await page.waitForTimeout(1500);
    const cls = await page.evaluate(() => (window as unknown as { __cls: number }).__cls);
    // Chart pan: drag across the first chart's canvas for about a second and count animation frames.
    const canvas = page.locator("[data-panel='chart-1'] canvas").first();
    const box = await canvas.boundingBox();
    const frames = await page.evaluate(() => new Promise<number>((resolve) => {
      let n = 0;
      const t0 = performance.now();
      const tick = () => { n += 1; if (performance.now() - t0 < 1000) requestAnimationFrame(tick); else resolve(n); };
      requestAnimationFrame(tick);
    }).then((n) => n));
    const idle = frames;
    const drag = (async () => {
      await page.mouse.move(box!.x + box!.width * 0.8, box!.y + box!.height / 2);
      await page.mouse.down();
      for (let i = 0; i < 60; i += 1) await page.mouse.move(box!.x + box!.width * (0.8 - i * 0.01), box!.y + box!.height / 2);
      await page.mouse.up();
    })();
    const during = await page.evaluate(() => new Promise<number>((resolve) => {
      let n = 0;
      const t0 = performance.now();
      const tick = () => { n += 1; if (performance.now() - t0 < 1000) requestAnimationFrame(tick); else resolve(n); };
      requestAnimationFrame(tick);
    }));
    await drag;
    console.log(`T8 layout shift (CLS from the first paint, 61 s of polled reads with changing figures): ${cls.toFixed(4)}`);
    console.log(`T8 frames per second, idle: ${idle}; while panning 5,000 bars: ${during}`);
    expect(cls).toBeLessThan(0.1);
  });
});

/** UX-11 PR 4 (owner, 27 Oct): numbers first - every Terminal panel passes the text budget (one short visible line,
 * the rest in Details); the visible-word count is printed for the comparison page. */
for (const size of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test.describe(`v2 terminal text budget ${size.width}`, () => {
    test.use({ viewport: size });
    test("every panel fits the text budget", async ({ page }) => {
      await open(page, "light");
      await settle(page);
      console.log(`[words] Terminal ${size.width}: ${await visibleWords(page)}`);
      console.log(`[cards] ${JSON.stringify(await readCards(page))}`);
      // Owner 9 Nov: the chain panel IS the Option chain page (its own budget test: option-chain-v2.e2e.ts "every card fits
      // the text budget"); its analyses read the same here, so the panel is measured there, not twice.
      expect((await textBudgetProblems(page)).filter((p) => !p.startsWith("Option chain:"))).toEqual([]);
    });
  });
}

// --- TERM-2 step 4: R1 Roll, R2 What-if, R3 short strike to area, R4 margin pair (each behind its own flag) -------------

const R_FLAGS = { term_roll: true, term_whatif: true, term_strike_area: true, term_margin_pair: true };
const R_ON = { ...ON, ...R_FLAGS };
const PRICES = Array.from({ length: 121 }, (_, i) => 23500 + i * 20);
type AskLeg = { strike: number; right: string; side: string; lots: number; price: number | null };
const OPEN_LEGS: AskLeg[] = [{ strike: 25000, right: "CE", side: "SELL", lots: 1, price: 120 }, { strike: 25100, right: "CE", side: "BUY", lots: 1, price: 80 }];
/** A made-up payoff at expiry of these legs (75 a lot) - the shape only, for the screenshots. */
function payoffOf(legs: AskLeg[]) {
  const at = PRICES.map((s) => legs.reduce((sum, l) => {
    const intrinsic = l.right === "CE" ? Math.max(s - l.strike, 0) : Math.max(l.strike - s, 0);
    const unit = (l.price ?? 0) - intrinsic;
    return sum + (l.side === "SELL" ? unit : -unit) * l.lots * 75;
  }, 0));
  const net = legs.reduce((sum, l) => sum + (l.side === "SELL" ? 1 : -1) * (l.price ?? 0) * l.lots * 75, 0);
  const crossings = PRICES.filter((_, i) => i > 0 && Math.sign(at[i]) !== Math.sign(at[i - 1]));
  return { ...EVALUATION, prices: PRICES, at_expiry: at, today: at, on_date: at, net_premium: net, breakevens: crossings,
    extremes: { max_profit: Math.max(...at), max_loss: Math.min(...at), unbounded_profit: false, unbounded_loss: false } };
}
const ROLL = {
  mode: "PAPER", ok: true, errors: [], expiry: "2026-11-10", underlying: "NIFTY", spot: 24480,
  exit: [{ trade_id: 41, label: "NIFTY 03 NOV 25000 CE", side: "SELL", lots: 1, units: 75, price: 110, price_source: "live" },
    { trade_id: 42, label: "NIFTY 03 NOV 25100 CE", side: "BUY", lots: 1, units: 75, price: 70, price_source: "live" }],
  legs: [{ underlying: "NIFTY", expiry: "2026-11-10", strike: 25000, right: "CE", side: "SELL", lots: 1, price: 165, stop_loss: null, label: "NIFTY 10 NOV 25000 CE" },
    { underlying: "NIFTY", expiry: "2026-11-10", strike: 25100, right: "CE", side: "BUY", lots: 1, price: 120, stop_loss: null, label: "NIFTY 10 NOV 25100 CE" }],
  numbers: { net_premium: 3375, extremes: { max_profit: 3375, max_loss: -4125, unbounded_profit: false, unbounded_loss: false }, breakevens: [25045], lot_sizes: [75, 75] },
  numbers_errors: [], exit_value: -3000, net: 375, atomic: false,
  steps: "Step 1 closes these legs through Exit now. Step 2 puts the new legs in your basket: you review and place them. Between the two steps these strikes are not held.",
  as_of: NOW,
};
const MY_DAY = {
  as_of: NOW, today: "2026-10-27", previous_session: "2026-10-24", note: "", thresholds: { watch_room_left_pct: 50, near_room_left_pct: 20 },
  positions: [{ id: 41, symbol: POSITIONS[0].symbol, mode: "PAPER", side: "SHORT", kind: "OPTION", underlying: "NIFTY", deployment_id: null,
    chip: null, chip_label: null, room_left_pct: null, chip_text: null, expiry: "2026-11-03", expiry_sessions: 5, expiry_text: "5 sessions",
    area: { low: 25080, high: 25140, state: "fresh", where: "above", distance_pct: 0.32, range_text: "25,080.00-25,140.00", from: "strike", change: null },
    area_text: "Nearest area is 0.32% above your strike.", strike: 25000, right: "CE" }],
  since_last_session: { gaps: [], events: [], areas: [], empty: "Nothing new on the record since the last session." },
};
const BASKET = [
  { id: "b1", underlying: "NIFTY", expiry: "2026-11-03", strike: 24500, right: "CE", side: "SELL", lots: 1, price: 150, label: "NIFTY 03 NOV 24500 CE", stopLoss: null, iv: 13 },
  { id: "b2", underlying: "NIFTY", expiry: "2026-11-03", strike: 24600, right: "CE", side: "BUY", lots: 1, price: 100, label: "NIFTY 03 NOV 24600 CE", stopLoss: null, iv: 13 },
];

interface RSeen { evaluates: Record<string, unknown>[]; exits: string[]; places: string[] }
/** The four features' own answers (registered last, so they win), and what the page sent. */
function termRoutes(seen: RSeen) {
  return async (page: Page) => {
    await page.route(/\/api(?:\/v1)?\/today\/my-day$/, (r) => r.fulfill({ json: MY_DAY }));
    await page.route(/\/api(?:\/v1)?\/paper\/fno\/roll\/preview$/, (r) => r.fulfill({ json: ROLL }));
    await page.route(/\/api(?:\/v1)?\/paper\/fno\/positions\/\d+\/exit-group$/, (r) => {
      seen.exits.push(new URL(r.request().url()).pathname);
      return r.fulfill({ json: { status: "CLOSED", legs: [
        { trade_id: 41, symbol: POSITIONS[0].symbol, closed: true, exit_price: 110, pnl: 750, warnings: [] },
        { trade_id: 42, symbol: POSITIONS[1].symbol, closed: true, exit_price: 70, pnl: -750, warnings: [] }] } });
    });
    await page.route(/\/api(?:\/v1)?\/paper\/fno\/basket\/(execute|preview)$/, (r) => { seen.places.push(r.request().url()); return r.fallback(); });
    await page.route(/\/api(?:\/v1)?\/paper\/fno\/basket\/evaluate$/, (r) => {
      const body = r.request().postDataJSON() as { legs: AskLeg[]; with_open?: boolean; margin_pair?: boolean };
      seen.evaluates.push(body as unknown as Record<string, unknown>);
      const out: Record<string, unknown> = { ...payoffOf(body.legs), lot_sizes: body.legs.map(() => 75), labels: [], order: body.legs.map((_, i) => i) };
      if (body.with_open) {
        out.whatif = { ...payoffOf([...OPEN_LEGS, ...body.legs]), open: [
          { trade_id: 41, label: "NIFTY 03 NOV 25000 CE", side: "SELL", lots: 1, price: 120 }, { trade_id: 42, label: "NIFTY 03 NOV 25100 CE", side: "BUY", lots: 1, price: 80 }],
        open_legs: OPEN_LEGS.map((l, i) => ({ id: `open-${41 + i}`, direction: l.side, option_type: l.right, strike: l.strike, premium: l.price, lots: 1, lot_size: 75, expiry: "2026-11-03", iv: null })) };
      }
      if (body.margin_pair) {
        const lots = body.legs.reduce((a, l) => a + l.lots, 0);
        const required = 16_000 * lots;
        out.margin_pair = { source: "estimate", estimate: true, label: "ATP's estimate, not your broker's number", required, without_hedge: null, benefit: null,
          available: STATUS.funds.available, available_note: STATUS.funds.note, free_after: STATUS.funds.available - required, free_before: STATUS.funds.available,
          free_source: "practice", notes: [] };
      }
      return r.fulfill({ json: out });
    });
  };
}

test.describe("v2 terminal TERM-2 step 4 (R1-R4) 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`before: no Roll, no what-if, no margin pair, no area mark (${theme})`, async ({ page }) => {
      const seen: RSeen = { evaluates: [], exits: [], places: [] };
      await open(page, theme, { basket: BASKET, routes: termRoutes(seen) });
      await expect(page.locator("[data-testid='roll-group'], [data-testid='roll']")).toHaveCount(0);
      await expect(page.locator("[data-basket='whatif-toggle']")).toHaveCount(0);
      await expect(page.locator("[data-figure='margin-required']")).toHaveCount(0);
      await expect(page.locator("[data-area-mark]")).toHaveCount(0);
      expect(seen.evaluates.every((b) => !b.with_open && !b.margin_pair)).toBe(true);
      await shot(page, `term2r-before-1440-${theme}`);
      await page.locator("[data-panel='basket'] [data-basket='numbers']").scrollIntoViewIfNeeded();
      await shot(page, `term2r-before-1440-${theme}-basket`);
    });

    test(`after: Roll -> the preview -> the existing group exit -> the new legs in the basket (${theme})`, async ({ page }) => {
      const seen: RSeen = { evaluates: [], exits: [], places: [] };
      await open(page, theme, { features: R_ON, routes: termRoutes(seen) });
      const positions = page.locator("[data-panel='positions']");
      // R3: the short strike's distance to the nearest area, on its row and on the chain.
      await expect(positions.locator("[data-chip='strike-area']")).toHaveText("Area 0.32% above · 25,080.00-25,140.00");
      await expect(page.locator("[data-terminal-chain] [data-area-side='call']")).toHaveText(/Area 0\.32% above/);
      // Exits are never hidden or disabled beside Roll (ADR-0004).
      await expect(positions.locator("[data-testid='exit-group']")).toBeEnabled();
      await positions.locator("[data-testid='roll-group']").click();
      const sheet = page.getByRole("dialog");
      await expect(sheet.locator("[data-roll-preview='ok']")).toBeVisible();
      await expect(sheet.locator("[data-roll='not-atomic']")).toHaveText("Two steps, not one: you hold neither set of legs between them.");
      await expect(sheet.locator("[data-roll='exit'] li")).toHaveCount(2);
      await expect(sheet.locator("[data-roll='legs'] li")).toHaveCount(2);
      await expect(sheet.locator("[data-figure='net']")).toContainText("Net credit ₹375");
      await expect(sheet.locator("[data-testid='roll-confirm']")).toBeEnabled();
      await shot(page, `term2r-after-1440-${theme}-roll-preview`);
      await sheet.locator("[data-testid='roll-confirm']").click();
      await expect(page.locator("[data-roll-pending]")).toBeVisible();
      expect(seen.exits).toHaveLength(1);
      expect(seen.exits[0]).toMatch(/\/paper\/fno\/positions\/41\/exit-group$/);   // the existing group exit
      expect(seen.places).toEqual([]);                                               // nothing placed: the user reviews the basket
      const basket = page.locator("[data-panel='basket'] [data-basket-panel]");
      await expect(basket.locator("[data-basket='legs'] > li")).toHaveCount(2);
      await expect(basket.locator("[data-basket='legs']")).toContainText("10 NOV");
      await shot(page, `term2r-after-1440-${theme}-roll-pending`);
    });

    test(`after: the basket with what-if and the margin pair, which follows every lot change (${theme})`, async ({ page }) => {
      const seen: RSeen = { evaluates: [], exits: [], places: [] };
      await open(page, theme, { features: R_ON, basket: BASKET, routes: termRoutes(seen) });
      const basket = page.locator("[data-panel='basket'] [data-basket-panel]");
      await expect(basket.locator("[data-basket='whatif-toggle']")).toBeVisible();
      await expect(basket.locator("[data-basket='payoff']")).toHaveAttribute("data-payoff", "combined");
      await expect(basket.locator("[data-figure='whatif-max-loss']")).toBeVisible();
      const required = basket.locator("[data-figure='margin-required']");
      await expect(required).toContainText("Margin required (estimate)");
      await expect(required).toContainText("32,000");
      await expect(basket.locator("[data-figure='margin-available']")).toContainText("9,93,300");
      await basket.locator("[data-basket='payoff']").scrollIntoViewIfNeeded();
      await shot(page, `term2r-after-1440-${theme}-basket`);
      await required.scrollIntoViewIfNeeded();
      await shot(page, `term2r-after-1440-${theme}-basket-margin`);
      await basket.locator("[data-leg-more]").first().click();
      await expect(required).toContainText("48,000");
      expect(seen.evaluates.at(-1)).toMatchObject({ with_open: true, margin_pair: true });
      await basket.locator("[data-whatif-view='basket']").click();
      await expect(basket.locator("[data-basket='payoff']")).toHaveAttribute("data-payoff", "basket");
      await noSidewaysScroll(page);
    });
  }
});

test.describe("v2 terminal TERM-2 step 4 (R1-R4) tablet 820", () => {
  test.use({ viewport: { width: 820, height: 1180 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const [when, features] of [["before", ON], ["after", R_ON]] as const) {
    test(`${when}: positions and the basket (light)`, async ({ page }) => {
      const seen: RSeen = { evaluates: [], exits: [], places: [] };
      await open(page, "light", { features, basket: BASKET, routes: termRoutes(seen) });
      await page.locator("[data-tab='positions']").tap();
      await expect(page.locator("[data-phone-panel='positions'] [data-position]")).toHaveCount(2);
      await shot(page, `term2r-${when}-820-light-positions`);
      await page.locator("[data-tab='basket']").tap();
      await expect(page.locator("[data-phone-panel='basket'] [data-basket='payoff']")).toBeVisible();
      await settle(page);
      await shot(page, `term2r-${when}-820-light-basket`);
      await noSidewaysScroll(page);
    });
  }
});

test.describe("v2 terminal TERM-2 step 4 (R1-R4) 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const theme of THEMES) {
    for (const [when, features] of [["before", ON], ["after", R_ON]] as const) {
      test(`${when}: positions, the roll preview, the chain and the basket (${theme})`, async ({ page }) => {
        const seen: RSeen = { evaluates: [], exits: [], places: [] };
        await open(page, theme, { features, basket: BASKET, routes: termRoutes(seen) });
        await page.locator("[data-tab='positions']").tap();
        const positions = page.locator("[data-phone-panel='positions']");
        await expect(positions.locator("[data-position]")).toHaveCount(2);
        await shot(page, `term2r-${when}-390-${theme}-positions`);
        if (when === "after") {
          await expect(positions.locator("[data-chip='strike-area']")).toHaveText("Area 0.32% above · 25,080.00-25,140.00");
          const roll = await positions.locator("[data-testid='roll-group']").boundingBox();
          expect(roll && roll.height >= 44).toBe(true);
          await positions.locator("[data-testid='roll-group']").tap();
          await expect(page.getByRole("dialog").locator("[data-roll-preview='ok']")).toBeVisible();
          await shot(page, `term2r-after-390-${theme}-roll-preview`);
          await page.keyboard.press("Escape");
        } else {
          await expect(positions.locator("[data-testid='roll-group']")).toHaveCount(0);
        }
        await page.locator("[data-tab='chain']").tap();
        await expect(page.locator("[data-phone-panel='chain'] [data-terminal-chain] [data-chain-scroll]")).toBeVisible();
        await page.locator("[data-phone-panel='chain'] tr[data-strike='25000']").scrollIntoViewIfNeeded();
        await shot(page, `term2r-${when}-390-${theme}-chain`);
        await page.locator("[data-tab='basket']").tap();
        await expect(page.locator("[data-phone-panel='basket'] [data-basket='payoff']")).toBeVisible();
        await settle(page);
        await shot(page, `term2r-${when}-390-${theme}-basket`);
        await noSidewaysScroll(page);           // the 44 px rule for every tab: the test above (no screenshots taken before it)
      });
    }
  }
});

// --- M63 I-5: the Bots & signals and Payoff panels (read only) --------------------------------------------------------

const FACT = (p: (typeof POSITIONS)[number], strike: number) => ({ symbol: p.symbol, exchange: "NFO", known: true, listed: true, underlying: "NIFTY",
  right: "CE", strike, expiry: "2026-11-03", lot_size: 75, label: `NIFTY 03 NOV ${strike} CE`, expired: false, days_left: 5,
  spot_symbol: "NIFTY 50", spot_exchange: "NSE", roll: null, roll_note: null });
const BOTS = [
  { id: 7, strategy_id: "orb_15m_5m", symbol: "NIFTY 50", instrument_kind: "OPTION", holding: "INTRADAY", mode: "PAPER", status: "ACTIVE", pause_reason: null,
    last_evaluated_at: NOW, last_signal_at: "2026-10-27T04:33:00Z", last_error: null, consecutive_failures: 0, open_positions: 0, wait_reason: null },
  { id: 6, strategy_id: "swing_breakout_d", symbol: "RELIANCE", instrument_kind: "EQUITY", holding: "SWING", mode: "PAPER", status: "PAUSED", pause_reason: null,
    last_evaluated_at: NOW, last_signal_at: null, last_error: null, consecutive_failures: 0, open_positions: 0, wait_reason: null },
];
const WAITING = [
  { id: "action:31", at: "2026-10-27T04:40:00Z", symbol: "NIFTY 50", exchange: "NSE", direction: "SHORT", setup: "Rejection at the seller area",
    entry: 24480, stop: 24560, target: 24300, rr: 2.2, source: { kind: "autopilot", name: "Deployment #7" }, mode: "PAPER", status: "waiting",
    expires_at: "2026-10-27T05:05:00Z", snoozed_until: null, reasons: [], action_id: 31, outcome: null },
];
/** His bots, his waiting signals and what his two open legs are (so they are drawn on the payoff). */
async function m63Routes(page: Page) {
  await page.route(/\/api(?:\/v1)?\/deployments(\?.*)?$/, (r) => r.fulfill({ json: BOTS }));
  await page.route(/\/api(?:\/v1)?\/signals\/feed\?/, (r) => r.fulfill({ json: { tab: "waiting", source: "all", symbol: null, items: WAITING, next_cursor: null,
    counts: { waiting: 1, today: 1 }, as_of: NOW, since: null, sources: {} } }));
  await page.route(/\/api(?:\/v1)?\/paper\/fno\/contracts$/, (r) => r.fulfill({ json: { items: [FACT(POSITIONS[0], 25000), FACT(POSITIONS[1], 25100)] } }));
}

/** The payoff and bots panels' content (the same on the grid and on a phone tab); nothing in them places an order. */
async function expectPayoff(payoff: ReturnType<Page["locator"]>) {
  await expect(payoff.locator("[data-compare-chart] [data-curve]")).toHaveCount(2);          // his positions and his basket
  await expect(payoff.locator("[data-compare-figures] thead th")).toHaveText(["Figure", "Your positions", "Your basket"]);
  await expect(payoff.locator("[data-figure-row='Max profit'] td").first()).toHaveText("₹3,000.00");
  await expect(payoff.locator("[data-figure-row='Max loss'] td").first()).toHaveText("₹4,500.00");
  await expect(payoff.locator("[data-figure-row='Breakevens'] td").first()).toHaveText("25,040");
  await expect(payoff.locator("[data-figure-row='Margin']")).toHaveCount(0);
  await expect(payoff.locator("[data-payoff='expiry']")).toHaveText("At expiry 3 Nov");
  await expect(payoff.locator("[data-panel-body='payoff'] button")).toHaveCount(0);
}

async function expectBots(bots: ReturnType<Page["locator"]>) {
  await expect(bots.locator("[data-bot]")).toHaveCount(2);
  await expect(bots.locator("[data-bot='7']")).toContainText("Opening Range Breakout");
  await expect(bots.locator("[data-bot='7'] [data-chip='bot-status']")).toHaveText("Running");
  await expect(bots.locator("[data-bot='6'] [data-chip='bot-status']")).toHaveText("Paused");
  await expect(bots.locator("[data-signal='action:31']")).toContainText("Rejection at the seller area");
  await expect(bots.locator("[data-signal='action:31'] a")).toHaveAttribute("href", "/signals?proposal=31");
  await expect(bots).not.toContainText(/24,?480|24,?560|24,?300|Deployment #7/);              // no price, no internal name
  await expect(bots.locator("[data-panel-body='bots'] button")).toHaveCount(0);
  await expect(bots.getByRole("link", { name: "Open Signals" })).toBeVisible();
  await expect(bots.getByRole("link", { name: "Open Trading bot" })).toBeVisible();
}

test.describe("v2 terminal M63 I-5 panels 1440", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`Bots & signals and Payoff from the Panels menu: read only, his own (${theme})`, async ({ page }) => {
      await open(page, theme, { basket: BASKET, routes: m63Routes });
      await page.locator("[data-terminal='panels'] summary").click();
      for (const id of ["payoff", "bots"]) await page.locator(`[data-panel-toggle='${id}']`).check();
      await page.locator("[data-terminal='panels'] summary").click();
      await settle(page);
      const payoff = page.locator("[data-panel='payoff']");
      const bots = page.locator("[data-panel='bots']");
      await expectPayoff(payoff);
      await expectBots(bots);
      // M63 review: no empty band - both sit directly under Positions & orders, at most one grid gutter below it.
      const gutter = await page.locator("[data-terminal-grid]").evaluate((g) => parseFloat(getComputedStyle(g).rowGap) || 0);
      const above = await page.locator("[data-panel='positions']").boundingBox();
      for (const p of [payoff, bots]) {
        const box = await p.boundingBox();
        expect(above && box).toBeTruthy();
        const gap = (box?.y ?? 0) - ((above?.y ?? 0) + (above?.height ?? 0));
        expect(gap).toBeGreaterThanOrEqual(0);
        expect(gap).toBeLessThanOrEqual(gutter + 1);
      }
      // The two new panels fit the text budget. (The Basket panel with two legs reads three visible lines today - not
      // part of this item; the budget test above checks the empty default layout.)
      expect((await textBudgetProblems(page)).filter((p) => !p.startsWith("Basket:") && !p.startsWith("Option chain:"))).toEqual([]);   // chain: the Option chain page's own budget test
      await noSidewaysScroll(page);
      await payoff.scrollIntoViewIfNeeded();
      await shot(page, `m63-after-1440-${theme}`);
    });
  }
});

test.describe("v2 terminal M63 I-5 panels 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const theme of THEMES) {
    test(`the Payoff and Bots tabs, last: read only, his own (${theme})`, async ({ page }) => {
      await open(page, theme, { basket: BASKET, routes: m63Routes });
      await expect(page.locator("[data-terminal-tabs] [role='tab']")).toHaveText([/^Chart$/, /^Chain$/, /^Basket\s*2$/, /^Positions\s*2$/, /^Orders$/, /^Payoff$/, /^Bots$/]);
      await page.locator("[data-tab='payoff']").tap();
      await settle(page);
      await expectPayoff(page.locator("[data-phone-panel='payoff']"));
      expect(await textBudgetProblems(page)).toEqual([]);
      await expectPhoneLayout(page);
      await shot(page, `m63-after-390-${theme}-payoff`);
      await page.locator("[data-tab='bots']").tap();
      await settle(page);
      await expectBots(page.locator("[data-phone-panel='bots']"));
      expect(await textBudgetProblems(page)).toEqual([]);
      await expectPhoneLayout(page);
      await shot(page, `m63-after-390-${theme}-bots`);
    });
  }
});

// M58 (T4): the lines between panels grow one panel and shrink its neighbour; the limit shows inline; the Panels dialog's
// width / height steppers (saved with "Save as mine"); double-click a line = the ready sizes, a title = maximise.
for (const size of [{ width: 1280, height: 800 }, { width: 1920, height: 1080 }]) {
  test.describe(`v2 terminal gutters ${size.width} (M58)`, () => {
    test.use({ viewport: size });
    const box = async (page: Page, id: string) => (await page.locator(`[data-panel='${id}']`).boundingBox())!;
    const line = (page: Page, ids: string) => page.locator(`[data-gutter-ids='${ids}']`);
    // Mid-gesture and open-menu shots: the visible screen only (a full-page shot resizes the page under the pointer).
    const shotView = async (page: Page, name: string) => {
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, animations: "disabled" });
    };

    test("M58 proof: the default layout", async ({ page }) => {
      await open(page, "light");
      await settle(page);
      await shot(page, `m58-${size.width}-default`);
      await page.locator("[data-terminal='panels'] summary").click();
      await shotView(page, `m58-${size.width}-panels`);
    });

    test("dragging the line between Chart and Option chain grows one and shrinks the other; the limit shows", async ({ page }) => {
      await open(page, "light");
      await settle(page);
      const gutter = line(page, "chart-1|chain");
      await expect(gutter).toHaveAttribute("aria-label", "Resize Chart and Option chain");
      await expect(gutter).toHaveCSS("cursor", "col-resize");
      const g = (await gutter.boundingBox())!;
      expect(g.width).toBeGreaterThanOrEqual(6);
      expect(g.width).toBeLessThanOrEqual(8);
      const chart0 = await box(page, "chart-1"), chain0 = await box(page, "chain");
      expect(g.height).toBeGreaterThanOrEqual(chart0.height - 1);                 // full length
      // The horizontal line under the top row: the top panels, Positions & orders and the basket share it.
      await expect(line(page, "chart-1|chain|positions|basket")).toHaveCSS("cursor", "row-resize");
      const col = (chain0.x + chain0.width - chart0.x + 8) / 12;                  // 7 + 5 columns (UX-3 off here: the chart has the watchlist's 2)
      await gutter.hover();
      await shotView(page, `m58-${size.width}-hover`);
      await page.mouse.down();
      await page.mouse.move(g.x + g.width / 2 + col * 1.1, g.y + g.height / 2, { steps: 6 });
      await page.mouse.up();
      const chart1 = await box(page, "chart-1"), chain1 = await box(page, "chain");
      expect(chart1.width).toBeGreaterThan(chart0.width + col / 2);
      expect(chain1.width).toBeLessThan(chain0.width - col / 2);
      expect(chain1.x).toBeGreaterThan(chain0.x + col / 2);
      // Further than the chain's smallest width: it stops at 3 columns and says so (the line turns amber).
      const g2 = (await gutter.boundingBox())!;
      await page.mouse.move(g2.x + g2.width / 2, g2.y + g2.height / 2);
      await page.mouse.down();
      await page.mouse.move(g2.x + col * 4, g2.y + g2.height / 2, { steps: 6 });
      await expect(page.locator("[data-terminal-limit]")).toContainText("Option chain cannot be narrower than 3 columns.");
      await expect(gutter).toHaveAttribute("data-limit", "true");
      await shotView(page, `m58-${size.width}-limit`);
      await page.mouse.up();
      const chain2 = await box(page, "chain");
      expect(chain2.width).toBeGreaterThan(col * 2.5);
      expect(chain2.width).toBeLessThan(col * 3.5);
      // Double-click the line: Chart and Option chain go back to the ready sizes.
      await gutter.dblclick();
      await expect.poll(async () => Math.round((await box(page, "chart-1")).width)).toBe(Math.round(chart0.width));
      await expect.poll(async () => Math.round((await box(page, "chain")).width)).toBe(Math.round(chain0.width));
    });

    test("the keyboard moves the line; a title double-click maximises and restores", async ({ page }) => {
      await open(page, "light");
      await settle(page);
      const chart0 = await box(page, "chart-1");
      await line(page, "chart-1|chain").focus();
      await page.keyboard.press("ArrowRight");
      await expect.poll(async () => (await box(page, "chart-1")).width).toBeGreaterThan(chart0.width);
      await page.keyboard.press("ArrowLeft");
      await expect.poll(async () => Math.round((await box(page, "chart-1")).width)).toBe(Math.round(chart0.width));
      await page.locator("[data-panel-handle='chain']").dblclick();
      await expect(page.locator("[data-panel='chain']")).toHaveAttribute("data-maximised", "true");
      for (const id of ["chart-1", "basket", "positions"]) await expect(page.locator(`[data-panel='${id}']`)).toHaveAttribute("data-collapsed", "true");
      expect((await box(page, "chain")).width).toBeGreaterThan(chart0.width * 1.5);
      await expect(page.locator("[data-gutter-ids]")).toHaveCount(0);
      await shot(page, `m58-${size.width}-maximised`);
      await page.locator("[data-panel-handle='chain']").dblclick();
      await expect(page.locator("[data-panel='chain']")).not.toHaveAttribute("data-maximised", "true");
      await expect.poll(async () => Math.round((await box(page, "chart-1")).width)).toBe(Math.round(chart0.width));
    });

    test("the Panels dialog: width and height steppers, Fit to screen, Equal widths; saved with Save as mine", async ({ page }) => {
      const seen = await open(page, "light");
      await settle(page);
      await page.locator("[data-terminal='panels'] summary").click();
      const width = page.locator("[data-size='chart-1:w']");
      await expect(width).toHaveText("8");                                              // owner 13 Nov: Chart 8 · Chain 4 (no watchlist)
      await expect(page.locator("[data-size-min='chart-1:w']")).toContainText("min 3");
      await page.locator("[data-size-step='chart-1:w:+1']").click();
      await expect(width).toHaveText("9");
      await expect(page.locator("[data-size='chain:w']")).toHaveText("3");             // the neighbour gave a column: its smallest
      await shotView(page, `m58-${size.width}-dialog`);
      await expect(page.locator("[data-size-step='chain:w:-1']")).toBeDisabled();
      await page.locator("[data-terminal='equal-widths']").click();
      // UX-3 + TA-4: two rows - Chart | Option chain, and Positions & orders | Basket - each shares the 12 columns.
      for (const id of ["chart-1", "chain", "positions", "basket"]) await expect(page.locator(`[data-size='${id}:w']`)).toHaveText("6");
      await page.locator("[data-terminal='fit']").click();
      await expect(page.locator("[data-size='positions:w']")).toHaveText("6");
      await page.locator("[data-size-step='chart-1:h:+1']").click();
      await page.locator("[data-size-step='chart-1:w:+1']").click();
      await expect(width).toHaveText("7");
      const height = await page.locator("[data-size='chart-1:h']").textContent();
      await page.locator("[data-terminal='panels'] summary").click();
      await page.locator("[data-terminal='save']").click();
      await page.locator("[data-terminal='name']").fill("Wide chart");
      await page.locator("[data-terminal='ask-ok']").click();
      await expect(page.locator("[data-note='terminal-layout'], [data-testid='terminal-layout']").first()).toContainText("Saved as");
      const saved = seen.puts.at(-1)?.body as { layouts: Record<string, { panels: { id: string; w: number }[] }>; active: string };
      expect(saved.layouts[saved.active].panels.find((p) => p.id === "chart-1")?.w).toBe(7);
      await page.reload();
      await settle(page);
      await page.locator("[data-terminal='panels'] summary").click();
      await expect(page.locator("[data-size='chart-1:w']")).toHaveText("7");
      await expect(page.locator("[data-size='chart-1:h']")).toHaveText(height ?? "");
    });
  });
}

// M75 NAV-3 (docs/design/M75_NO_NAVIGATION.md, the Terminal / charts rows): the Terminal's chart and chain links open in
// place - the PATH stays /terminal while the panel is open, and closing it returns to the Terminal as it was.
// (The bots panel's "Open Signals" / "Open Trading bot" links: src/inplace/terminalChartsNav.test.tsx; its content: the M63 I-5 tests above.)
for (const size of [{ width: 390, height: 844, shape: "bottom", phone: true }, { width: 1440, height: 900, shape: "right", phone: false }] as const) {
  test.describe(`v2 terminal ${size.width}: links open in place (M75 NAV-3)`, () => {
    test.use(size.phone ? { viewport: { width: size.width, height: size.height }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 }
      : { viewport: { width: size.width, height: size.height }, deviceScaleFactor: 1 });

    test("the chart's Open a larger chart: the chart beside the Terminal; closing keeps the layout", async ({ page }) => {
      await open(page, "light");
      const host = size.phone ? page.locator("[data-phone-panel='chart']") : page.locator("[data-panel='chart-1']");
      const before = size.phone ? null : await page.locator("[data-page='terminal-v2']").getAttribute("data-layout");
      await host.locator("a[data-action='open']").first().click();
      const chart = page.locator("[data-inplace-panel='chart']");
      await expect(chart.locator("[data-embedded-chart]")).toBeVisible();
      await expect(chart).toHaveAttribute("data-inplace-shape", size.shape);
      await expect(chart.locator("a[data-action='open']")).toHaveCount(0);           // no jump from inside the panel
      expect(new URL(page.url()).pathname).toBe("/terminal");
      if (process.env.INPLACE_SHOTS) await page.screenshot({ path: `${process.env.INPLACE_SHOTS}/nav3-terminal-chart-${size.width}.png` });
      if (size.phone) await page.goBack(); else await page.keyboard.press("Escape");
      await expect(chart).toHaveCount(0);
      expect(new URL(page.url()).pathname).toBe("/terminal");
      expect(new URL(page.url()).searchParams.getAll("panel")).toEqual([]);
      if (before) await expect(page.locator("[data-page='terminal-v2']")).toHaveAttribute("data-layout", before);
      await expect(host).toBeVisible();
    });

    test("the previous Terminal's chain, Connect your broker (the chain could not be read): the broker drawer over it", async ({ page }) => {
      await open(page, "light", { path: "/terminal?view=classic", routes: async (p) => {
        await p.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/live/, (r) => r.fulfill({ status: 503, json: { detail: "No broker session" } }));
      } });
      if (size.phone) await page.locator("[data-tab='chain']").tap();
      const link = page.locator("[data-empty='chain-error'] a", { hasText: "Connect your broker" }).first();
      await expect(link).toBeVisible({ timeout: 15_000 });
      await link.click();
      const broker = page.locator("[data-inplace-panel='broker']");
      await expect(broker).toBeVisible();
      await expect(broker).toHaveAttribute("data-inplace-shape", size.shape);
      expect(new URL(page.url()).pathname).toBe("/terminal");
      if (size.phone) await expectPhoneLayout(page);
      if (process.env.INPLACE_SHOTS) await page.screenshot({ path: `${process.env.INPLACE_SHOTS}/nav3-terminal-broker-${size.width}.png` });
      await page.keyboard.press("Escape");
      await expect(broker).toHaveCount(0);
      expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe("/terminal?view=classic");
      await expect(link).toBeVisible();
    });
  });
}

// UX-3 + TA-4 (UX2 plan PR 8): with `watchlists` on, the Charts watchlist panel is docked left in the Option seller layout;
// a row with options becomes the one active symbol (chart, chain, basket follow); any other row opens on Charts in place.
const WL_ON = { ...ON, watchlists: true };
const MY_LIST = [{ id: 7, name: "My list", version: 1, items: [
  { symbol: "NIFTY 50", exchange: "NSE_INDEX" }, { symbol: "NIFTY BANK", exchange: "NSE_INDEX" }, { symbol: "NIFTY IT", exchange: "NSE_INDEX" },
] }];
const listRoutes = async (page: Page) => {
  await page.route(/\/api(?:\/v1)?\/watchlists$/, (r) => (r.request().method() === "GET" ? r.fulfill({ json: MY_LIST }) : r.fallback()));
};
const wlRow = (page: Page, scope: string, symbol: string) =>
  page.locator(`${scope} [data-watchlist] [data-row]`).filter({ hasText: symbol }).locator("button:not([data-handle]):not([aria-label^='Remove'])").first();

test.describe("v2 terminal 1440: the watchlist panel (UX-3 + TA-4)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const theme of THEMES) {
    test(`docked left: Watchlist · Chart · Chain, Positions & orders with the basket beside them (${theme})`, async ({ page }) => {
      await open(page, theme, { features: WL_ON, routes: listRoutes });
      const ids = ["watchlist", "chart-1", "chain", "positions", "basket"];
      await expect(page.locator("[data-terminal-grid] [data-panel]")).toHaveCount(ids.length);
      const boxes = Object.fromEntries(await Promise.all(ids.map(async (id) => [id, (await page.locator(`[data-panel='${id}']`).boundingBox())!] as const)));
      expect(boxes.watchlist.x).toBeLessThan(boxes["chart-1"].x);
      expect(boxes["chart-1"].x).toBeLessThan(boxes.chain.x);
      expect(boxes.watchlist.width).toBeGreaterThan(250);                              // 3 of 12 columns: symbol, price and change readable
      expect(boxes.positions.y).toBeGreaterThan(boxes.chain.y + boxes.chain.height - 1);
      expect(boxes.basket.x).toBeGreaterThan(boxes.positions.x + boxes.positions.width - 1);
      const chain = page.locator("[data-panel='chain']");
      // Owner 13 Nov: the chart is the widest (Watchlist 3 · Chart 5 · Chain 4); at 4 columns IV goes first, OI change stays.
      expect(boxes["chart-1"].width).toBeGreaterThan(boxes.chain.width);
      expect(boxes.chain.width).toBeGreaterThan(boxes.watchlist.width);
      await expect(chain.locator("[data-col='call-oi_change']").first()).toBeVisible();
      for (const col of ["call-iv", "put-iv"]) await expect(chain.locator(`[data-col='${col}']`)).toHaveCount(0);
      await expect(page.locator("[data-panel='watchlist'] [data-row]")).toHaveCount(3);
      // OWNER_RULES D.3: no cut-off text in the narrow panel - each name whole on its own line, the list chooser in full.
      const names = page.locator("[data-panel='watchlist'] [data-row] [data-row-name]");
      await expect(names).toHaveText(["NIFTY 50", "NIFTY BANK", "NIFTY IT"]);
      for (const el of await names.all()) {
        const m = await el.evaluate((n) => ({ sw: n.scrollWidth, cw: n.clientWidth }));
        expect(m.sw).toBeLessThanOrEqual(m.cw);
      }
      await expect(page.locator("[data-panel='watchlist'] [data-row] [data-price-line]")).toHaveCount(3);
      const picker = page.locator("[data-panel='watchlist'] #watchlist-pick");
      await expect(picker.locator("option:checked")).toHaveText("My list (3)");
      const fits = await picker.evaluate((sel) => {
        const s = sel as HTMLSelectElement;
        const c = document.createElement("canvas").getContext("2d")!;
        const st = getComputedStyle(s);
        c.font = `${st.fontWeight} ${st.fontSize} ${st.fontFamily}`;
        const text = c.measureText(s.options[s.selectedIndex].text).width;
        const room = s.clientWidth - parseFloat(st.paddingLeft) - parseFloat(st.paddingRight) - 24;   // 24: the arrow
        return text <= room;
      });
      expect(fits).toBe(true);
      await noSidewaysScroll(page);
      await shot(page, `ux2-pr8-1440-${theme}`);
    });
  }

  test("a row with options switches the Terminal's symbol; a row without opens on Charts in place; close and reopen the panel", async ({ page }) => {
    await open(page, "light", { features: WL_ON, routes: listRoutes });
    const bar = page.locator("[data-block='terminal-context']");
    await expect(bar).toHaveAttribute("data-symbol", "NIFTY");
    await wlRow(page, "[data-panel='watchlist']", "NIFTY BANK").click();
    await expect(bar).toHaveAttribute("data-symbol", "BANKNIFTY");
    await expect(page.locator("[data-panel='chart-1']")).toContainText("NIFTY BANK");
    await expect(wlRow(page, "[data-panel='watchlist']", "NIFTY BANK")).toHaveAttribute("aria-current", "true");
    await wlRow(page, "[data-panel='watchlist']", "NIFTY IT").click();
    await expect(page.locator("[data-inplace-panel='chart']")).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/terminal");
    await expect(bar).toHaveAttribute("data-symbol", "BANKNIFTY");                     // no options: the Terminal stays
    await page.keyboard.press("Escape");
    await expect(page.locator("[data-inplace-panel='chart']")).toHaveCount(0);
    await page.locator("[data-panel-close='watchlist']").click();
    await expect(page.locator("[data-panel='watchlist']")).toHaveCount(0);
    await page.locator("[data-terminal='panels'] summary").click();
    await page.locator("[data-panel-toggle='watchlist']").check();
    await expect(page.locator("[data-panel='watchlist']")).toBeVisible();
  });

  test("with `watchlists` off: no watchlist panel, no menu entry, the chart takes its columns", async ({ page }) => {
    await open(page, "light");
    await expect(page.locator("[data-panel='watchlist']")).toHaveCount(0);
    await page.locator("[data-terminal='panels'] summary").click();
    await expect(page.locator("[data-panel-toggle='watchlist']")).toHaveCount(0);
    const chart = (await page.locator("[data-panel='chart-1']").boundingBox())!;
    const grid = (await page.locator("[data-terminal-grid]").boundingBox())!;
    expect(Math.abs(chart.x - grid.x)).toBeLessThan(2);
  });
});

test.describe("v2 terminal 390: the Watchlist tab (UX-3)", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  for (const theme of THEMES) {
    test(`the Watchlist tab first; a row opens the Chart tab on its symbol (${theme})`, async ({ page }) => {
      await open(page, theme, { features: WL_ON, routes: listRoutes });
      await expect(page.locator("[data-terminal-tabs] [role='tab']")).toHaveText([/^Watchlist$/, /^Chart$/, /^Chain$/, /^Basket$/, /^Positions\s*2$/, /^Orders$/, /^Payoff$/, /^Bots$/]);
      await expect(page.locator("[data-tab='chart']")).toHaveAttribute("aria-selected", "true");
      await page.locator("[data-tab='watchlist']").tap();
      await expect(page.locator("[data-phone-panel='watchlist'] [data-row]")).toHaveCount(3);
      await expectPhoneLayout(page);
      await noSidewaysScroll(page);
      await shot(page, `ux2-pr8-390-${theme}`);
      await wlRow(page, "[data-phone-panel='watchlist']", "NIFTY BANK").tap();
      await expect(page.locator("[data-tab='chart']")).toHaveAttribute("aria-selected", "true");
      await expect(page.locator("[data-block='terminal-context']")).toHaveAttribute("data-symbol", "BANKNIFTY");
    });
  }

  // Owner fix (the chat bubble over the list's price column): a long list, the Copilot's bubble on. While the list
  // scrolls the bubble steps aside and it comes back shortly after; the list's last row scrolls clear of it even with
  // the list's bottom edge level with the bubble's; at the end of the page nothing is under it (the shared check).
  test("a long list with the chat bubble: it steps aside while the list scrolls; the last row scrolls clear of it (dark)", async ({ page }) => {
    const items = Array.from({ length: 40 }, (_, i) => ({ symbol: `STOCK${String(i + 1).padStart(2, "0")}`, exchange: "NSE_EQ" }));
    await open(page, "dark", { features: { ...WL_ON, ai_copilot: true }, routes: async (p) => {
      await p.route(/\/api(?:\/v1)?\/watchlists$/, (r) => (r.request().method() === "GET" ? r.fulfill({ json: [{ ...MY_LIST[0], items }] }) : r.fallback()));
    } });
    await page.locator("[data-tab='watchlist']").tap();
    const rows = page.locator("[data-phone-panel='watchlist'] [data-row]");
    await expect(rows).toHaveCount(40);
    const bubble = page.locator("[data-chat-bubble]");
    await expect(bubble).toBeVisible();
    const resting = () => bubble.evaluate((b) => !b.hasAttribute("data-scrolling") && getComputedStyle(b).opacity === "1");
    await expect.poll(resting).toBe(true);
    const list = page.locator("[data-phone-panel='watchlist'] ul[aria-label^='Symbols in']");
    await list.evaluate((e) => { e.scrollTop = e.scrollHeight / 2; });
    await expect(bubble).toHaveAttribute("data-scrolling", "");
    await expect(bubble).toHaveAttribute("aria-label", /^Chat with the Copilot/);          // still named and in the tab order
    await expect(bubble).not.toHaveAttribute("tabindex", "-1");
    await expect.poll(resting).toBe(true);
    // the list's bottom edge level with the bubble's bottom edge (the worst place), the list at its end; room above and below
    // the page so the window can always scroll there (owner 14 Nov: with the compact top the list ends above that place at
    // the page's top, and the window cannot scroll up past it)
    await page.evaluate(() => { document.body.style.paddingTop = "50vh"; document.body.style.paddingBottom = "50vh"; });
    const gap = await page.evaluate(() => document.querySelector("[data-phone-panel='watchlist'] ul[aria-label^='Symbols in']")!.getBoundingClientRect().bottom
      - document.querySelector("[data-chat-bubble]")!.getBoundingClientRect().bottom);
    await page.evaluate((dy) => window.scrollBy(0, dy), gap);
    await list.evaluate((e) => { e.scrollTop = e.scrollHeight; });
    await expect.poll(resting).toBe(true);
    const [last, box, room] = await page.evaluate(() => {
      const rect = (e: Element | null) => { const b = e!.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, left: b.left, right: b.right }; };
      const ul = document.querySelector("[data-phone-panel='watchlist'] ul[aria-label^='Symbols in']")!;
      return [rect([...ul.querySelectorAll("[data-row]")].pop() ?? null), rect(document.querySelector("[data-chat-bubble]")), rect(ul)];
    });
    expect(Math.abs(room.bottom - box.bottom)).toBeLessThan(2);
    expect(last.bottom, "the last row sits above the chat bubble").toBeLessThanOrEqual(box.top);
    await expectPhoneLayout(page);
  });
});

// Owner bug 13 Nov: "chart and the other panels do not become small or big by dragging". The corner pushed nothing (a
// neighbour in the way refused the whole step); a line drag snapped with no feedback until half a column. Now: a guide
// follows the pointer on a line, the corner moves the shared line, and the sizes stay after "Save as mine" and a reload.
// TERMINAL_SHOTS=<dir>: the owner's proof (1440 dark, two sizes; 390).
test.describe("v2 terminal 1440: panels resize by dragging (owner bug 13 Nov)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  const box = async (page: Page, id: string) => (await page.locator(`[data-panel='${id}']`).boundingBox())!;
  const view = async (page: Page, name: string) => {
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, animations: "disabled" });
  };

  test("a line drag: the guide follows the pointer, both neighbours resize, the sizes stay after Save as mine and a reload", async ({ page }) => {
    const seen = await open(page, "dark");
    await settle(page);
    await view(page, "resize-1440-dark-default");
    const chart0 = await box(page, "chart-1"), chain0 = await box(page, "chain"), pos0 = await box(page, "positions");
    const col = (chain0.x + chain0.width - chart0.x + 8) / 12;
    const v = page.locator("[data-gutter-ids='chart-1|chain']");
    await expect(v).toHaveAttribute("role", "separator");
    await expect(v).toHaveAttribute("aria-valuetext", "Chart 8 columns wide, Option chain 4 columns wide.");
    // The grab area is 16 px: 4 px into the chart's own edge still takes the line. Owner 13 Nov: the chart starts widest,
    // so the drag goes left (the chain wider).
    const g = (await v.boundingBox())!;
    await page.mouse.move(g.x - 3, g.y + g.height / 2);
    await expect(v).toHaveCSS("cursor", "col-resize");
    await page.mouse.down();
    await page.mouse.move(g.x - 3 - 20, g.y + g.height / 2, { steps: 4 });                // under half a column: the guide moves at once
    const guide = page.locator("[data-terminal-guide='x']");
    await expect(guide).toBeVisible();
    expect((await guide.boundingBox())!.x).toBeLessThan(g.x - 10);
    await page.mouse.move(g.x - 3 - col, g.y + g.height / 2, { steps: 8 });
    await page.mouse.up();
    await expect(guide).toHaveCount(0);
    const chart1 = await box(page, "chart-1"), chain1 = await box(page, "chain");
    expect(chart1.width).toBeLessThan(chart0.width - col / 2);
    expect(chain1.width).toBeGreaterThan(chain0.width + col / 2);
    await expect(v).toHaveAttribute("aria-valuetext", "Chart 7 columns wide, Option chain 5 columns wide.");
    // The chart re-fits: its canvas follows the panel's new width.
    await expect.poll(async () => page.evaluate(() => Math.max(...Array.from(document.querySelectorAll("[data-panel='chart-1'] canvas")).map((c) => c.getBoundingClientRect().width))))
      .toBeLessThan(chart0.width - col / 2);
    await view(page, "resize-1440-dark-size-a");
    // The line under the top row: Positions & orders and the Basket get taller (keyboard too: the arrow keys move it).
    const h = page.locator("[data-gutter-ids='chart-1|chain|positions|basket']");
    await h.focus();
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("ArrowUp");
    await expect.poll(async () => (await box(page, "positions")).y).toBeLessThan(pos0.y - 60);
    await expect(h).toHaveAttribute("aria-valuenow", "10");
    // Save as mine, reload: the sizes come back.
    await page.locator("[data-terminal='save']").click();
    await page.locator("[data-terminal='name']").fill("Big chart");
    await page.locator("[data-terminal='ask-ok']").click();
    await expect(page.locator("[data-note='terminal-layout'], [data-testid='terminal-layout']").first()).toContainText("Saved as");
    const saved = seen.puts.at(-1)?.body as { layouts: Record<string, { panels: { id: string; w: number; h: number }[] }>; active: string };
    expect(saved.layouts[saved.active].panels.find((q) => q.id === "chart-1")).toMatchObject({ w: 7, h: 10 });
    const before = { chart: await box(page, "chart-1"), positions: await box(page, "positions") };
    await page.reload();
    await settle(page);
    await expect.poll(async () => Math.round((await box(page, "chart-1")).width)).toBe(Math.round(before.chart.width));
    // Relative to the chart (the "Saved as" note above the grid is gone after the reload).
    expect(Math.round((await box(page, "positions")).y - (await box(page, "chart-1")).y)).toBe(Math.round(before.positions.y - before.chart.y));
    await view(page, "resize-1440-dark-size-a-after-reload");
    // Reset: the ready sizes again.
    await page.locator("[data-terminal='reset']").click();
    await expect.poll(async () => Math.round((await box(page, "chart-1")).width)).toBe(Math.round(chart0.width));
    expect(Math.round((await box(page, "positions")).y - (await box(page, "chart-1")).y)).toBe(Math.round(pos0.y - chart0.y));
  });

  test("the corner moves the shared lines: the chart narrower and taller, its neighbours take and give the space", async ({ page }) => {
    await open(page, "dark");
    await settle(page);
    const chart0 = await box(page, "chart-1"), chain0 = await box(page, "chain"), pos0 = await box(page, "positions");
    const col = (chain0.x + chain0.width - chart0.x + 8) / 12;
    const c = (await page.locator("[data-panel-resize='chart-1']").boundingBox())!;
    await page.mouse.move(c.x + 10, c.y + 10);
    await page.mouse.down();
    await page.mouse.move(c.x + 10 - col * 2, c.y + 10 + 48 * 2, { steps: 10 });
    await expect(page.locator("[data-terminal-limit]")).toHaveCount(0);                  // before the fix: "Another panel is in the way."
    await page.mouse.up();
    const chart1 = await box(page, "chart-1"), chain1 = await box(page, "chain"), pos1 = await box(page, "positions");
    expect(chart1.width).toBeLessThan(chart0.width - col * 1.5);
    expect(chain1.width).toBeGreaterThan(chain0.width + col * 1.5);
    expect(chart1.height).toBeGreaterThan(chart0.height + 80);
    expect(pos1.y).toBeGreaterThan(pos0.y + 80);
    await view(page, "resize-1440-dark-size-b");
    // The minimum holds and says why (the taller chart's corner can sit below the window: bring it into view first).
    await page.locator("[data-panel-resize='chart-1']").scrollIntoViewIfNeeded();
    const c2 = (await page.locator("[data-panel-resize='chart-1']").boundingBox())!;
    await page.mouse.move(c2.x + 10, c2.y + 10);
    await page.mouse.down();
    await page.mouse.move(c2.x + 10 + col * 5, c2.y + 10, { steps: 10 });          // the chain (6 wide) stops at 3
    await expect(page.locator("[data-terminal-limit]")).toContainText("Option chain cannot be narrower than 3 columns.");
    await page.mouse.up();
  });
});

test.describe("v2 terminal 390: panels stay stacked (owner bug 13 Nov)", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

  test("no lines and no corners on a phone; the tabs and the phone layout rules hold", async ({ page }) => {
    await open(page, "dark");
    await settle(page);
    await expect(page.locator("[data-terminal-grid]")).toHaveCount(0);
    await expect(page.locator("[data-gutter-ids], [data-panel-resize]")).toHaveCount(0);
    await expect(page.locator("[data-terminal-tabs]")).toBeVisible();
    await expectPhoneLayout(page);
    await shot(page, "resize-390-dark");
  });
});

// Owner 13 Nov ("make the default panel setting like this; in the OI table no 'L' - say it once above: OI in lakhs; the
// entries should display properly"): the Net Diff table in the Terminal's chain panel at the default layout (1440), a
// narrow panel (1024) and the phone (390). Every cell on one line, no text wider than its cell, the unit said once above,
// and a table wider than its box scrolls inside it with the Strike column sticky. Proof pictures with CHAIN_TABLE_SHOTS.
const CHAIN_SHOTS = process.env.CHAIN_TABLE_SHOTS;
/** The live chain with the server's Net Diff block (ATM ± 6 of 50), OI scaled to index-sized figures (made up). */
const netDiffRoutes = async (page: Page) => {
  await listRoutes(page);
  await page.route(/\/api(?:\/v1)?\/option-chain\/([^/]+)\/live/, (route) => {
    type Leg = { oi: number | null; oi_change: number | null; ltp: number | null; volume?: number | null };
    const scale = (l: Leg, k: number): Leg => ({ ...l, oi: (l.oi ?? 0) * 60 + k * 1_234, oi_change: (l.oi_change ?? 0) * 120 - k * 4_321, volume: (l.volume ?? 0) * 40 });
    const rows = (DATA.live.rows as { strike: number; call: Leg; put: Leg }[]).map((r, i) => ({ ...r, call: scale(r.call, i), put: scale(r.put, 21 - i) }));
    const summary = DATA.live.summary as Record<string, unknown>;
    const atm = Math.round(Number(summary.spot) / 50) * 50;
    const shown = rows.filter((r) => r.strike >= atm - 300 && r.strike <= atm + 300).map((r) => {
      const ce = r.call.oi_change ?? 0;
      const pe = r.put.oi_change ?? 0;
      return { strike: r.strike, ce_chg: ce, ce_oi: r.call.oi ?? 0, ce_ltp: r.call.ltp, pe_ltp: r.put.ltp, pe_oi: r.put.oi ?? 0, pe_chg: pe,
        diff: pe - ce, dominance: pe - ce > 0 ? "Bulls" : pe - ce < 0 ? "Bears" : "Neutral" };
    });
    return route.fulfill({ json: { ...DATA.live, rows, market_open: true, summary: { ...summary, pcr_oi_atm: 0.91 },
      net_diff: { status: "ready", since: "2026-10-27T04:02:00+00:00", step: 50, atm, window: 6, pcr: 0.91, rows: shown } } });
  });
};

/** Each visible cell of the table: every text line inside its cell, on one line (a header: one line per part). */
async function tableProblems(page: Page, scope: string) {
  return page.locator(`${scope} [data-block='oi-diff'] table`).evaluate((table) => {
    const problems: string[] = [];
    for (const cell of Array.from(table.querySelectorAll("td, th"))) {
      const c = cell.getBoundingClientRect();
      if (c.width === 0 || c.height === 0) continue;                               // a column the phone leaves out
      const tops = new Set<number>();
      const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        if (!(n.textContent ?? "").trim() || n.parentElement?.closest(".sr-only")) continue;
        const range = document.createRange();
        range.selectNodeContents(n);
        const rects = Array.from(range.getClientRects()).filter((r) => r.width > 0);
        if (rects.length > 1) problems.push(`wraps: ${n.textContent}`);
        for (const r of rects) {
          if (r.left < c.left - 0.5 || r.right > c.right + 0.5) problems.push(`wider than its cell: ${n.textContent}`);
          tops.add(Math.round(r.top));
        }
      }
      if (cell.tagName === "TD" && tops.size > 1) problems.push(`two lines: ${cell.textContent}`);
    }
    return problems;
  });
}

async function scrollsWithStickyStrike(page: Page, scope: string) {
  const box = page.locator(`${scope} [data-oi-diff-scroll]`);
  const m = await box.evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth, ox: getComputedStyle(el).overflowX }));
  if (m.sw <= m.cw + 1) return false;                                                // it fits: nothing to scroll
  expect(["auto", "scroll"]).toContain(m.ox);
  await box.evaluate((el) => { el.scrollLeft = el.scrollWidth; });
  const b = (await box.boundingBox())!;
  for (const sel of ["thead th:has-text('Strike')", "[data-oi-diff-atm] th", "[data-oi-diff-total] th"]) {
    const s = (await box.locator(sel).first().boundingBox())!;
    expect(s.x, sel).toBeGreaterThanOrEqual(b.x - 1);                                  // the Strike column stays in sight
    expect(s.x + s.width, sel).toBeLessThanOrEqual(b.x + b.width + 1);
  }
  await box.evaluate((el) => { el.scrollLeft = 0; });
  return true;
}

async function noLakhSuffix(page: Page, scope: string) {
  const cells = await page.locator(`${scope} [data-block='oi-diff'] td, ${scope} [data-chain-scroll] td`).allInnerTexts();
  expect(cells.filter((t) => /\d\s*L\b/.test(t))).toEqual([]);
}

for (const size of [{ width: 1440, height: 900 }, { width: 1024, height: 900 }]) {
  test.describe(`v2 terminal ${size.width}: the default layout and the Net Diff table (owner 13 Nov)`, () => {
    test.use({ viewport: size });
    for (const theme of THEMES) {
      test(`Watchlist · Chart (widest) · Chain over Positions & orders · Basket; the OI table clean (${theme})`, async ({ page }) => {
        await open(page, theme, { features: WL_ON, routes: netDiffRoutes });
        const ids = ["watchlist", "chart-1", "chain", "positions", "basket"] as const;
        const b = Object.fromEntries(await Promise.all(ids.map(async (id) => [id, (await page.locator(`[data-panel='${id}']`).boundingBox())!] as const)));
        expect(b["chart-1"].width).toBeGreaterThan(b.chain.width);
        expect(b.chain.width).toBeGreaterThan(b.watchlist.width);
        expect(b.positions.width).toBeGreaterThan(b.basket.width * 2);
        const scope = "[data-panel='chain']";
        const diff = page.locator(`${scope} [data-block='oi-diff']`);
        await expect(diff.locator("[data-oi-diff-row]")).toHaveCount(7);   // ATM ± 6 steps of 50; the fixture lists every 100
        await expect(diff.locator("h2 [data-oi-unit]")).toHaveText("OI in lakhs");
        await expect(page.locator(`${scope} [data-chain-scroll]`).locator("xpath=../preceding-sibling::p[@data-oi-unit]")).toHaveText("(OI and volume in lakhs)");
        await noLakhSuffix(page, scope);
        await expect(diff.locator("[data-dominance]").first()).toHaveText(/^(Bulls|Bears|Neutral)$/);
        // all nine of Trade's columns in a desktop panel
        await expect(diff.locator("thead th")).toHaveCount(9);
        expect(await tableProblems(page, scope)).toEqual([]);
        const scrolls = await scrollsWithStickyStrike(page, scope);
        if (size.width === 1024) expect(scrolls).toBe(true);                              // the narrow panel: it scrolls, never clips
        await noSidewaysScroll(page);
        if (CHAIN_SHOTS) {
          await page.screenshot({ path: `${CHAIN_SHOTS}/terminal-${size.width}-${theme}.png`, animations: "disabled" });
          await diff.scrollIntoViewIfNeeded();
          await page.locator(scope).screenshot({ path: `${CHAIN_SHOTS}/chain-panel-${size.width}-${theme}.png`, animations: "disabled" });
        }
      });
    }

    test("the Contracts choice: the unit line says contracts, the cells stay bare", async ({ page }) => {
      test.skip(size.width !== 1440, "once");
      await page.addInitScript(() => { try { localStorage.setItem("atp_option_chain_columns", JSON.stringify({ units: "contracts" })); } catch { /* none */ } });
      await open(page, "light", { features: WL_ON, routes: netDiffRoutes });
      const scope = "[data-panel='chain']";
      await expect(page.locator(`${scope} [data-block='oi-diff'] h2 [data-oi-unit]`)).toHaveText("OI in contracts");
      await noLakhSuffix(page, scope);
      expect(await tableProblems(page, scope)).toEqual([]);
    });
  });
}

test.describe("v2 terminal 390: the Net Diff table on the Chain tab (owner 13 Nov)", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  for (const theme of THEMES) {
    test(`readable: one line per cell, the unit once above, sideways scroll inside the table with Strike sticky (${theme})`, async ({ page }) => {
      await open(page, theme, { features: WL_ON, routes: netDiffRoutes });
      await page.locator("[data-tab='chain']").tap();
      const scope = "[data-phone-panel='chain']";
      const diff = page.locator(`${scope} [data-block='oi-diff']`);
      await expect(diff.locator("[data-oi-diff-row]")).toHaveCount(7);   // ATM ± 6 steps of 50; the fixture lists every 100
      await expect(diff.locator("h2 [data-oi-unit]")).toHaveText("OI in lakhs");
      await expect(diff.locator("[data-dominance]").first()).toBeVisible();
      await noLakhSuffix(page, scope);
      expect(await tableProblems(page, scope)).toEqual([]);
      await scrollsWithStickyStrike(page, scope);
      await expectPhoneLayout(page);
      await noSidewaysScroll(page);
      if (CHAIN_SHOTS) {
        await diff.scrollIntoViewIfNeeded();
        await diff.screenshot({ path: `${CHAIN_SHOTS}/oi-table-390-${theme}.png`, animations: "disabled" });
      }
    });
  }
});

// Owner 14 Nov ("space waste hot aahe, important panel khali dablet"): the Terminal's top is ONE compact strip - the symbol,
// expiry, figures, the layout controls and PAPER - so the panels start as high as they can (before: 138 px below the page
// top at 1440, 198 px at 390). TOPBAR_SHOTS=<dir>: the proof screenshots.
test.describe("v2 terminal top: one compact strip (owner 14 Nov)", () => {
  const TOP_SHOTS = process.env.TOPBAR_SHOTS;
  const sizes = [
    { width: 1440, height: 900, phone: false, most: 100 }, { width: 1024, height: 768, phone: false, most: 140 },
    { width: 390, height: 844, phone: true, most: 175 }, { width: 360, height: 780, phone: true, most: 200 },
  ] as const;
  for (const s of sizes) {
    test.describe(`${s.width}`, () => {
      test.use({ viewport: { width: s.width, height: s.height }, ...(s.phone ? { isMobile: true, hasTouch: true, deviceScaleFactor: 1 } : {}) });
      test(`the panels start right under the strip (${s.width})`, async ({ page }) => {
        await open(page, "dark");
        const bar = page.locator("[data-block='terminal-context']");
        const b = (await bar.boundingBox())!;
        const top = (await page.locator(s.phone ? "[data-terminal-tabs]" : "[data-terminal-grid]").boundingBox())!.y;
        const main = (await page.locator("[data-page='terminal-v2']").boundingBox())!.y;
        console.log(`TOPBAR ${s.width}: bar ${Math.round(b.height)} px, panels at ${Math.round(top - main)} px below the page top`);
        if (TOP_SHOTS) await page.screenshot({ path: `${TOP_SHOTS}/terminal-top-${s.width}-dark.png`, animations: "disabled" });
        expect(top - main).toBeLessThanOrEqual(s.most);
        // No stacked "Symbol" / "Expiry" labels: the pickers name themselves.
        await expect(bar.locator("label[for]")).toHaveCount(0);
        await expect(page.locator("[data-terminal='symbol']")).toHaveAttribute("aria-label", "Symbol");
        await expect(bar.locator("[data-terminal='mode']")).toHaveText("PAPER");
        if (!s.phone) {
          // The layout controls live in the bar (no Layout row of their own), each icon named by its tooltip and aria-label.
          await expect(bar.locator("[data-block='terminal-toolbar']")).toHaveCount(1);
          await expect(bar.locator("[data-terminal='save']")).toHaveAttribute("aria-label", "Save as mine");
          await expect(bar.locator("[data-terminal='panels'] summary")).toHaveAttribute("title", "Panels");
          await expect(bar.locator("[data-terminal='count']")).toHaveText("0 of 10 saved");
          if (s.width === 1440) {
            // One row at 1440: the layout picker and PAPER sit level with the symbol picker.
            const mid = async (sel: string) => { const r = (await page.locator(sel).boundingBox())!; return r.y + r.height / 2; };
            const row = await mid("[data-terminal='symbol']");
            for (const sel of ["[data-terminal='layout']", "[data-terminal='mode']"]) expect(Math.abs((await mid(sel)) - row)).toBeLessThan(4);
          }
        } else {
          for (const sel of ["[data-terminal='symbol']", "[data-terminal='expiry']"]) expect((await page.locator(sel).boundingBox())!.height).toBeGreaterThanOrEqual(44);
        }
        await noSidewaysScroll(page);
      });
    });
  }
});
