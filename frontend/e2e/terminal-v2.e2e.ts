import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
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
  let stored: Record<string, unknown> = { layouts: {}, active: null };
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
    return route.fulfill({ json: { ...DATA.live, market_open: true } });
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
  await page.goto("/terminal");
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
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true, animations: "disabled" });
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
    for (const col of ["call-oi", "call-oi_change", "call-iv", "call-ltp", "put-oi", "put-iv"]) await expect(chain.locator(`[data-col='${col}']`).first()).toBeVisible();
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
        await expect(page.locator("[data-terminal-tabs] [role='tab']")).toHaveText([/^Chart$/, /^Chain$/, /^Basket$/, /^Positions\s*2$/, /^Orders$/]);
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

    test("no sideways scroll and 44 px targets on every tab; a chain price opens the one ticket as a sheet", async ({ page }) => {
      await open(page, "light");
      for (const tab of ["chart", "chain", "basket", "positions", "orders"]) {     // TERM-2 step 3 tabs
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
      expect(await textBudgetProblems(page)).toEqual([]);
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
      const col = (chain0.x + chain0.width - chart0.x + 8) / 9;                   // 4 + 5 columns
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
      // The horizontal line under the top row: the top panels and Positions & orders share it.
      await expect(line(page, "chart-1|chain|basket|positions")).toHaveCSS("cursor", "row-resize");
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
      expect((await box(page, "chain")).width).toBeGreaterThan(chart0.width * 2.5);
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
      await expect(width).toHaveText("4");
      await expect(page.locator("[data-size-min='chart-1:w']")).toContainText("min 3");
      await page.locator("[data-size-step='chart-1:w:+1']").click();
      await expect(width).toHaveText("5");
      await expect(page.locator("[data-size='chain:w']")).toHaveText("4");             // the neighbour gave a column
      await shotView(page, `m58-${size.width}-dialog`);
      await page.locator("[data-size-step='chain:w:-1']").click();                       // 3: its smallest
      await expect(page.locator("[data-size-step='chain:w:-1']")).toBeDisabled();
      await page.locator("[data-terminal='equal-widths']").click();
      for (const id of ["chart-1", "chain", "basket"]) await expect(page.locator(`[data-size='${id}:w']`)).toHaveText("4");
      await page.locator("[data-terminal='fit']").click();
      await expect(page.locator("[data-size='positions:w']")).toHaveText("12");
      await page.locator("[data-size-step='chart-1:h:+1']").click();
      await page.locator("[data-size-step='chart-1:w:+1']").click();
      await expect(width).toHaveText("5");
      const height = await page.locator("[data-size='chart-1:h']").textContent();
      await page.locator("[data-terminal='panels'] summary").click();
      await page.locator("[data-terminal='save']").click();
      await page.locator("[data-terminal='name']").fill("Wide chart");
      await page.locator("[data-terminal='ask-ok']").click();
      await expect(page.locator("[data-note='terminal-layout'], [data-testid='terminal-layout']").first()).toContainText("Saved as");
      const saved = seen.puts.at(-1)?.body as { layouts: Record<string, { panels: { id: string; w: number }[] }>; active: string };
      expect(saved.layouts[saved.active].panels.find((p) => p.id === "chart-1")?.w).toBe(5);
      await page.reload();
      await settle(page);
      await page.locator("[data-terminal='panels'] summary").click();
      await expect(page.locator("[data-size='chart-1:w']")).toHaveText("5");
      await expect(page.locator("[data-size='chart-1:h']")).toHaveText(height ?? "");
    });
  });
}
